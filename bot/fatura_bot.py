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


RE_CARTAO = re.compile(r"\(final (\d{4})\)")
RE_PARCELA = re.compile(r"\s*(\d{2}/\d{2})$")
RE_CATEGORIA = re.compile(r"^([A-ZÀ-Ú][A-ZÀ-Ú ]+?)\s*\.")
RE_VENCIMENTO = re.compile(r"vencimento:?\s*(\d{2})/(\d{2})/(\d{4})", re.IGNORECASE)
RE_TOTAL = re.compile(r"total dos lan[cç]amentos atuais\s+([\d.]+,\d{2})", re.IGNORECASE)

# Seções que encerram/reabrem a leitura de lançamentos (texto já sem acentos)
INICIO_SECAO = ("lancamentos: compras e saques", "lancamentos: produtos e servicos",
                "lancamentos internacionais")
FIM_SECAO = ("compras parceladas - proximas faturas",)


def textos_do_pdf(pdf) -> list[str]:
    """Devolve os blocos de texto na ordem de leitura.

    A fatura do Itaú tem duas colunas de lançamentos lado a lado; lendo a
    página inteira, cada linha misturaria duas compras. Nesse caso cada
    página é lida como coluna esquerda e depois coluna direita.
    """
    paginas = [pg.extract_text() or "" for pg in pdf.pages]
    duas_colunas = any("compras e saques" in sem_acentos(t) for t in paginas)
    if not duas_colunas:
        return paginas

    blocos = []
    for pg in pdf.pages:
        meio = pg.width * 0.588
        blocos.append(pg.within_bbox((0, 0, meio, pg.height)).extract_text() or "")
        blocos.append(pg.within_bbox((meio, 0, pg.width, pg.height)).extract_text() or "")
    return blocos


def ler_pdf(caminho: Path) -> tuple[list[dict], dict]:
    try:
        import pdfplumber
    except ImportError:
        sys.exit("Instale as dependências: pip install -r bot/requirements.txt")

    itens: list[dict] = []
    info: dict = {}
    with pdfplumber.open(caminho) as pdf:
        blocos = textos_do_pdf(pdf)

    texto_completo = "\n".join(blocos)
    if m := RE_VENCIMENTO.search(texto_completo):
        info["mes"] = f"{m.group(3)}-{m.group(2)}"
    if m := RE_TOTAL.search(texto_completo):
        info["total"] = para_numero(m.group(1))

    lendo = True
    cartao = ""
    for bloco in blocos:
        for linha in bloco.splitlines():
            linha = linha.strip()
            norm = sem_acentos(linha)
            if any(t in norm for t in FIM_SECAO):
                lendo = False
                continue
            if any(t in norm for t in INICIO_SECAO):
                lendo = True
                continue
            if (m := RE_CARTAO.search(linha)) and "lancamentos no cartao" not in norm:
                cartao = m.group(1)
                continue

            m = RE_LINHA_PDF.match(linha)
            if m:
                if not lendo:
                    continue
                desc = re.sub(r"\s{2,}", " ", m.group("desc")).strip()
                parcela = RE_PARCELA.search(desc)
                if parcela:
                    desc = f"{desc[:parcela.start()].strip()} ({parcela.group(1)})"
                itens.append({
                    "data": normalizar_data(m.group("data")),
                    "desc": desc,
                    "valor": para_numero(m.group("valor")),
                    "cartao": cartao,
                    "categoria": "",
                })
            elif itens and lendo and not itens[-1]["categoria"]:
                # Linha logo abaixo da compra: "ALIMENTAÇÃO .SERRANA"
                if c := RE_CATEGORIA.match(linha):
                    itens[-1]["categoria"] = c.group(1).strip()
    return itens, info


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


def ler_csv(caminho: Path) -> tuple[list[dict], dict]:
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
            "cartao": "",
            "categoria": "",
        })
    return itens, {}


# ---------------------------------------------------------------- regras


def regra_combina(regra: dict, item: dict) -> bool:
    """Todas as condições presentes na regra precisam bater."""
    if "contem" in regra and sem_acentos(regra["contem"]) not in sem_acentos(item["desc"]):
        return False
    if "cartao" in regra and str(regra["cartao"]) != item.get("cartao", ""):
        return False
    if "categoria" in regra and sem_acentos(regra["categoria"]) != sem_acentos(item.get("categoria", "")):
        return False
    return any(k in regra for k in ("contem", "cartao", "categoria"))


def aplicar_regras(itens: list[dict], config: dict) -> list[dict]:
    pessoas = config["pessoas"]
    ignorar = [sem_acentos(t) for t in config.get("ignorar", [])]
    regras = config.get("regras", [])

    lancamentos = []
    for item in itens:
        if any(t in sem_acentos(item["desc"]) for t in ignorar) or item["valor"] == 0:
            continue

        pagantes = list(pessoas)
        for regra in regras:
            if regra_combina(regra, item):
                validos = [p for p in regra["pagantes"] if p in pessoas]
                if validos:
                    pagantes = validos
                break

        lancamento = {
            "id": len(lancamentos) + 1,
            "data": item["data"],
            "desc": item["desc"],
            "valor": round(item["valor"], 2),
            "pagantes": pagantes,
        }
        if item.get("cartao"):
            lancamento["cartao"] = item["cartao"]
        if item.get("categoria"):
            lancamento["categoria"] = item["categoria"]
        lancamentos.append(lancamento)
    return lancamentos


def moeda(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lê a fatura do cartão e gera os lançamentos do mês.")
    parser.add_argument("arquivo", type=Path, help="Fatura em PDF ou CSV")
    parser.add_argument("--mes", help="Mês da fatura no formato AAAA-MM "
                        "(padrão: mês do vencimento lido no PDF, senão o mês atual)")
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO, help="Arquivo de configuração")
    parser.add_argument("--sobrescrever", action="store_true", help="Substitui o JSON do mês se já existir")
    parser.add_argument("--stdout", action="store_true", help="Só imprime o JSON, sem gravar arquivo")
    args = parser.parse_args()

    if args.mes and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args.mes):
        sys.exit("--mes deve estar no formato AAAA-MM, ex.: 2026-10")
    if not args.arquivo.exists():
        sys.exit(f"Arquivo não encontrado: {args.arquivo}")

    config = json.loads(args.config.read_text(encoding="utf-8"))

    extensao = args.arquivo.suffix.lower()
    if extensao == ".pdf":
        itens, info = ler_pdf(args.arquivo)
    elif extensao in (".csv", ".txt"):
        itens, info = ler_csv(args.arquivo)
    else:
        sys.exit("Formato não suportado. Use PDF ou CSV.")

    mes = args.mes or info.get("mes") or date.today().strftime("%Y-%m")

    lancamentos = aplicar_regras(itens, config)
    if not lancamentos:
        sys.exit("Nenhum lançamento encontrado. Confira se o arquivo é a fatura correta.")

    # Confere com o total impresso na fatura (soma antes de aplicar "ignorar")
    lido = round(sum(i["valor"] for i in itens), 2)
    if "total" in info and abs(lido - info["total"]) > 0.005:
        print(f"ATENÇÃO: soma lida {moeda(lido)} difere do total da fatura {moeda(info['total'])}. "
              "Confira os lançamentos.", file=sys.stderr)

    saida = {"mes": mes, "lancamentos": lancamentos}
    texto = json.dumps(saida, ensure_ascii=False, indent=2) + "\n"

    if args.stdout:
        sys.stdout.write(texto)
        return

    destino = PASTA_FATURAS / f"{mes}.json"
    if destino.exists() and not args.sobrescrever:
        sys.exit(f"{destino} já existe. Use --sobrescrever para substituir.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")

    total = sum(l["valor"] for l in lancamentos)
    print(f"{len(lancamentos)} lançamentos gravados em {destino.relative_to(RAIZ)}")
    print(f"Total: {moeda(total)}" + (" (confere com a fatura)" if info.get("total") == round(total, 2) else ""))


if __name__ == "__main__":
    main()
