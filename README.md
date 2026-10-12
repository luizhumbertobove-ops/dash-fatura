# Dash Fatura

Dashboard para dividir a fatura do cartão entre **Luiz, Beto e Leo**, publicado no GitHub Pages,
e um bot em Python que lê a fatura (PDF ou CSV) e gera os lançamentos do mês.

## Como funciona

- Cada mês tem seus próprios lançamentos. Um mês sem dados começa **zerado**.
- O valor de cada item é **dividido igualmente** entre as pessoas marcadas
  (em centavos; se sobrar 1 centavo, ele vai para o primeiro da lista).
- Ao abrir um mês, o site procura nesta ordem:
  1. edições salvas no navegador (localStorage, chave `fatura_lancamentos_AAAA-MM`);
  2. o arquivo `data/faturas/AAAA-MM.json` gerado pelo bot e publicado no site;
  3. nada encontrado → mês zerado.
- Toda alteração (marcar/desmarcar pessoa, novo item, exclusão) é salva automaticamente.
  O botão **Exportar** baixa o JSON do mês, que pode ser commitado em `data/faturas/`
  para ficar disponível em qualquer dispositivo.

## Bot de leitura da fatura

```bash
pip install -r bot/requirements.txt

# Lê a fatura e grava data/faturas/2026-10.json
python bot/fatura_bot.py ~/Downloads/fatura-outubro.pdf --mes 2026-10

# CSV (ex.: exportação do Nubank: date,title,amount ou "Data;Descrição;Valor")
python bot/fatura_bot.py ~/Downloads/fatura.csv --mes 2026-10 --sobrescrever

# Só mostrar o JSON (para colar em "Importar (JSON)" no site)
python bot/fatura_bot.py fatura.pdf --mes 2026-10 --stdout
```

Depois é só fazer commit e push de `data/faturas/AAAA-MM.json` na `main`; o Pages republica sozinho.

### Configuração (`data/config.json`)

- `pessoas`: quem participa do rateio.
- `ignorar`: trechos de descrição que não entram (pagamentos da fatura anterior, saldo etc.).
- `regras`: quem divide cada tipo de gasto. A primeira regra cujo `contem` aparecer na
  descrição define os pagantes; sem regra, o item é dividido entre todos.

```json
{ "contem": "uber", "pagantes": ["Leo"] }
```

## Deploy (GitHub Pages)

1. Em **Settings → Pages**, em *Build and deployment*, escolha **Source: GitHub Actions**.
2. Todo push na `main` dispara `.github/workflows/pages.yml`, que publica `index.html` e `data/`.

> ⚠️ Em conta gratuita o GitHub Pages exige repositório público, e o site publicado é público
> mesmo com repo privado (exceto no plano Enterprise). Os JSONs em `data/faturas/` ficam visíveis
> para quem tiver o link. Nunca commite o PDF/CSV original da fatura (já está no `.gitignore`).
