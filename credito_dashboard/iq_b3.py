#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IQ.B3 — Company Intelligence Dashboard
Fonte de dados: Yahoo Finance (via requests + cookie/crumb) + Brapi (cotação BR)
"""
import json, time, requests, warnings
from flask import Flask, jsonify, render_template_string
from flask_cors import CORS

warnings.filterwarnings("ignore")
app = Flask(__name__)
CORS(app)

BRAPI_TOKEN = "ucaHWHuWF7tLMv47tpzQB8"  # cotação BR ao vivo
YF_HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://finance.yahoo.com",
}
_yf_session = None

def get_yf_session():
    global _yf_session
    if _yf_session:
        return _yf_session
    s = requests.Session()
    s.headers.update(YF_HEADERS)
    try:
        # obter cookie
        s.get("https://finance.yahoo.com", timeout=10)
        # obter crumb
        cr = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10)
        if cr.status_code == 200 and cr.text and '<' not in cr.text:
            s.params = {"crumb": cr.text.strip()}
    except:
        pass
    _yf_session = s
    return s

def yf_summary(ticker_sa):
    """Busca dados fundamentais do Yahoo Finance."""
    s = get_yf_session()
    mods = "summaryProfile,defaultKeyStatistics,financialData,recommendationTrend,institutionOwnership,majorHoldersBreakdown,price,summaryDetail,calendarEvents"
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker_sa}"
    try:
        r = s.get(url, params={"modules": mods}, timeout=15)
        if r.status_code != 200:
            # tentar query2
            r = requests.get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker_sa}",
                params={"modules": mods}, headers=YF_HEADERS, timeout=15
            )
        data = r.json()
        res = data.get("quoteSummary", {}).get("result", [])
        return res[0] if res else {}
    except Exception as e:
        return {"_error": str(e)}

def brapi_quote(ticker):
    """Cotação ao vivo da Brapi."""
    try:
        r = requests.get(
            f"https://brapi.dev/api/quote/{ticker}",
            params={"token": BRAPI_TOKEN, "dividends": "true"},
            timeout=10
        )
        data = r.json()
        return data.get("results", [{}])[0]
    except:
        return {}

def rv(d, *keys):
    """Extrai valor raw de dicionário aninhado do Yahoo."""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    if isinstance(d, dict):
        return d.get("raw", d.get("fmt"))
    return d

# ── Dados curados (Supply Chain, Índices, Peers) ──────────────────────────────
SC = {
    "CSNA3": {
        "clientes": [
            {"t":"MRVE3","n":"MRV Engenharia","r":"Cimento / Aço estrutural"},
            {"t":"CYRE3","n":"Cyrela","r":"Cimento / Estrutura metálica"},
            {"t":"—","n":"Setor Automotivo BR","r":"Aço laminado plano"},
            {"t":"—","n":"Construtoras BR","r":"Aço estrutural"},
            {"t":"—","n":"Mercado global (CMIN3)","r":"Minério de ferro"},
        ],
        "fornecedores": [
            {"t":"CMIG4","n":"CEMIG","r":"Energia elétrica (MG)"},
            {"t":"CSAN3","n":"Cosan / Comgás","r":"Gás natural industrial"},
            {"t":"RAIL3","n":"Rumo Logística","r":"Transporte ferroviário"},
            {"t":"—","n":"Importadores de carvão","r":"Coque metalúrgico"},
            {"t":"—","n":"Calcário MG/ES","r":"Insumo siderurgia"},
        ]
    },
    "VALE3": {
        "clientes": [
            {"t":"CSNA3","n":"CSN / CMIN3","r":"Minério de ferro / pelotas"},
            {"t":"GGBR4","n":"Gerdau","r":"Minério de ferro"},
            {"t":"—","n":"Baosteel / China Steel","r":"Maior cliente global"},
            {"t":"—","n":"ArcelorMittal Global","r":"Minério e pelotas"},
            {"t":"—","n":"Siderúrgicas Japão/Coreia","r":"Minério de ferro"},
        ],
        "fornecedores": [
            {"t":"RAIL3","n":"Rumo Logística","r":"Transporte ferroviário"},
            {"t":"CMIG4","n":"CEMIG","r":"Energia elétrica MG"},
            {"t":"—","n":"Komatsu / Caterpillar","r":"Equipamentos mineração"},
            {"t":"—","n":"Air Products","r":"Gases industriais"},
            {"t":"CSAN3","n":"Cosan","r":"Combustíveis e lubrificantes"},
        ]
    },
    "PETR4": {
        "clientes": [
            {"t":"VBBR3","n":"Vibra Energia","r":"Combustíveis distribuição"},
            {"t":"CSAN3","n":"Raízen / Cosan","r":"Combustíveis distribuição"},
            {"t":"AZUL4","n":"Azul Linhas Aéreas","r":"Querosene de aviação"},
            {"t":"GOLL4","n":"Gol","r":"Querosene de aviação"},
            {"t":"—","n":"Petroquímica / Exportação","r":"Petróleo cru e derivados"},
        ],
        "fornecedores": [
            {"t":"PRIO3","n":"PetroRio","r":"Serviços exploração E&P"},
            {"t":"—","n":"Halliburton / SLB","r":"Serviços de poço"},
            {"t":"—","n":"MODEC / BW Offshore","r":"FPSOs e plataformas"},
            {"t":"—","n":"Estaleiros BR","r":"Construção plataformas"},
            {"t":"—","n":"Transpetro","r":"Logística dutos e navios"},
        ]
    },
    "ITUB4": {
        "clientes": [
            {"t":"—","n":"Pessoas Físicas (PF)","r":"Crédito / conta corrente"},
            {"t":"—","n":"Pequenas e Médias Empresas","r":"Capital de giro / crédito"},
            {"t":"—","n":"Grandes Corporações","r":"CIB / tesouraria / DCM"},
            {"t":"—","n":"Governo e Entes Públicos","r":"Crédito público"},
        ],
        "fornecedores": [
            {"t":"TOTS3","n":"TOTVS","r":"Sistemas ERP / core banking"},
            {"t":"—","n":"IBM / Accenture","r":"TI e infraestrutura"},
            {"t":"—","n":"Mastercard / Visa","r":"Processamento pagamentos"},
            {"t":"—","n":"Serasa / Boa Vista","r":"Bureau de crédito"},
        ]
    },
    "WEGE3": {
        "clientes": [
            {"t":"—","n":"Indústria Manufatureira BR","r":"Motores elétricos"},
            {"t":"—","n":"Setor de Energia / Utilidades","r":"Geradores e transformadores"},
            {"t":"—","n":"Exportação global (60+ países)","r":"Automação industrial"},
            {"t":"PETR4","n":"Petrobras","r":"Motores / geradores offshore"},
        ],
        "fornecedores": [
            {"t":"—","n":"Aço siderúrgico (bobinas)","r":"Laminados para motores"},
            {"t":"CMIG4","n":"CEMIG","r":"Energia industrial SC/PR"},
            {"t":"—","n":"Fornecedores de cobre","r":"Enrolamentos elétricos"},
            {"t":"—","n":"Alumínio / fundição","r":"Carcaças de motores"},
        ]
    },
    "GGBR4": {
        "clientes": [
            {"t":"—","n":"Setor Automotivo BR","r":"Aço especial / longarinas"},
            {"t":"—","n":"Construção Civil","r":"Vergalhão / perfis"},
            {"t":"—","n":"Setor de Energia","r":"Aço para infraestrutura"},
            {"t":"—","n":"Exportação América do Norte","r":"Aços especiais Gerdau NA"},
        ],
        "fornecedores": [
            {"t":"VALE3","n":"Vale","r":"Minério de ferro / pelotas"},
            {"t":"—","n":"Sucata metálica","r":"Insumo principal EAF"},
            {"t":"—","n":"Fornecedores de energia","r":"Energia para fornos EAF"},
            {"t":"RAIL3","n":"Rumo / ferrovias","r":"Logística de insumos"},
        ]
    },
}

IX = {
    "CSNA3": [
        {"i":"IBOV","n":"Ibovespa","w":"~0,25%","d":"Principal índice da B3"},
        {"i":"IBRX100","n":"IBrX 100","w":"~0,2%","d":"100 ações mais negociadas"},
        {"i":"INDX","n":"Índice Industrial","w":"Sim","d":"Setor industrial B3"},
        {"i":"IMAT","n":"IMatBásicos","w":"Sim","d":"Materiais básicos"},
        {"i":"SMLL","n":"Small Cap","w":"Sim","d":"Menor capitalização relativa"},
    ],
    "VALE3": [
        {"i":"IBOV","n":"Ibovespa","w":"~10-12%","d":"Maior ou 2º maior peso"},
        {"i":"IBRX100","n":"IBrX 100","w":"~10%","d":"100 ações mais negociadas"},
        {"i":"IMAT","n":"IMatBásicos","w":"~55-65%","d":"Dominante em materiais"},
        {"i":"MSCI EM","n":"MSCI Emerging Markets","w":"Sim","d":"Referência global emergentes"},
        {"i":"S&P GSCI","n":"S&P GSCI Iron Ore","w":"Sim","d":"Commodities de ferro"},
    ],
    "PETR4": [
        {"i":"IBOV","n":"Ibovespa","w":"~8-10%","d":"Top 3 peso no Ibovespa"},
        {"i":"IBRX100","n":"IBrX 100","w":"~8%","d":"100 ações mais negociadas"},
        {"i":"IEEX","n":"IEE Energia","w":"~25-30%","d":"Setor petróleo e energia"},
        {"i":"IDIV","n":"IDIV Dividendos","w":"Sim","d":"Maiores pagadores de div."},
        {"i":"MSCI EM","n":"MSCI Emerging Markets","w":"Sim","d":"Global emergentes"},
    ],
    "ITUB4": [
        {"i":"IBOV","n":"Ibovespa","w":"~7-9%","d":"Top 5 peso"},
        {"i":"IFNC","n":"IFNC Financeiro","w":"~15%","d":"Setor financeiro B3"},
        {"i":"IBRX100","n":"IBrX 100","w":"~8%","d":"100 ações mais negociadas"},
        {"i":"MSCI EM","n":"MSCI Emerging Markets","w":"Sim","d":"Global emergentes"},
        {"i":"IDIV","n":"IDIV Dividendos","w":"Sim","d":"Maiores pagadores de div."},
    ],
    "WEGE3": [
        {"i":"IBOV","n":"Ibovespa","w":"~3-4%","d":"Top 15 peso"},
        {"i":"IBRX100","n":"IBrX 100","w":"~3%","d":"100 ações mais negociadas"},
        {"i":"INDX","n":"Índice Industrial","w":"Sim","d":"Setor industrial"},
        {"i":"MSCI EM","n":"MSCI Emerging Markets","w":"Sim","d":"Global emergentes"},
    ],
    "_DEFAULT": [
        {"i":"IBOV","n":"Ibovespa","w":"Verificar","d":"Principal índice da B3"},
        {"i":"IBRX100","n":"IBrX 100","w":"Verificar","d":"100 ações mais negociadas"},
        {"i":"MSCI EM","n":"MSCI Emerging Markets","w":"Verificar","d":"Global emergentes"},
    ],
}

PEERS = {
    "CSNA3":["GGBR4","USIM5","CMIN3"],
    "VALE3":["CMIN3","CSNA3","GGBR4"],
    "PETR4":["PRIO3","RECV3","RRRP3"],
    "ITUB4":["BBDC4","BBAS3","SANB11"],
    "WEGE3":["EGIE3","CPFE3","ENGI11"],
    "GGBR4":["CSNA3","USIM5","CMIN3"],
    "BBDC4":["ITUB4","BBAS3","SANB11"],
    "ABEV3":["SMTO3","VBBR3"],
    "RENT3":["MOVI3","HBSA3"],
    "MGLU3":["VIIA3","LREN3"],
}

# ── Rotas API ──────────────────────────────────────────────────────────────────

@app.route("/api/quote/<ticker>")
def quote(ticker):
    tk = ticker.upper()
    tk_sa = tk + ".SA"

    # 1. Cotação BR via Brapi
    bq = brapi_quote(tk)

    # 2. Fundamentals via Yahoo Finance
    yf = yf_summary(tk_sa)

    sp  = yf.get("summaryProfile", {})
    fd  = yf.get("financialData", {})
    ks  = yf.get("defaultKeyStatistics", {})
    pr  = yf.get("price", {})
    sd  = yf.get("summaryDetail", {})
    rt  = yf.get("recommendationTrend", {}).get("trend", [])
    ih  = yf.get("institutionOwnership", {}).get("ownershipList", [])
    mh  = yf.get("majorHoldersBreakdown", {})

    # Preço: prioriza Brapi (mais atualizado para B3)
    price     = bq.get("regularMarketPrice") or rv(pr, "regularMarketPrice")
    prev      = bq.get("regularMarketPreviousClose") or rv(sd, "previousClose")
    mkt_cap   = bq.get("marketCap") or rv(pr, "marketCap")
    pe        = bq.get("priceEarnings") or rv(sd, "trailingPE") or rv(ks, "trailingEps")
    eps       = bq.get("earningsPerShare") or rv(ks, "trailingEps")

    # Analistas
    rec_trend = rt[0] if rt else {}
    buy  = rec_trend.get("strongBuy", 0) + rec_trend.get("buy", 0)
    hold = rec_trend.get("hold", 0)
    sell = rec_trend.get("sell", 0) + rec_trend.get("strongSell", 0)

    # Acionistas institucionais
    holders = []
    for h in ih[:8]:
        org = h.get("organization")
        pct = rv(h, "pctHeld")
        if org and pct:
            holders.append({"name": org, "percentHeld": pct, "type": "Institucional"})

    # Diretores
    officers = []
    for o in sp.get("companyOfficers", [])[:10]:
        officers.append({
            "name": o.get("name",""),
            "title": o.get("title",""),
            "totalPay": rv(o, "totalPay"),
        })

    result = {
        # identidade
        "symbol":              tk,
        "longName":            bq.get("longName") or rv(pr, "longName") or tk,
        "shortName":           bq.get("shortName") or tk,
        "currency":            bq.get("currency","BRL"),
        "sector":              sp.get("sector",""),
        "industry":            sp.get("industry",""),
        "country":             sp.get("country","Brasil"),
        "fullTimeEmployees":   sp.get("fullTimeEmployees"),
        "longBusinessSummary": sp.get("longBusinessSummary",""),
        "website":             sp.get("website",""),
        "phone":               sp.get("phone",""),
        "address1":            sp.get("address1",""),
        "city":                sp.get("city",""),
        "state":               sp.get("state",""),
        # preço
        "regularMarketPrice":        price,
        "regularMarketPreviousClose":prev,
        "regularMarketChange":       bq.get("regularMarketChange"),
        "regularMarketChangePercent":bq.get("regularMarketChangePercent"),
        "regularMarketVolume":       bq.get("regularMarketVolume"),
        "regularMarketTime":         bq.get("regularMarketTime"),
        "fiftyTwoWeekHigh":          bq.get("fiftyTwoWeekHigh") or rv(sd,"fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow":           bq.get("fiftyTwoWeekLow")  or rv(sd,"fiftyTwoWeekLow"),
        "marketCap":                 mkt_cap,
        # valuation
        "priceEarnings":       pe,
        "earningsPerShare":    eps,
        "priceToBook":         rv(ks,"priceToBook"),
        "enterpriseValue":     rv(ks,"enterpriseValue"),
        "enterpriseToEbitda":  rv(ks,"enterpriseToEbitda"),
        "enterpriseToRevenue": rv(ks,"enterpriseToRevenue"),
        "beta":                rv(ks,"beta"),
        "sharesOutstanding":   rv(ks,"sharesOutstanding"),
        # financeiro
        "ebitda":              rv(fd,"ebitda"),
        "totalRevenue":        rv(fd,"totalRevenue"),
        "netIncome":           rv(ks,"netIncomeToCommon"),
        "grossMargins":        rv(fd,"grossMargins"),
        "ebitdaMargins":       rv(fd,"ebitdaMargins"),
        "profitMargins":       rv(fd,"profitMargins"),
        "returnOnEquity":      rv(fd,"returnOnEquity"),
        "returnOnAssets":      rv(fd,"returnOnAssets"),
        "totalCash":           rv(fd,"totalCash"),
        "totalDebt":           rv(fd,"totalDebt"),
        "debtToEquity":        rv(fd,"debtToEquity"),
        "bookValue":           rv(ks,"bookValue"),
        "totalCashPerShare":   rv(fd,"totalCashPerShare"),
        # dividendos
        "dividendYield":       rv(sd,"dividendYield") or rv(sd,"trailingAnnualDividendYield"),
        "dividendRate":        rv(sd,"dividendRate")  or rv(sd,"trailingAnnualDividendRate"),
        "payoutRatio":         rv(sd,"payoutRatio"),
        "exDividendDate":      rv(sd,"exDividendDate"),
        "lastDividendValue":   rv(ks,"lastDividendValue"),
        # analistas
        "analystRatings":      {"buy": buy, "hold": hold, "sell": sell},
        "recommendationKey":   rv(fd,"recommendationKey") or "",
        "targetMeanPrice":     rv(fd,"targetMeanPrice"),
        "targetLowPrice":      rv(fd,"targetLowPrice"),
        "targetHighPrice":     rv(fd,"targetHighPrice"),
        # pessoas
        "companyOfficers":     officers,
        "institutionalHolders":holders,
        "majorHolders":        [],
        # dividendos históricos Brapi
        "dividendsData":       bq.get("dividendsData", {}),
    }
    return jsonify({"results": [result]})

@app.route("/api/peers/<ticker>")
def peers(ticker):
    tk = ticker.upper()
    tickers = PEERS.get(tk, [])
    if not tickers:
        return jsonify({"results": []})
    results = []
    for t in tickers:
        bq = brapi_quote(t)
        if bq.get("regularMarketPrice"):
            yf = yf_summary(t + ".SA")
            ks = yf.get("defaultKeyStatistics", {})
            sd = yf.get("summaryDetail", {})
            fd = yf.get("financialData", {})
            results.append({
                "symbol": t,
                "longName": bq.get("longName", t),
                "regularMarketPrice": bq.get("regularMarketPrice"),
                "regularMarketPreviousClose": bq.get("regularMarketPreviousClose"),
                "marketCap": bq.get("marketCap"),
                "priceEarnings": bq.get("priceEarnings") or rv(sd,"trailingPE"),
                "enterpriseToEbitda": rv(ks,"enterpriseToEbitda"),
                "dividendYield": rv(sd,"dividendYield") or rv(sd,"trailingAnnualDividendYield"),
                "beta": rv(ks,"beta"),
            })
    return jsonify({"results": results})

@app.route("/api/sc/<ticker>")
def supply_chain(ticker):
    tk = ticker.upper()
    return jsonify(SC.get(tk, {"clientes":[],"fornecedores":[],"_aviso":"Supply chain não mapeado para este ticker ainda."}))

@app.route("/api/ix/<ticker>")
def indices(ticker):
    tk = ticker.upper()
    return jsonify({"indices": IX.get(tk, IX["_DEFAULT"])})

# ── Frontend (mesmo HTML anterior) ────────────────────────────────────────────

@app.route("/")
def index():
    return open("iq_b3_front.html").read()

if __name__ == "__main__":
    print("\n🔍  IQ.B3 Company Intelligence")
    print("    → http://127.0.0.1:5001\n")
    # resetar sessão YF a cada 30min
    import threading
    def reset_yf():
        global _yf_session
        while True:
            time.sleep(1800)
            _yf_session = None
    threading.Thread(target=reset_yf, daemon=True).start()
    app.run(debug=False, port=5001, host="127.0.0.1")
