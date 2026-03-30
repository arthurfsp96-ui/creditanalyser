#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
credito.py — Módulo de Monitoramento de Crédito Corporativo
Integrado ao market_bot de Arthur Silveira

Funcionalidades:
  - Baixa DFP/ITR automaticamente da CVM para empresas monitoradas
  - Calcula Altman Z'', Piotroski F-Score, DSCR e score composto
  - Persiste histórico de scores em CSV (dados/credito_historico.csv)
  - Envia alertas Telegram quando score deteriora significativamente
  - Ciclo semanal (segunda-feira) + on-demand (/credito TICKER)

Segue os padrões do market_bot:
  - Python puro sem pandas/numpy
  - Erros sempre logados, nunca silenciosos
  - Formatação Telegram: HTML com <b>, <i>, <code>
  - Separadores: {'─'*30}
"""

import os, csv, re, json, time, requests
from datetime import datetime, timedelta

# ── Imports internos do market_bot ──────────────────────────────────────────
try:
    from config import TELEGRAM_TOKEN, CHAT_ID, BRAPI_TOKEN
except ImportError:
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    CHAT_ID        = os.environ.get("CHAT_ID", "")
    BRAPI_TOKEN    = os.environ.get("BRAPI_TOKEN", "ucaHWHuWF7tLMv47tpzQB8")

# ── Arquivo de histórico de crédito ────────────────────────────────────────
CREDITO_CSV = os.path.join(os.path.dirname(__file__), "dados", "credito_historico.csv")
CREDITO_CACHE_JSON = os.path.join(os.path.dirname(__file__), "dados", "credito_cache.json")

HEADERS_CSV = [
    "data", "ticker", "empresa", "ano_dfp",
    "score_credito", "rating_br", "rating_global", "categoria",
    "z_score", "zona_altman",
    "f_score", "qualidade_piotroski",
    "dscr", "nivel_dscr",
    "alavancagem", "icj", "margem_ebitda", "fco_receita",
    "receita_bi", "ebitda_bi", "divida_liquida_bi", "caixa_bi",
    "lucro_liquido_bi",
    "fonte_dados",  # CVM_DFP, CVM_ITR, BRAPI, MANUAL
]

# ── Watchlist de empresas monitoradas ──────────────────────────────────────
# Edite esta lista para adicionar/remover empresas
# Formato: {"ticker": "PETR4", "cnpj": "00.000.000/0000-00", "nome": "Empresa"}
WATCHLIST_CREDITO = [
    {"ticker": "PETR4",  "cnpj": "00.362.305/0001-04", "nome": "Petrobras",        "setor": "petroleo"},
    {"ticker": "VALE3",  "cnpj": "33.592.510/0001-54", "nome": "Vale",             "setor": "mineracao"},
    {"ticker": "ITUB4",  "cnpj": "60.701.190/0001-04", "nome": "Itaú Unibanco",    "setor": "bancos"},
    {"ticker": "BBDC4",  "cnpj": "60.746.948/0001-12", "nome": "Bradesco",         "setor": "bancos"},
    {"ticker": "BBAS3",  "cnpj": "00.000.000/0001-91", "nome": "Banco do Brasil",  "setor": "bancos"},
    {"ticker": "WEGE3",  "cnpj": "84.429.695/0001-11", "nome": "WEG",              "setor": "industrial"},
    {"ticker": "SUZB3",  "cnpj": "16.404.287/0001-55", "nome": "Suzano",           "setor": "papel"},
    {"ticker": "GGBR4",  "cnpj": "33.611.500/0001-19", "nome": "Gerdau",           "setor": "siderurgia"},
    {"ticker": "ABEV3",  "cnpj": "00.395.288/0003-78", "nome": "Ambev",            "setor": "alimentos"},
    {"ticker": "RAIL3",  "cnpj": "02.387.241/0001-60", "nome": "Rumo Logística",   "setor": "logistica"},
]

# Limiares para alertas de deterioração
LIMIAR_SCORE_QUEDA    = 1.5   # Queda de 1.5+ pontos no score composto → alerta
LIMIAR_ZSCORE_QUEDA   = 0.5   # Queda de 0.5+ no Z'' → alerta
LIMIAR_FSCORE_QUEDA   = 2     # Queda de 2+ pontos no F-Score → alerta
LIMIAR_ALAV_SUBIDA    = 0.5   # Alta de 0.5x na alavancagem → alerta


# ═══════════════════════════════════════════════════════════════════════════
# COLETA DE DADOS FINANCEIROS
# ═══════════════════════════════════════════════════════════════════════════

def _cnpj_limpo(cnpj):
    return re.sub(r"\D", "", cnpj or "")


def _cvm_buscar_cadastro():
    """Cache do cadastro CVM em JSON local (válido por 7 dias)."""
    cache_file = os.path.join(os.path.dirname(__file__), "dados", "cvm_cadastro.json")
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if (time.time() - mtime) < 7 * 86400:  # 7 dias
                with open(cache_file, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
    try:
        import io as _io
        r = requests.get(
            "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv",
            timeout=30, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return {}
        reader = csv.DictReader(_io.StringIO(r.content.decode("latin-1")), delimiter=";")
        cad = {}
        for row in reader:
            cnpj = _cnpj_limpo(row.get("CNPJ_CIA", ""))
            if cnpj:
                cad[cnpj] = {
                    "cod_cvm": row.get("CD_CVM", "").strip(),
                    "nome_pregao": row.get("DENOM_COMERC", "").strip(),
                    "segmento": row.get("SEGM", "").strip(),
                    "situacao_cvm": row.get("SIT", "").strip(),
                }
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cad, f, ensure_ascii=False)
        print(f"✓ Cadastro CVM carregado: {len(cad)} empresas")
        return cad
    except Exception as e:
        print(f"✗ Erro carregando cadastro CVM: {e}")
        return {}


def _cvm_extrair_dados(cod_cvm, ano, tipo="DFP"):
    """
    Baixa e extrai dados financeiros da CVM para um cod_cvm específico.
    tipo: DFP (anual) ou ITR (trimestral)
    """
    import io as _io
    base = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"

    # Mapa de contas CVM → campos internos
    CONTA_CAMPO = {
        "3.01": "receita_liquida", "3.03": "lucro_bruto",
        "3.05": "ebit",           "3.06": "resultado_financeiro",
        "3.11": "lucro_liquido",
        "1":    "ativo_total",    "1.01": "ativo_circulante",
        "1.01.01": "caixa",       "1.02": "ativo_nao_circulante",
        "2.01": "passivo_circulante", "2.02": "passivo_nao_circulante",
        "2.03": "patrimonio_liquido", "2.03.04": "lucros_retidos",
        "6.01": "fco",            "6.02": "capex_liquido",
    }
    CONTAS_DIVIDA = {"2.01.04", "2.01.05", "2.02.01", "2.02.02"}

    dados = {}
    divida_acc = 0.0

    arquivos = (
        [f"DFP/DADOS/dfp_cia_aberta_{t}_con_{ano}.csv" for t in ["DRE", "BPA", "BPP", "DFC_MD"]]
        if tipo == "DFP" else
        [f"ITR/DADOS/itr_cia_aberta_{t}_con_{ano}.csv" for t in ["DRE", "BPA", "BPP", "DFC_MD"]]
    )

    for arq in arquivos:
        url = f"{base}/{arq}"
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
            if r.status_code != 200:
                continue
            content = r.content.decode("latin-1", errors="replace")
            reader = csv.DictReader(_io.StringIO(content), delimiter=";")
            rows_empresa = [
                row for row in reader
                if row.get("CD_CVM", "").strip() == str(cod_cvm)
                and row.get("ORDEM_EXERC", "").strip() == "ÚLTIMO"
            ]
            if not rows_empresa:
                continue
            # Versão mais recente
            max_v = max(int(r2.get("VERSAO", 0) or 0) for r2 in rows_empresa)
            rows_empresa = [r2 for r2 in rows_empresa if int(r2.get("VERSAO", 0) or 0) == max_v]
            escala_str = rows_empresa[0].get("ESCALA_MOEDA", "MIL").strip().upper()
            escala = 1.0 if escala_str == "MIL" else 0.001
            dados["dt_refer"] = rows_empresa[0].get("DT_FIM_EXERC", "")
            for row in rows_empresa:
                conta = row.get("CD_CONTA", "").strip()
                try:
                    val = float(row.get("VL_CONTA", "0").replace(",", ".") or 0) * escala
                except (ValueError, AttributeError):
                    val = 0.0
                if conta in CONTA_CAMPO:
                    dados[CONTA_CAMPO[conta]] = val
                if conta in CONTAS_DIVIDA:
                    divida_acc += val
        except Exception as e:
            print(f"  [AVISO] {arq}: {e}")

    if divida_acc > 0:
        dados["divida_bruta"] = divida_acc
    cx = dados.get("caixa", 0)
    db = dados.get("divida_bruta", 0)
    if db > 0:
        dados["divida_liquida"] = max(0, db - cx)
    # EBITDA = EBIT + D&A estimado (7% receita)
    if not dados.get("ebitda_ajustado") and dados.get("ebit", 0) > 0:
        rl = dados.get("receita_liquida", 0)
        dados["ebitda_ajustado"] = dados["ebit"] + (rl * 0.07 if rl > 0 else 0)
        dados["_ebitda_estimado"] = True
    return dados


def coletar_dados_empresa(empresa: dict) -> dict:
    """
    Coleta dados financeiros completos de uma empresa da watchlist.
    Pipeline: CVM DFP → CVM ITR → Brapi (fallback)
    """
    ticker = empresa["ticker"].upper()
    cnpj   = _cnpj_limpo(empresa.get("cnpj", ""))
    nome   = empresa.get("nome", ticker)
    ano    = datetime.now().year - 1  # Último DFP fechado

    print(f"  → {ticker} ({nome})...", end=" ", flush=True)

    # 1. Tentar CVM
    cad = _cvm_buscar_cadastro()
    dados = {}
    fonte = "SEM_DADOS"

    if cnpj and cnpj in cad:
        cod_cvm = cad[cnpj]["cod_cvm"]
        dados = _cvm_extrair_dados(cod_cvm, ano, "DFP")
        if dados.get("receita_liquida"):
            fonte = "CVM_DFP"
        else:
            # Tentar ITR mais recente
            dados = _cvm_extrair_dados(cod_cvm, datetime.now().year, "ITR")
            if dados.get("receita_liquida"):
                fonte = "CVM_ITR"

    # 2. Fallback Brapi (indicadores sumários)
    if not dados.get("receita_liquida"):
        try:
            r = requests.get(
                f"https://brapi.dev/api/quote/{ticker}",
                params={"token": BRAPI_TOKEN,
                        "modules": "financialData,defaultKeyStatistics,balanceSheetHistory"},
                timeout=12
            )
            if r.status_code == 200:
                res = r.json().get("results", [{}])[0]
                fd  = res.get("financialData", {})
                ks  = res.get("defaultKeyStatistics", {})
                def _rv(d, k):
                    v = d.get(k)
                    return v.get("raw") if isinstance(v, dict) else v
                # Brapi retorna valores em USD/unidade — converter para BRL/mil aproximado
                # (simplificação: usamos os valores como proxy)
                dados = {
                    "receita_liquida":   (_rv(fd, "totalRevenue") or 0) / 1e6,  # → bilhões→mil
                    "lucro_liquido":     (_rv(ks, "netIncomeToCommon") or 0) / 1e6,
                    "ebitda_ajustado":   (_rv(fd, "ebitda") or 0) / 1e6,
                    "patrimonio_liquido": (_rv(ks, "bookValue") or 0),
                    "fco":               (_rv(fd, "operatingCashflow") or 0) / 1e6,
                    "divida_bruta":      (_rv(fd, "totalDebt") or 0) / 1e6,
                    "caixa":             (_rv(fd, "totalCash") or 0) / 1e6,
                }
                dados["divida_liquida"] = max(0, dados["divida_bruta"] - dados["caixa"])
                fonte = "BRAPI"
        except Exception as e:
            print(f"✗ Brapi: {e}")

    if not dados.get("receita_liquida"):
        print("✗ Sem dados")
        return {"ticker": ticker, "nome": nome, "_erro": "Sem dados disponíveis"}

    dados.update({"ticker": ticker, "nome": nome, "_fonte": fonte})
    print(f"✓ {fonte}")
    return dados


# ═══════════════════════════════════════════════════════════════════════════
# MODELOS DE SCORING (replicados do credito_dashboard para independência)
# ═══════════════════════════════════════════════════════════════════════════

def _calcular_zscore(d):
    at = d.get("ativo_total", 0)
    if not at or at <= 0:
        return None
    X1 = ((d.get("ativo_circulante", 0) - d.get("passivo_circulante", 0)) / at)
    X2 = (d.get("lucros_retidos", d.get("lucro_liquido", 0)) / at)
    X3 = (d.get("ebitda_ajustado", d.get("ebit", 0)) / at)
    dt = d.get("divida_bruta", 0)
    pl = d.get("patrimonio_liquido", 0)
    X4 = (pl / dt) if dt > 0 else 1.0
    Z = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
    zona = "segura" if Z > 2.6 else ("cinza" if Z > 1.1 else "distress")
    return {"z_score": round(Z, 3), "zona": zona}


def _calcular_piotroski(d):
    """F-Score simplificado (sem período anterior — calcula critérios absolutos)."""
    at = d.get("ativo_total", 0)
    pontos = 0
    # F1: ROA > 0
    if at > 0 and d.get("lucro_liquido", 0) > 0:
        pontos += 1
    # F2: FCO > 0
    if d.get("fco", 0) > 0:
        pontos += 1
    # F4: FCO > Lucro (qualidade)
    if at > 0 and d.get("fco", 0) / at > (d.get("lucro_liquido", 0) or 0) / at:
        pontos += 1
    # F5: Alavancagem < 0.5 (proxy de redução)
    dt = d.get("divida_bruta", 0)
    if at > 0 and dt / at < 0.5:
        pontos += 1
    # F6: Liquidez corrente >= 1
    ac = d.get("ativo_circulante", 0)
    pc = d.get("passivo_circulante", 0)
    if pc > 0 and ac / pc >= 1.0:
        pontos += 1
    # F8: Margem bruta > 0
    rl = d.get("receita_liquida", 0)
    lb = d.get("lucro_bruto", 0)
    if rl > 0 and lb > 0:
        pontos += 1
    # F9: Giro do ativo > 0.3
    if at > 0 and rl / at > 0.3:
        pontos += 1
    qualidade = (
        "FORTE" if pontos >= 7 else
        "MODERADA" if pontos >= 5 else
        "FRACA" if pontos >= 3 else
        "MUITO FRACA"
    )
    return {"f_score": pontos, "qualidade": qualidade}


def _calcular_score_credito(d):
    """Score composto 0-10."""
    eb  = d.get("ebitda_ajustado", 0)
    dl  = d.get("divida_liquida", 0)
    rl  = d.get("receita_liquida", 0)
    cx  = d.get("caixa", 0)
    jur = d.get("juros_pagos", abs(d.get("resultado_financeiro", 0)))
    fco = d.get("fco", 0)
    pc  = d.get("passivo_circulante", 0)

    alav = dl / eb if eb > 0 else 99
    icj  = eb / jur if jur > 0 else (5.0 if eb > 0 else 0)
    liq  = cx / pc if pc > 0 else (2.0 if cx > 0 else 0)
    mg   = eb / rl * 100 if rl > 0 else 0
    fco_r = fco / rl * 100 if rl > 0 else 0

    def sc_alav(x):
        thresholds = [(1, 10), (1.5, 9), (2, 8), (2.5, 7), (3, 6), (3.5, 5), (4, 4), (5, 3), (6, 2)]
        for t, s in thresholds:
            if x <= t: return s
        return 1

    def sc_icj(x):
        if x >= 6: return 10
        if x >= 4: return 8
        if x >= 3: return 7
        if x >= 2: return 6
        if x >= 1.5: return 5
        if x >= 1: return 4
        return 2

    def sc_liq(x):
        if x >= 2: return 10
        if x >= 1.5: return 8
        if x >= 1: return 6
        if x >= 0.75: return 4
        return 2

    def sc_mg(x):
        if x >= 35: return 10
        if x >= 25: return 8
        if x >= 20: return 7
        if x >= 15: return 6
        if x >= 10: return 5
        if x >= 5: return 3
        return 1

    def sc_fco(x):
        if x >= 15: return 10
        if x >= 10: return 8
        if x >= 5: return 6
        if x >= 0: return 4
        return 2

    def sc_escala(r):
        if r >= 50000: return 10
        if r >= 20000: return 8
        if r >= 10000: return 7
        if r >= 5000: return 6
        if r >= 2000: return 5
        if r >= 500: return 4
        return 3

    pesos = [
        (sc_alav(alav), 0.30),
        (sc_icj(icj),   0.20),
        (sc_liq(liq),   0.15),
        (sc_mg(mg),     0.15),
        (sc_fco(fco_r), 0.10),
        (sc_escala(rl), 0.10),
    ]
    score = sum(s * p for s, p in pesos)

    def s2r(s):
        thresholds = [(9.5, "brAAA", "Aaa/AAA"), (8.5, "brAA", "Aa2/AA"),
                      (7.5, "brA+", "A1/A+"),    (6.5, "brA-", "Baa1/BBB+"),
                      (5.5, "brBBB", "Baa3/BBB-"),(4.5, "brBB+", "Ba1/BB+"),
                      (3.5, "brBB", "Ba2/BB"),    (2.5, "brB+", "B1/B+"),
                      (1.5, "brB-", "B3/B-")]
        for t, br, gl in thresholds:
            if s >= t: return br, gl
        return "brCCC", "Caa1/CCC"

    br, gl = s2r(score)
    cat = ("IG" if score >= 5.5 else "HY" if score >= 3.5 else "Distressed")
    return {
        "score": round(score, 2), "rating_br": br,
        "rating_global": gl, "categoria": cat,
        "inputs": {"alavancagem": round(alav, 2) if alav < 99 else None,
                   "icj": round(icj, 2), "liquidez": round(liq, 2),
                   "margem_ebitda": round(mg, 1), "fco_receita": round(fco_r, 1)}
    }


def _calcular_dscr(d):
    fco = d.get("fco", 0)
    jur = d.get("juros_pagos", abs(d.get("resultado_financeiro", 0) or 0))
    amort = d.get("divida_cp", 0) or d.get("passivo_circulante", 0) * 0.4
    ds = jur + amort
    if ds <= 0 or fco == 0:
        return None
    dscr = fco / ds
    nivel = "SEGURO" if dscr >= 1.5 else ("ADEQUADO" if dscr >= 1.25 else ("LIMITE" if dscr >= 1.0 else "RISCO"))
    return {"dscr": round(dscr, 3), "nivel": nivel}


# ═══════════════════════════════════════════════════════════════════════════
# PERSISTÊNCIA — CSV de histórico de scores
# ═══════════════════════════════════════════════════════════════════════════

def _garantir_csv():
    os.makedirs(os.path.dirname(CREDITO_CSV), exist_ok=True)
    if not os.path.exists(CREDITO_CSV):
        with open(CREDITO_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=HEADERS_CSV).writeheader()


def salvar_score(d: dict, sc: dict, zs: dict, pf: dict, dscr_r: dict):
    """Salva os scores calculados no histórico CSV."""
    _garantir_csv()
    eb = d.get("ebitda_ajustado", 0)
    dl = d.get("divida_liquida", 0)
    rl = d.get("receita_liquida", 0)
    row = {
        "data":             datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ticker":           d.get("ticker", ""),
        "empresa":          d.get("nome", ""),
        "ano_dfp":          d.get("dt_refer", "")[:4] if d.get("dt_refer") else "",
        "score_credito":    sc.get("score", ""),
        "rating_br":        sc.get("rating_br", ""),
        "rating_global":    sc.get("rating_global", ""),
        "categoria":        sc.get("categoria", ""),
        "z_score":          zs.get("z_score", "") if zs else "",
        "zona_altman":      zs.get("zona", "") if zs else "",
        "f_score":          pf.get("f_score", "") if pf else "",
        "qualidade_piotroski": pf.get("qualidade", "") if pf else "",
        "dscr":             dscr_r.get("dscr", "") if dscr_r else "",
        "nivel_dscr":       dscr_r.get("nivel", "") if dscr_r else "",
        "alavancagem":      sc.get("inputs", {}).get("alavancagem", ""),
        "icj":              sc.get("inputs", {}).get("icj", ""),
        "margem_ebitda":    sc.get("inputs", {}).get("margem_ebitda", ""),
        "fco_receita":      sc.get("inputs", {}).get("fco_receita", ""),
        "receita_bi":       round(rl / 1000, 2) if rl else "",
        "ebitda_bi":        round(eb / 1000, 2) if eb else "",
        "divida_liquida_bi": round(dl / 1000, 2) if dl else "",
        "caixa_bi":         round(d.get("caixa", 0) / 1000, 2) if d.get("caixa") else "",
        "lucro_liquido_bi": round(d.get("lucro_liquido", 0) / 1000, 2) if d.get("lucro_liquido") else "",
        "fonte_dados":      d.get("_fonte", ""),
    }
    with open(CREDITO_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=HEADERS_CSV).writerow(row)


def carregar_historico_score(ticker: str, n_ultimos: int = 5) -> list:
    """Retorna os últimos N scores de um ticker (mais recente primeiro)."""
    _garantir_csv()
    rows = []
    try:
        with open(CREDITO_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("ticker", "").upper() == ticker.upper():
                    rows.append(row)
    except Exception as e:
        print(f"[AVISO] carregar_historico_score: {e}")
    return rows[-n_ultimos:][::-1]  # Mais recente primeiro


def detectar_deterioracao(ticker: str, score_atual: float, z_atual: float,
                           f_atual: int, alav_atual: float) -> list:
    """
    Compara score atual com o anterior e retorna lista de alertas de deterioração.
    """
    historico = carregar_historico_score(ticker, n_ultimos=3)
    if not historico:
        return []

    alertas = []
    ultimo = historico[0]  # Score mais recente salvo (antes do atual)

    try:
        sc_ant  = float(ultimo.get("score_credito") or 0)
        z_ant   = float(ultimo.get("z_score") or 0)
        f_ant   = int(ultimo.get("f_score") or 0)
        al_ant  = float(ultimo.get("alavancagem") or 0)
        data_ant = ultimo.get("data", "")

        if sc_ant > 0 and (sc_ant - score_atual) >= LIMIAR_SCORE_QUEDA:
            alertas.append(
                f"📉 Score de crédito: {sc_ant:.1f} → {score_atual:.1f} "
                f"(-{sc_ant - score_atual:.1f} pts desde {data_ant[:10]})"
            )
        if z_ant > 0 and z_atual is not None and (z_ant - z_atual) >= LIMIAR_ZSCORE_QUEDA:
            alertas.append(
                f"📉 Altman Z'': {z_ant:.2f} → {z_atual:.2f} "
                f"(-{z_ant - z_atual:.2f} — deterioração de zona)"
            )
        if f_ant > 0 and (f_ant - f_atual) >= LIMIAR_FSCORE_QUEDA:
            alertas.append(
                f"📉 Piotroski F-Score: {f_ant}/9 → {f_atual}/9 "
                f"(-{f_ant - f_atual} critérios)"
            )
        if al_ant > 0 and alav_atual is not None and (alav_atual - al_ant) >= LIMIAR_ALAV_SUBIDA:
            alertas.append(
                f"📈 Alavancagem: {al_ant:.1f}x → {alav_atual:.1f}x "
                f"(+{alav_atual - al_ant:.1f}x — elevação de risco)"
            )
    except (ValueError, TypeError) as e:
        print(f"[AVISO] detectar_deterioracao {ticker}: {e}")

    return alertas


# ═══════════════════════════════════════════════════════════════════════════
# FORMATAÇÃO TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════

_EMOJI_ZONA = {"segura": "🟢", "cinza": "🟡", "distress": "🔴"}
_EMOJI_SCORE = {range(0, 4): "🔴", range(4, 6): "🟠", range(6, 8): "🟡", range(8, 11): "🟢"}
_EMOJI_PF = {"FORTE": "🟢", "MODERADA": "🟡", "FRACA": "🟠", "MUITO FRACA": "🔴"}
_EMOJI_DSCR = {"SEGURO": "🟢", "ADEQUADO": "🟡", "LIMITE": "🟠", "RISCO": "🔴"}
_EMOJI_RATING = {"IG": "🔵", "HY": "🟡", "Distressed": "🔴"}


def _emoji_score(s):
    for r, e in _EMOJI_SCORE.items():
        if int(s) in r: return e
    return "⚪"


def formatar_relatorio_empresa(d, sc, zs, pf, dscr_r, alertas) -> str:
    """Gera mensagem Telegram completa para uma empresa."""
    ticker = d.get("ticker", "")
    nome   = d.get("nome", ticker)
    fonte  = d.get("_fonte", "?")
    eb = d.get("ebitda_ajustado", 0)
    dl = d.get("divida_liquida", 0)
    rl = d.get("receita_liquida", 0)
    cx = d.get("caixa", 0)
    ll = d.get("lucro_liquido", 0)
    alav = sc.get("inputs", {}).get("alavancagem")
    mg   = sc.get("inputs", {}).get("margem_ebitda", 0)

    score = sc.get("score", 0)
    cat   = sc.get("categoria", "")
    em_sc = _emoji_score(score)
    em_cat = _EMOJI_RATING.get(cat, "⚪")

    lines = [
        f"<b>━━━ {ticker} — {nome} ━━━</b>",
        f"<i>Análise de Crédito · Fonte: {fonte}</i>",
        "",
        f"<b>{em_sc} Score de Crédito: {score:.1f}/10</b>  "
        f"<code>{sc.get('rating_br','—')}</code> / <code>{sc.get('rating_global','—')}</code>  {em_cat} {cat}",
        "",
        "<b>📊 Indicadores Financeiros</b>",
        f"<code>"
        f"Receita Líq.  R$ {rl/1000:.1f}bi\n"
        f"EBITDA        R$ {eb/1000:.1f}bi  ({mg:.1f}%)\n"
        f"Lucro Líq.    R$ {ll/1000:.1f}bi\n"
        f"Dívida Líq.   R$ {dl/1000:.1f}bi  ({alav:.1f}x EBITDA)\n"
        f"Caixa         R$ {cx/1000:.1f}bi"
        f"</code>",
        "",
        "<b>🔬 Modelos Quantitativos</b>",
    ]

    # Altman Z''
    if zs:
        em_z = _EMOJI_ZONA.get(zs["zona"], "⚪")
        lines.append(f"  {em_z} Altman Z'': <b>{zs['z_score']:.3f}</b> — {zs['zona'].upper()}")

    # Piotroski F-Score
    if pf:
        em_pf = _EMOJI_PF.get(pf["qualidade"], "⚪")
        lines.append(f"  {em_pf} Piotroski F-Score: <b>{pf['f_score']}/9</b> — {pf['qualidade']}")

    # DSCR
    if dscr_r:
        em_dscr = _EMOJI_DSCR.get(dscr_r["nivel"], "⚪")
        lines.append(f"  {em_dscr} DSCR: <b>{dscr_r['dscr']:.2f}x</b> — {dscr_r['nivel']}")

    # Alertas de deterioração
    if alertas:
        lines += ["", "⚠️ <b>ALERTAS DE DETERIORAÇÃO</b>"]
        for a in alertas:
            lines.append(f"  {a}")

    lines.append(f"\n<i>⏱ {datetime.now().strftime('%d/%m/%Y %H:%M')}</i>")
    return "\n".join(lines)


def formatar_resumo_watchlist(resultados: list) -> str:
    """Gera mensagem compacta com ranking de crédito da watchlist."""
    # Ordenar por score (pior primeiro = mais atenção)
    ordenado = sorted(resultados, key=lambda x: x.get("score", 0))

    lines = [
        "📋 <b>MONITORAMENTO DE CRÉDITO SEMANAL</b>",
        f"<i>{datetime.now().strftime('%d/%m/%Y')} · {len(resultados)} empresas</i>",
        "",
        "<b>Score  Ticker  Rating   Altman  Piots.  Alav.</b>",
        "<code>",
    ]

    for r in ordenado:
        sc    = r.get("score", 0)
        tk    = r.get("ticker", "???").ljust(6)
        rat   = r.get("rating_br", "—").ljust(6)
        z     = r.get("z_score")
        f_sc  = r.get("f_score")
        alav  = r.get("alavancagem")

        em = _emoji_score(sc)
        z_str = f"{z:.2f}" if z is not None else "—"
        f_str = f"{f_sc}/9" if f_sc is not None else "—"
        al_str = f"{alav:.1f}x" if alav is not None else "—"

        lines.append(f"{em} {sc:.1f}  {tk} {rat}  {z_str}   {f_str}    {al_str}")

    lines.append("</code>")

    # Destacar piores
    piores = [r for r in ordenado if r.get("score", 10) < 4.5]
    if piores:
        lines += ["", "🔴 <b>Requer atenção:</b>"]
        for r in piores[:3]:
            lines.append(
                f"  • <b>{r['ticker']}</b> {r.get('rating_br','—')} — "
                f"score {r.get('score',0):.1f} | alav {r.get('alavancagem','—')}x"
            )

    lines.append(f"\n<i>Use /credito TICKER para relatório completo</i>")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# ENVIO TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════

def _enviar_telegram(mensagem: str, parse_mode="HTML"):
    """Envia mensagem via Telegram Bot."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": mensagem,
                  "parse_mode": parse_mode, "disable_web_page_preview": True},
            timeout=15
        )
        if r.status_code != 200:
            print(f"[ERRO] Telegram: {r.status_code} — {r.text[:100]}")
            return False
        return True
    except Exception as e:
        print(f"[ERRO] Telegram send: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# CICLOS PRINCIPAIS
# ═══════════════════════════════════════════════════════════════════════════

def ciclo_credito_semanal():
    """
    Ciclo semanal de monitoramento de crédito (segunda-feira, junto ao ciclo_semanal).
    1. Coleta dados de todas as empresas da watchlist
    2. Calcula scores
    3. Detecta deteriorações vs semana anterior
    4. Envia resumo + alertas no Telegram
    """
    print("\n" + "─"*40)
    print("💳 CICLO CRÉDITO SEMANAL")
    print("─"*40)

    resultados_tabela = []
    alertas_globais   = []

    for empresa in WATCHLIST_CREDITO:
        ticker = empresa["ticker"]
        try:
            # Coletar dados
            d   = coletar_dados_empresa(empresa)
            if d.get("_erro"):
                continue

            # Calcular scores
            sc    = _calcular_score_credito(d)
            zs    = _calcular_zscore(d)
            pf    = _calcular_piotroski(d)
            dscr_r = _calcular_dscr(d)

            score_val = sc.get("score", 0)
            z_val     = zs.get("z_score") if zs else None
            f_val     = pf.get("f_score", 0) if pf else 0
            alav_val  = sc.get("inputs", {}).get("alavancagem")

            # Detectar deterioração vs histórico
            alertas_empresa = detectar_deterioracao(
                ticker, score_val, z_val or 0, f_val, alav_val or 0
            )

            # Salvar no histórico
            salvar_score(d, sc, zs, pf, dscr_r)

            # Acumular para tabela resumo
            resultados_tabela.append({
                "ticker":      ticker,
                "score":       score_val,
                "rating_br":   sc.get("rating_br"),
                "z_score":     z_val,
                "f_score":     f_val,
                "alavancagem": alav_val,
            })

            # Se tem alertas, enviar relatório completo
            if alertas_empresa:
                alertas_globais.append((ticker, empresa["nome"], alertas_empresa))
                msg = formatar_relatorio_empresa(d, sc, zs, pf, dscr_r, alertas_empresa)
                _enviar_telegram(f"⚠️ ALERTA DE CRÉDITO\n\n{msg}")
                time.sleep(1)

        except Exception as e:
            print(f"[ERRO] {ticker}: {e}")
            import traceback
            traceback.print_exc()

    # Enviar resumo da watchlist
    if resultados_tabela:
        msg_resumo = formatar_resumo_watchlist(resultados_tabela)
        _enviar_telegram(msg_resumo)
        print(f"✓ Resumo enviado: {len(resultados_tabela)} empresas")

    if not alertas_globais:
        print("✓ Sem deteriorações detectadas")

    return resultados_tabela


def relatorio_empresa_on_demand(ticker: str) -> bool:
    """
    Gera e envia relatório completo de crédito para um ticker específico.
    Acionado por comando /credito TICKER no Telegram.
    """
    ticker = ticker.upper().strip()
    print(f"\n💳 Relatório on-demand: {ticker}")

    # Encontrar empresa na watchlist ou criar entrada temporária
    empresa = next((e for e in WATCHLIST_CREDITO if e["ticker"] == ticker), None)
    if not empresa:
        # Tentar pelo ticker mesmo sem CNPJ (usará Brapi como fallback)
        empresa = {"ticker": ticker, "cnpj": "", "nome": ticker, "setor": "default"}

    try:
        d      = coletar_dados_empresa(empresa)
        if d.get("_erro"):
            _enviar_telegram(f"❌ <b>{ticker}</b>: {d['_erro']}")
            return False

        sc     = _calcular_score_credito(d)
        zs     = _calcular_zscore(d)
        pf     = _calcular_piotroski(d)
        dscr_r = _calcular_dscr(d)
        alav_v = sc.get("inputs", {}).get("alavancagem", 0)
        alertas = detectar_deterioracao(
            ticker, sc.get("score", 0),
            zs.get("z_score", 0) if zs else 0,
            pf.get("f_score", 0) if pf else 0,
            alav_v or 0
        )
        msg = formatar_relatorio_empresa(d, sc, zs, pf, dscr_r, alertas)
        _enviar_telegram(f"💳 RELATÓRIO DE CRÉDITO\n\n{msg}")

        # Salvar no histórico
        salvar_score(d, sc, zs, pf, dscr_r)
        print(f"✓ Relatório enviado: {ticker} — Score {sc.get('score'):.1f}")
        return True

    except Exception as e:
        print(f"[ERRO] relatorio_empresa_on_demand {ticker}: {e}")
        import traceback
        traceback.print_exc()
        _enviar_telegram(f"❌ Erro ao gerar relatório de <b>{ticker}</b>: {e}")
        return False


def alertar_deterioracao_ativa():
    """
    Ciclo de monitoramento rápido (2h) — verifica apenas se houve mudança
    significativa nos scores sem baixar DFP completo (usa cache).
    Pode ser chamado do ciclo_monitoramento() do market_bot.
    """
    # Verificar histórico recente: se score caiu >2pts nas últimas 4 semanas → re-alertar
    alertas_urgentes = []
    for empresa in WATCHLIST_CREDITO:
        historico = carregar_historico_score(empresa["ticker"], n_ultimos=5)
        if len(historico) < 2:
            continue
        try:
            sc_recente  = float(historico[0].get("score_credito") or 0)
            sc_anterior = float(historico[-1].get("score_credito") or 0)
            if sc_anterior > 0 and (sc_anterior - sc_recente) >= 2.0:
                alertas_urgentes.append(
                    f"🔴 <b>{empresa['ticker']}</b> — Score caiu {sc_anterior:.1f}→{sc_recente:.1f} "
                    f"(Rating: {historico[0].get('rating_br','—')})"
                )
        except (ValueError, TypeError):
            continue

    if alertas_urgentes:
        msg = "⚠️ <b>DETERIORAÇÃO DE CRÉDITO DETECTADA</b>\n\n" + "\n".join(alertas_urgentes)
        msg += "\n\n<i>Use /credito TICKER para análise completa</i>"
        _enviar_telegram(msg)
        print(f"[CRÉDITO] {len(alertas_urgentes)} alertas urgentes enviados")
