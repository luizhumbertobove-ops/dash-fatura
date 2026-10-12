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

# Lê a fatura e grava data/faturas/AAAA-MM.json (mês do vencimento)
python bot/fatura_bot.py ~/Downloads/Fatura_Itau.pdf

# CSV (ex.: exportação do Nubank: date,title,amount ou "Data;Descrição;Valor")
python bot/fatura_bot.py ~/Downloads/fatura.csv --mes 2026-10 --sobrescrever

# Só mostrar o JSON (para colar em "Importar (JSON)" no site)
python bot/fatura_bot.py fatura.pdf --mes 2026-10 --stdout
```

Depois é só fazer commit e push de `data/faturas/AAAA-MM.json` na `main`; o Pages republica sozinho.

### Configuração (`data/config.json`)

- `pessoas`: quem participa do rateio.
- `padrao`: quem fica selecionado quando nenhuma regra se aplica e no "Novo Item" (hoje só o Luiz).
- `ignorar`: trechos de descrição que não entram (pagamentos da fatura anterior, saldo etc.).
- `regras`: quem divide cada tipo de gasto. A primeira regra cujo `contem` aparecer na
  descrição define os pagantes; sem regra, o item fica com quem está em `padrao`.

Cada regra pode usar uma ou mais condições (todas precisam bater):

```json
{ "contem": "uber", "pagantes": ["Leo"] }
{ "cartao": "8082", "pagantes": ["Luiz"] }
{ "categoria": "veículos", "pagantes": ["Luiz", "Beto"] }
```

- `contem`: trecho da descrição (sem diferenciar maiúsculas/acentos);
- `cartao`: 4 últimos dígitos do cartão (a fatura Itaú separa por cartão adicional);
- `categoria`: categoria impressa pelo banco abaixo da compra (ALIMENTAÇÃO, VEÍCULOS...).

### Fatura Itaú (PDF)

O PDF do Itaú tem os lançamentos em duas colunas; o bot lê cada coluna separadamente,
guarda o cartão (final XXXX), a categoria e a parcela (ex.: `ToyomotorsService (01/05)`),
ignora a seção "Compras parceladas - próximas faturas" e confere a soma com o
"Total dos lançamentos atuais". O mês é detectado pelo vencimento (`--mes` é opcional).

## Deploy (GitHub Pages)

1. Em **Settings → Pages**, em *Build and deployment*, escolha **Source: GitHub Actions**.
2. Todo push na `main` dispara `.github/workflows/pages.yml`, que publica `index.html` e `data/`.

> ⚠️ Em conta gratuita o GitHub Pages exige repositório público, e o site publicado é público
> mesmo com repo privado (exceto no plano Enterprise). Os JSONs em `data/faturas/` ficam visíveis
> para quem tiver o link. Nunca commite o PDF/CSV original da fatura (já está no `.gitignore`).
