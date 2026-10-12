// Leitor de fatura em PDF que roda no navegador (o arquivo não sai do computador).
// É a mesma lógica de bot/fatura_bot.py: lê a fatura do Itaú em duas colunas,
// guarda cartão, categoria e parcela, ignora "Compras parceladas - próximas
// faturas" e confere a soma com o "Total dos lançamentos atuais".

const PDFJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
const PDFJS_WORKER_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const MESES_ABREV = { JAN: 1, FEV: 2, MAR: 3, ABR: 4, MAI: 5, JUN: 6, JUL: 7, AGO: 8, SET: 9, OUT: 10, NOV: 11, DEZ: 12 };

const RE_LINHA = new RegExp(
    "^(\\d{2}/\\d{2}(?:/\\d{2,4})?|\\d{2}\\s+(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ))\\s+" +
    "(.+?)\\s+" +
    "(-?\\s?(?:R\\$\\s?)?-?\\d{1,3}(?:\\.\\d{3})*,\\d{2})\\s*$", "i");
const RE_CARTAO = /\(final (\d{4})\)/;
const RE_PARCELA = /\s*(\d{2}\/\d{2})$/;
const RE_CATEGORIA = /^([A-ZÀ-Ú][A-ZÀ-Ú ]+?)\s*\./;
const RE_VENCIMENTO = /vencimento:?\s*(\d{2})\/(\d{2})\/(\d{4})/i;
const RE_TOTAL = /total dos lan[cç]amentos atuais\s+([\d.]+,\d{2})/i;

// Comparados com o texto já sem acentos e em minúsculas
const INICIO_SECAO = ["lancamentos: compras e saques", "lancamentos: produtos e servicos", "lancamentos internacionais"];
const FIM_SECAO = ["compras parceladas - proximas faturas"];

let pdfjsCarregando = null;

function carregarPdfJs() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (!pdfjsCarregando) {
        pdfjsCarregando = new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = PDFJS_URL;
            s.onload = () => {
                window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;
                resolve(window.pdfjsLib);
            };
            s.onerror = () => {
                pdfjsCarregando = null;
                reject(new Error("Não foi possível carregar o leitor de PDF. Verifique a internet."));
            };
            document.head.appendChild(s);
        });
    }
    return pdfjsCarregando;
}

function normalizarTexto(texto) {
    return String(texto).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// "1.234,56", "R$ -45,90", "- 0,03" -> número
function valorDoTexto(texto) {
    let s = String(texto).replace(/[^\d,.\-]/g, "");
    const negativo = (s.match(/-/g) || []).length % 2 === 1;
    s = s.replace(/-/g, "");
    if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
    const n = parseFloat(s) || 0;
    return negativo ? -n : n;
}

function normalizarDataPdf(texto) {
    const t = texto.trim().toUpperCase();
    const m = t.match(/^(\d{2})\s+([A-Z]{3})/);
    if (m && MESES_ABREV[m[2]]) return `${m[1]}/${String(MESES_ABREV[m[2]]).padStart(2, "0")}`;
    return t.slice(0, 5);
}

// Monta as linhas de texto de um conjunto de itens do pdf.js (de cima para baixo)
function montarLinhas(itens) {
    const linhas = [];
    itens
        .slice()
        .sort((a, b) => b.y - a.y || a.x - b.x)
        .forEach(item => {
            let linha = linhas.find(l => Math.abs(l.y - item.y) <= 2.5);
            if (!linha) {
                linha = { y: item.y, itens: [] };
                linhas.push(linha);
            }
            linha.itens.push(item);
        });

    return linhas
        .sort((a, b) => b.y - a.y)
        .map(l => {
            const partes = l.itens.sort((a, b) => a.x - b.x);
            let texto = "";
            let fimAnterior = null;
            partes.forEach(p => {
                if (fimAnterior !== null && p.x - fimAnterior > 1 && !texto.endsWith(" ") && !p.str.startsWith(" ")) texto += " ";
                texto += p.str;
                fimAnterior = p.x + p.w;
            });
            return texto.replace(/\s+/g, " ").trim();
        })
        .filter(Boolean);
}

// Devolve os blocos de texto na ordem de leitura. Na fatura do Itaú cada
// página tem duas colunas de lançamentos: lê a esquerda e depois a direita.
async function blocosDoPdf(pdf) {
    const paginas = [];
    for (let n = 1; n <= pdf.numPages; n++) {
        const pagina = await pdf.getPage(n);
        const { width } = pagina.getViewport({ scale: 1 });
        const conteudo = await pagina.getTextContent();
        const itens = conteudo.items
            .filter(i => i.str && i.str.trim())
            .map(i => ({ str: i.str, x: i.transform[4], y: i.transform[5], w: i.width }));
        paginas.push({ width, itens });
    }

    const duasColunas = paginas.some(p => normalizarTexto(p.itens.map(i => i.str).join(" ")).includes("compras e saques"));
    const blocos = [];
    paginas.forEach(p => {
        if (!duasColunas) {
            blocos.push(montarLinhas(p.itens));
            return;
        }
        const meio = p.width * 0.588;
        blocos.push(montarLinhas(p.itens.filter(i => i.x < meio)));
        blocos.push(montarLinhas(p.itens.filter(i => i.x >= meio)));
    });
    return blocos;
}

async function lerFaturaPdf(arquivo) {
    const pdfjs = await carregarPdfJs();
    const pdf = await pdfjs.getDocument({ data: await arquivo.arrayBuffer() }).promise;
    const blocos = await blocosDoPdf(pdf);

    const textoCompleto = blocos.map(b => b.join("\n")).join("\n");
    const info = {};
    const venc = textoCompleto.match(RE_VENCIMENTO);
    if (venc) info.mes = `${venc[3]}-${venc[2]}`;
    const total = textoCompleto.match(RE_TOTAL);
    if (total) info.total = valorDoTexto(total[1]);

    const itens = [];
    let lendo = true;
    let cartao = "";
    blocos.forEach(linhas => linhas.forEach(linha => {
        const norm = normalizarTexto(linha);
        if (FIM_SECAO.some(t => norm.includes(t))) { lendo = false; return; }
        if (INICIO_SECAO.some(t => norm.includes(t))) { lendo = true; return; }
        const mCartao = linha.match(RE_CARTAO);
        if (mCartao && !norm.includes("lancamentos no cartao")) { cartao = mCartao[1]; return; }

        const m = linha.match(RE_LINHA);
        if (m) {
            if (!lendo) return;
            let desc = m[2].replace(/\s{2,}/g, " ").trim();
            const parcela = desc.match(RE_PARCELA);
            if (parcela) desc = `${desc.slice(0, parcela.index).trim()} (${parcela[1]})`;
            itens.push({ data: normalizarDataPdf(m[1]), desc, valor: valorDoTexto(m[3]), cartao, categoria: "" });
        } else if (lendo && itens.length && !itens[itens.length - 1].categoria) {
            // Linha logo abaixo da compra: "ALIMENTAÇÃO .SERRANA"
            const c = linha.match(RE_CATEGORIA);
            if (c) itens[itens.length - 1].categoria = c[1].trim();
        }
    }));

    return { itens, ...info };
}

// Mesmas regras do bot: "ignorar", "regras" (contem/cartao/categoria) e "padrao"
function aplicarRegrasFatura(itens, config, pessoas) {
    const padrao = (config.padrao || pessoas).filter(p => pessoas.includes(p));
    const ignorar = (config.ignorar || []).map(normalizarTexto);
    const regras = config.regras || [];

    const combina = (regra, item) => {
        if (!("contem" in regra || "cartao" in regra || "categoria" in regra)) return false;
        if ("contem" in regra && !normalizarTexto(item.desc).includes(normalizarTexto(regra.contem))) return false;
        if ("cartao" in regra && String(regra.cartao) !== item.cartao) return false;
        if ("categoria" in regra && normalizarTexto(regra.categoria) !== normalizarTexto(item.categoria)) return false;
        return true;
    };

    return itens
        .filter(item => item.valor !== 0 && !ignorar.some(t => normalizarTexto(item.desc).includes(t)))
        .map(item => {
            const regra = regras.find(r => combina(r, item));
            const daRegra = regra ? (regra.pagantes || []).filter(p => pessoas.includes(p)) : [];
            return {
                ...item,
                valor: Math.round(item.valor * 100) / 100,
                pagantes: daRegra.length ? daRegra : (padrao.length ? [...padrao] : [pessoas[0]]),
            };
        });
}
