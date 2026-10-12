#!/usr/bin/env python3
"""Bot que lê a fatura do cartão (PDF ou CSV) e gera os lançamentos do mês.

O resultado é gravado em data/faturas/AAAA-MM.json, que o dashboard
(index.html) carrega automaticamente ao abrir o mês correspondente.

Uso:
    python bot/fatura_bot.py fatura.pdf --mes 2026-10
    python bot/fatura_bot.py fatura.csv --mes 2026-10 --sobrescrever
    python bot/fatura_bot.py fatura.pdf --mes 2026-10 --stdout   # só imprime o JSON

Quem divide cada item é decidido pelas "regras" de data/config.json
(trecho da descrição -> lista de pagantes). Sem regra, divide entre todos.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONFIG_PADRAO = RAIZ / "data" / "config.json"
PASTA_FATURAS = RAIZ / "data" / "faturas"

MESES_ABREV = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}

# Linha típica de fatura em PDF:
#   "12/10  SUPERMERCADO EXTRA          450,00"
#   "12 OUT UBER *TRIP                  R$ 84,50"
#   "05/09  LOJA X  02/10               -1.234,56"
RE_LINHA_PDF = re.compile(
    r"^(?P<data>\d{2}/\d{2}(?:/\d{2,4})?|\d{2}\s+(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ))\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<valor>-?\s?(?:R\$\s?)?-?\d{1,3}(?:\.\d{3})*,\d{2})\s*$",
    re.IGNORECASE,
)


def sem_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    ).lower()


def para_numero(texto: str) -> float:
    """Converte '1.234,56', 'R$ -45,90', '45.90' ou '-12' em float."""
    s = re.sub(r"[^\d,.\-]", "", str(texto))
    negativo = s.count("-") % 2 == 1
    s = s.replace("-", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    valor = float(s) if s else 0.0
    return -valor if negativo else valor


def normalizar_data(texto: str) -> str:
    texto = texto.strip().upper()
    m = re.match(r"(\d{2})\s+([A-Z]{3})", texto)
    if m and m.group(2) in MESES_ABREV:
        return f"{m.group(1)}/{MESES_ABREV[m.group(2)]:02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", texto)  # formato ISO (CSV do Nubank)
    if m:
        return f"{m.group(3)}/{m.group(2)}"
    return texto[:5]


# ---------------------------------------------------------------- leitores


def ler_pdf(caminho: Path) -> list[dict]:
    try:
        import pdfplumber
    except ImportError:
        sys.exit("Instale as dependências: pip install -r bot/requirements.txt")

    itens = []
    with pdfplumber.open(caminho) as pdf:
        for pagina in pdf.pages:
            for linha in (pagina.extract_text() or "").splitlines():
                m = RE_LINHA_PDF.match(linha.strip())
                if not m:
                    continue
                itens.append({
                    "data": normalizar_data(m.group("data")),
                    "desc": re.sub(r"\s{2,}", " ", m.group("desc")).strip(),
                    "valor": para_numero(m.group("valor")),
                })
    return itens


COLUNAS_DATA = ("data", "date", "data da compra", "data lancamento")
COLUNAS_DESC = ("descricao", "description", "title", "estabelecimento", "historico", "lancamento")
COLUNAS_VALOR = ("valor", "amount", "valor (r$)", "valor em r$", "value")


def achar_coluna(cabecalho: list[str], candidatos: tuple[str, ...]) -> int | None:
    normalizado = [sem_acentos(c).strip() for c in cabecalho]
    for cand in candidatos:
        if cand in normalizado:
            return normalizado.index(cand)
    for i, nome in enumerate(normalizado):
        if any(cand in nome for cand in candidatos):
            return i
    return None


def ler_csv(caminho: Path) -> list[dict]:
    bruto = caminho.read_bytes()
    for codificacao in ("utf-8-sig", "latin-1"):
        try:
            texto = bruto.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue

    dialeto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t")
    linhas = list(csv.reader(io.StringIO(texto), dialeto))
    if not linhas:
        return []

    cabecalho = linhas[0]
    i_data = achar_coluna(cabecalho, COLUNAS_DATA)
    i_desc = achar_coluna(cabecalho, COLUNAS_DESC)
    i_valor = achar_coluna(cabecalho, COLUNAS_VALOR)
    if i_desc is None or i_valor is None:
        sys.exit(f"Não encontrei as colunas de descrição/valor no CSV. Cabeçalho: {cabecalho}")

    itens = []
    for linha in linhas[1:]:
        if len(linha) <= max(i_desc, i_valor) or not linha[i_valor].strip():
            continue
        itens.append({
            "data": normalizar_data(linha[i_data]) if i_data is not None else "",
            "desc": linha[i_desc].strip(),
            "valor": para_numero(linha[i_valor]),
        })
    return itens


# ---------------------------------------------------------------- regras


def aplicar_regras(itens: list[dict], config: dict) -> list[dict]:
    pessoas = config["pessoas"]
    ignorar = [sem_acentos(t) for t in config.get("ignorar", [])]
    regras = config.get("regras", [])

    lancamentos = []
    for item in itens:
        desc_norm = sem_acentos(item["desc"])
        if any(t in desc_norm for t in ignorar) or item["valor"] == 0:
            continue

        pagantes = list(pessoas)
        for regra in regras:
            if sem_acentos(regra["contem"]) in desc_norm:
                validos = [p for p in regra["pagantes"] if p in pessoas]
                if validos:
                    pagantes = validos
                break

        lancamentos.append({
            "id": len(lancamentos) + 1,
            "data": item["data"],
            "desc": item["desc"],
            "valor": round(item["valor"], 2),
            "pagantes": pagantes,
        })
    return lancamentos


def main() -> None:
    parser = argparse.ArgumentParser(description="Lê a fatura do cartão e gera os lançamentos do mês.")
    parser.add_argument("arquivo", type=Path, help="Fatura em PDF ou CSV")
    parser.add_argument("--mes", default=date.today().strftime("%Y-%m"),
                        help="Mês da fatura no formato AAAA-MM (padrão: mês atual)")
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO, help="Arquivo de configuração")
    parser.add_argument("--sobrescrever", action="store_true", help="Substitui o JSON do mês se já existir")
    parser.add_argument("--stdout", action="store_true", help="Só imprime o JSON, sem gravar arquivo")
    args = parser.parse_args()

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args.mes):
        sys.exit("--mes deve estar no formato AAAA-MM, ex.: 2026-10")
    if not args.arquivo.exists():
        sys.exit(f"Arquivo não encontrado: {args.arquivo}")

    config = json.loads(args.config.read_text(encoding="utf-8"))

    extensao = args.arquivo.suffix.lower()
    if extensao == ".pdf":
        itens = ler_pdf(args.arquivo)
    elif extensao in (".csv", ".txt"):
        itens = ler_csv(args.arquivo)
    else:
        sys.exit("Formato não suportado. Use PDF ou CSV.")

    lancamentos = aplicar_regras(itens, config)
    if not lancamentos:
        sys.exit("Nenhum lançamento encontrado. Confira se o arquivo é a fatura correta.")

    saida = {"mes": args.mes, "lancamentos": lancamentos}
    texto = json.dumps(saida, ensure_ascii=False, indent=2) + "\n"

    if args.stdout:
        sys.stdout.write(texto)
        return

    destino = PASTA_FATURAS / f"{args.mes}.json"
    if destino.exists() and not args.sobrescrever:
        sys.exit(f"{destino} já existe. Use --sobrescrever para substituir.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")

    total = sum(l["valor"] for l in lancamentos)
    print(f"{len(lancamentos)} lançamentos gravados em {destino.relative_to(RAIZ)}")
    print(f"Total da fatura: R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))


if __name__ == "__main__":
    main()
