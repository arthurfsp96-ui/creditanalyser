#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cvm_dfp.py — Download automático de DFP/ITR da CVM por CNPJ ou Ticker
Integrado ao credito_dashboard (app.py) e ao market_bot (credito.py)

Fonte: Portal Dados Abertos CVM — dados.cvm.gov.br
Arquivos CSV públicos, sem autenticação, atualizados anualmente (DFP) e trimestralmente (ITR)

Contas extraídas por código CVM padrão (COSIF/XBRL BR):
  DRE  → 3.01 Receita Líquida | 3.03 Lucro Bruto | 3.05 EBIT | 3.06 Res.Fin | 3.11 Lucro Líquido
  BPA  → 1 Ativo Total | 1.01 Ativo Circ | 1.02 Ativo N.Circ
  BPP  → 2.01 Passivo Circ | 2.02 Passivo N.Circ | 2.03 PL
  DFC  → 6.01 FCO | 6.02 FCI (CAPEX) | 6.03 FCF
"""

import os, io, csv, re, requests, zipfile
from datetime import datetime

# ── Cache em memória (por sessão de servidor) ─────────────────────────────────
# Evita baixar o CSV inteiro (3-10MB) em cada request
_cvm_dfp_cache = {}   # {(tipo, ano): {cnpj_limpo: {conta: valor}}}
_cvm_cad_cache = {}   # {cnpj_limpo: {cod_cvm, nome_pregao, segmento, ...}}

BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; credito-dashboard/1.0)"}

# Mapeamento conta → campo interno do sistema
CONTA_MAP = {
    # DRE
    "3.01": "receita_liquida",
    "3.03": "lucro_bruto",
    "3.04": "despesas_operacionais",
    "3.05": "ebit",
    "3.06": "resultado_financeiro",
    "3.07": "resultado_antes_ir",
    "3.08": "imposto_renda",
    "3.11": "lucro_liquido",
    # BPA — Ativo
    "1":    "ativo_total",
    "1.01": "ativo_circulante",
    "1.01.01": "caixa_equivalentes",
    "1.01.04": "estoques",
    "1.02": "ativo_nao_circulante",
    "1.02.01": "realizavel_lp",
    "1.02.03": "intangivel",
    # BPP — Passivo + PL
    "2.01": "passivo_circulante",
    "2.01.01.01": "emprestimos_cp",
    "2.02": "passivo_nao_circulante",
    "2.02.01": "emprestimos_lp",
    "2.03": "patrimonio_liquido",
    "2.03.04": "lucros_retidos",
    # DFC
    "6.01": "fco",
    "6.02": "fci",
    "6.03": "fcf",
}

# Contas a somar para calcular dívida bruta (empréstimos CP + LP)
CONTAS_DIVIDA_BRUTA = {"2.01.04", "2.01.05", "2.02.01", "2.02.02",
                        "2.01.01.01", "2.02.01.01"}


def _cnpj_limpo(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj or "")


def _cnpj_mask(cnpj14: str) -> str:
    c = cnpj14.zfill(14)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}"


def _baixar_csv_cvm(url: str, encoding="latin-1") -> list[dict]:
    """Baixa CSV da CVM e retorna lista de dicts."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        if r.status_code != 200:
            return []
        content = r.content.decode(encoding, errors="replace")
        reader = csv.DictReader(io.StringIO(content), delimiter=";")
        return list(reader)
    except Exception as e:
        print(f"[CVM] Erro baixando {url}: {e}")
        return []


def _baixar_zip_cvm(url: str, encoding="latin-1") -> list[dict]:
    """Baixa ZIP da CVM (ITR), extrai CSV e retorna lista de dicts."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return []
        z = zipfile.ZipFile(io.BytesIO(r.content))
        rows = []
        for name in z.namelist():
            if name.endswith(".csv"):
                with z.open(name) as f:
                    content = f.read().decode(encoding, errors="replace")
                    reader = csv.DictReader(io.StringIO(content), delimiter=";")
                    rows.extend(list(reader))
        return rows
    except Exception as e:
        print(f"[CVM] Erro baixando ZIP {url}: {e}")
        return []


def _carregar_cadastro_cvm() -> dict:
    """Carrega cadastro CVM (CNPJ → cod_cvm, nome, segmento) com cache."""
    global _cvm_cad_cache
    if _cvm_cad_cache:
        return _cvm_cad_cache

    url = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
    rows = _baixar_csv_cvm(url)
    for row in rows:
        cnpj = _cnpj_limpo(row.get("CNPJ_CIA", ""))
        if cnpj:
            _cvm_cad_cache[cnpj] = {
                "cod_cvm":      row.get("CD_CVM", "").strip(),
                "nome_social":  row.get("DENOM_SOCIAL", "").strip(),
                "nome_pregao":  row.get("DENOM_COMERC", "").strip(),
                "segmento":     row.get("SEGM", "").strip(),
                "categoria":    row.get("CATEG_REG", "").strip(),
                "situacao_cvm": row.get("SIT", "").strip(),
                "dt_reg":       row.get("DT_REG", "").strip(),
            }
    print(f"[CVM] Cadastro carregado: {len(_cvm_cad_cache)} empresas")
    return _cvm_cad_cache


def _extrair_financeiros_dfp(rows: list[dict], cod_cvm: str, exercicio="ÚLTIMO") -> dict:
    """
    Filtra linhas do CSV pelo cod_cvm e extrai valores financeiros.
    Retorna dict com campos internos do sistema.
    """
    resultado = {}
    divida_bruta_acc = 0.0
    caixa_acc = 0.0

    # Filtrar versão mais recente (maior número de versão)
    empresa_rows = [r for r in rows if r.get("CD_CVM", "").strip() == str(cod_cvm)
                    and r.get("ORDEM_EXERC", "").strip() == exercicio]
    if not empresa_rows:
        return resultado

    # Usar a versão mais recente
    max_versao = max(int(r.get("VERSAO", 0) or 0) for r in empresa_rows)
    empresa_rows = [r for r in empresa_rows if int(r.get("VERSAO", 0) or 0) == max_versao]

    # Escala da moeda (MIL = valores em R$ mil, UNIDADE = R$ unidade)
    escala_str = empresa_rows[0].get("ESCALA_MOEDA", "MIL").strip().upper()
    escala = 1.0  # já em mil (padrão do sistema)
    if escala_str == "UNIDADE":
        escala = 0.001  # converter para mil

    # Data de referência
    resultado["dt_refer"] = empresa_rows[0].get("DT_FIM_EXERC", "").strip()

    for row in empresa_rows:
        conta = row.get("CD_CONTA", "").strip()
        ds    = row.get("DS_CONTA", "").strip()
        try:
            valor = float(row.get("VL_CONTA", "0").replace(",", ".") or 0) * escala
        except (ValueError, AttributeError):
            valor = 0.0

        # Mapear conta → campo interno
        campo = CONTA_MAP.get(conta)
        if campo:
            resultado[campo] = valor

        # Acumular dívida bruta (soma de todas as contas de empréstimos)
        if conta in CONTAS_DIVIDA_BRUTA:
            divida_bruta_acc += valor

        # Caixa e equivalentes (conta 1.01.01 ou similar)
        if conta == "1.01.01":
            caixa_acc = valor

    # Cálculos derivados
    if divida_bruta_acc > 0:
        resultado["divida_bruta"] = divida_bruta_acc

    # Caixa: usar 1.01.01 se disponível, senão uma fração do ativo circulante
    if caixa_acc > 0:
        resultado["caixa"] = caixa_acc
    elif resultado.get("ativo_circulante", 0) > 0:
        resultado["caixa_estimado"] = resultado["ativo_circulante"] * 0.3  # estimativa

    # Dívida líquida = dívida bruta - caixa
    db = resultado.get("divida_bruta", 0)
    cx = resultado.get("caixa", resultado.get("caixa_estimado", 0))
    if db > 0 and cx >= 0:
        resultado["divida_liquida"] = max(0, db - cx)

    # EBITDA estimado = EBIT + D&A (D&A não disponível no DFP padrão, estimamos 10-15% receita)
    # Se não veio direto do CSV, estimamos
    if not resultado.get("ebitda_ajustado") and resultado.get("ebit", 0) > 0:
        rl = resultado.get("receita_liquida", 0)
        # D&A estimado como 7% da receita líquida (conservador)
        da_est = rl * 0.07 if rl > 0 else 0
        resultado["ebitda_ajustado"] = resultado["ebit"] + da_est
        resultado["_ebitda_estimado"] = True

    # Margens
    rl = resultado.get("receita_liquida", 0)
    if rl > 0:
        if resultado.get("lucro_bruto"):
            resultado["margem_bruta"] = round(resultado["lucro_bruto"] / rl * 100, 1)
        eb = resultado.get("ebitda_ajustado", 0)
        if eb:
            resultado["margem_ebitda"] = round(eb / rl * 100, 1)
        ll = resultado.get("lucro_liquido", 0)
        if ll is not None:
            resultado["margem_liquida"] = round(ll / rl * 100, 1)

    # Alavancagem
    eb = resultado.get("ebitda_ajustado", 0)
    dl = resultado.get("divida_liquida", 0)
    if eb and eb > 0 and dl is not None:
        resultado["alavancagem"] = round(dl / eb, 2)

    # ROE e ROA
    pl = resultado.get("patrimonio_liquido", 0)
    at = resultado.get("ativo_total", 0)
    ll = resultado.get("lucro_liquido", 0)
    if pl and pl > 0 and ll:
        resultado["roe"] = round(ll / pl * 100, 1)
    if at and at > 0 and ll:
        resultado["roa"] = round(ll / at * 100, 1)

    resultado["_campos_cvm"] = len([v for v in resultado.values() if v and v != 0])
    resultado["_fonte"] = "CVM_DFP"
    return resultado


def buscar_dfp_por_cnpj(cnpj: str, ano: int = None) -> dict:
    """
    Busca dados financeiros da CVM para uma empresa pelo CNPJ.
    Tenta DFP anual primeiro, depois ITR trimestral mais recente.

    Retorna dict compatível com o parse_dfp() do app.py.
    """
    cnpj_raw = _cnpj_limpo(cnpj)
    if len(cnpj_raw) != 14:
        return {"_erro": "CNPJ inválido"}

    if ano is None:
        ano = datetime.now().year - 1  # Último ano fechado

    # 1. Buscar cod_cvm no cadastro
    cad = _carregar_cadastro_cvm()
    info_empresa = cad.get(cnpj_raw)
    if not info_empresa:
        return {"_erro": "CNPJ não encontrado no cadastro CVM (empresa de capital fechado?)",
                "_cnpj": cnpj_raw}

    cod_cvm = info_empresa["cod_cvm"]
    resultado_base = {
        "nome_cvm":      info_empresa["nome_social"],
        "nome_pregao":   info_empresa["nome_pregao"],
        "segmento_b3":   info_empresa["segmento"],
        "situacao_cvm":  info_empresa["situacao_cvm"],
        "cod_cvm":       cod_cvm,
        "_cnpj":         cnpj_raw,
    }

    print(f"[CVM] Buscando DFP {ano} para {info_empresa['nome_pregao']} (cod_cvm={cod_cvm})")

    # 2. Baixar DFPs (DRE + BPA + BPP + DFC)
    dfp_tipos = {
        "DRE": f"{BASE_URL}/DFP/DADOS/dfp_cia_aberta_DRE_con_{ano}.csv",
        "BPA": f"{BASE_URL}/DFP/DADOS/dfp_cia_aberta_BPA_con_{ano}.csv",
        "BPP": f"{BASE_URL}/DFP/DADOS/dfp_cia_aberta_BPP_con_{ano}.csv",
        "DFC": f"{BASE_URL}/DFP/DADOS/dfp_cia_aberta_DFC_MD_con_{ano}.csv",
    }

    financeiros = {}
    for tipo, url in dfp_tipos.items():
        cache_key = (tipo, ano)
        if cache_key not in _cvm_dfp_cache:
            print(f"[CVM] Baixando {tipo} {ano}...")
            _cvm_dfp_cache[cache_key] = _baixar_csv_cvm(url)
        rows = _cvm_dfp_cache[cache_key]
        dados = _extrair_financeiros_dfp(rows, cod_cvm)
        financeiros.update(dados)

    # 3. Se DFP do ano passado não tem dados, tentar ITR mais recente
    if not financeiros.get("receita_liquida"):
        ano_itr = datetime.now().year
        itr_url = f"{BASE_URL}/ITR/DADOS/itr_cia_aberta_DRE_con_{ano_itr}.csv"
        cache_key = ("ITR_DRE", ano_itr)
        if cache_key not in _cvm_dfp_cache:
            print(f"[CVM] Tentando ITR {ano_itr}...")
            _cvm_dfp_cache[cache_key] = _baixar_csv_cvm(itr_url)
        rows = _cvm_dfp_cache[cache_key]
        dados_itr = _extrair_financeiros_dfp(rows, cod_cvm)
        if dados_itr.get("receita_liquida"):
            financeiros.update(dados_itr)
            financeiros["_fonte"] = "CVM_ITR"
            print(f"[CVM] Dados encontrados no ITR {ano_itr}")

    # 4. Também buscar ano anterior (para Piotroski — comparação)
    if financeiros.get("receita_liquida"):
        for tipo, url_base in [("DRE", "DRE"), ("BPA", "BPA"), ("BPP", "BPP")]:
            cache_key_ant = (f"{tipo}_ANT", ano - 1)
            if cache_key_ant not in _cvm_dfp_cache:
                url_ant = f"{BASE_URL}/DFP/DADOS/dfp_cia_aberta_{url_base}_con_{ano-1}.csv"
                _cvm_dfp_cache[cache_key_ant] = _baixar_csv_cvm(url_ant)
            rows_ant = _cvm_dfp_cache[cache_key_ant]
            dados_ant = _extrair_financeiros_dfp(rows_ant, cod_cvm)
            for k, v in dados_ant.items():
                if not k.startswith("_"):
                    financeiros[f"ant_{k}"] = v

    resultado_base.update(financeiros)
    resultado_base["_ano_dfp"] = ano
    return resultado_base


def buscar_dfp_por_ticker(ticker: str) -> dict:
    """Busca DFP pelo ticker B3 (tenta via mapa CNPJ ou busca no cadastro CVM)."""
    # Importar mapa do app.py (se disponível)
    try:
        from app import _CNPJ_TICKER_MAP
        # Inverter mapa ticker → cnpj
        ticker_cnpj = {v: k for k, v in _CNPJ_TICKER_MAP.items()}
        cnpj = ticker_cnpj.get(ticker.upper())
        if cnpj:
            return buscar_dfp_por_cnpj(cnpj)
    except ImportError:
        pass

    # Fallback: buscar no cadastro CVM pelo nome
    cad = _carregar_cadastro_cvm()
    tk = ticker.upper().replace("3", "").replace("4", "").replace("11", "")
    for cnpj_raw, info in cad.items():
        nome_p = info.get("nome_pregao", "").upper()
        if tk in nome_p or nome_p.startswith(tk[:4]):
            return buscar_dfp_por_cnpj(cnpj_raw)

    return {"_erro": f"Ticker {ticker} não encontrado no cadastro CVM"}


def buscar_risco_cadastral(cnpj: str) -> dict:
    """
    Consolida dados de risco cadastral de fontes GRATUITAS:
    1. BrasilAPI (Receita Federal) — situação, sócios, capital social
    2. CVM cadastro — situação regulatória
    3. Google News — notícias sobre a empresa (protesto, recuperação judicial, falência)
    4. ReceitaWS — fallback da Receita Federal

    Para protestos reais (pago): usar Valida.api.br (~R$0,10/consulta)
    """
    import urllib.request, urllib.parse
    from xml.etree import ElementTree as ET

    cnpj_raw = _cnpj_limpo(cnpj)
    resultado = {
        "cnpj": cnpj_raw,
        "fontes_consultadas": [],
        "alertas_risco": [],
        "score_risco_cadastral": 10,  # 10 = sem risco, 0 = alto risco
    }

    # ── 1. BrasilAPI (Receita Federal) ───────────────────────────────────────
    try:
        r = requests.get(
            f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_raw}",
            headers=HEADERS, timeout=12
        )
        if r.status_code == 200:
            d = r.json()
            sit = d.get("descricao_situacao_cadastral", "").upper()
            resultado["situacao_receita"] = sit
            resultado["razao_social"] = d.get("razao_social", "")
            resultado["nome_fantasia"] = d.get("nome_fantasia", "") or d.get("razao_social", "")
            resultado["data_abertura"] = d.get("data_inicio_atividade", "")
            resultado["capital_social"] = d.get("capital_social", 0)
            resultado["porte"] = d.get("porte", "")
            resultado["municipio"] = d.get("municipio", "")
            resultado["uf"] = d.get("uf", "")
            resultado["cnae_principal"] = d.get("cnae_fiscal_descricao", "")
            qsa = d.get("qsa", [])
            resultado["socios"] = [
                {"nome": s.get("nome_socio", ""), "qualificacao": s.get("qualificacao_socio", "")}
                for s in qsa[:10]
            ]
            resultado["fontes_consultadas"].append("BrasilAPI/ReceitaFederal")

            # Alertas de situação
            if sit and sit not in ("ATIVA", "ATIVO"):
                resultado["alertas_risco"].append(f"⚠ Situação RF: {sit}")
                resultado["score_risco_cadastral"] -= 4

            # Capital muito baixo para empresa de médio/grande porte
            cap = resultado["capital_social"] or 0
            if cap and cap < 10000:
                resultado["alertas_risco"].append(f"⚠ Capital social muito baixo: R$ {cap:,.2f}")
                resultado["score_risco_cadastral"] -= 1
    except Exception as e:
        resultado["_erro_brasilapi"] = str(e)

    # ── 2. CVM Cadastro ───────────────────────────────────────────────────────
    try:
        cad = _carregar_cadastro_cvm()
        if cnpj_raw in cad:
            info = cad[cnpj_raw]
            sit_cvm = info.get("situacao_cvm", "").upper()
            resultado["situacao_cvm"] = sit_cvm
            resultado["segmento_b3"] = info.get("segmento", "")
            resultado["categoria_cvm"] = info.get("categoria", "")
            resultado["fontes_consultadas"].append("CVM/Cadastro")
            if sit_cvm and sit_cvm not in ("A", "ATIVO", "ATIVA", "NORMAL", "FASE_OPERACIONAL"):
                resultado["alertas_risco"].append(f"⚠ Situação CVM: {sit_cvm}")
                resultado["score_risco_cadastral"] -= 3
        else:
            resultado["_aviso_cvm"] = "Empresa não listada na CVM (capital fechado)"
    except Exception as e:
        resultado["_erro_cvm"] = str(e)

    # ── 3. Google News — alertas de risco (GRATUITO) ──────────────────────────
    # Busca notícias com palavras-chave críticas: protesto, recuperação judicial, falência, execução
    try:
        nome_busca = resultado.get("nome_fantasia") or resultado.get("razao_social", "")
        nome_curto = " ".join(nome_busca.split()[:2]) if nome_busca else ""

        termos_risco = [
            f'{nome_curto} recuperação judicial',
            f'{nome_curto} protesto dívida',
            f'{nome_curto} falência concordata',
            f'{nome_curto} execução fiscal',
        ]

        noticias_risco = []
        palavras_alerta = {
            "recuperação judicial": "⚠ Notícias sobre recuperação judicial",
            "recuperacao judicial": "⚠ Notícias sobre recuperação judicial",
            "falência": "⚠ Notícias sobre falência",
            "falencia": "⚠ Notícias sobre falência",
            "protesto": "⚠ Notícias sobre protestos de dívida",
            "execução fiscal": "⚠ Notícias sobre execução fiscal",
            "execucao fiscal": "⚠ Notícias sobre execução fiscal",
            "inadimplência": "⚠ Notícias sobre inadimplência",
            "calote": "⚠ Notícias sobre calote",
            "dívida vencida": "⚠ Notícias sobre dívida vencida",
        }
        alertas_noticias_set = set()

        for termo in termos_risco[:2]:  # Limitar a 2 queries
            try:
                q = urllib.parse.quote(termo)
                url = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    xml = resp.read()
                root = ET.fromstring(xml)
                for item in root.findall(".//item")[:5]:
                    titulo = item.findtext("title", "").lower()
                    pub    = item.findtext("pubDate", "")
                    link   = item.findtext("link", "")
                    titulo_original = item.findtext("title", "")
                    for palavra, alerta in palavras_alerta.items():
                        if palavra in titulo and alerta not in alertas_noticias_set:
                            alertas_noticias_set.add(alerta)
                            resultado["alertas_risco"].append(f"{alerta} (recente)")
                            resultado["score_risco_cadastral"] -= 2
                    if titulo_original:
                        noticias_risco.append({
                            "titulo": titulo_original.split(" - ")[0],
                            "publicado": pub,
                            "link": link,
                        })
            except Exception:
                continue

        resultado["noticias_risco"] = noticias_risco[:8]
        resultado["fontes_consultadas"].append("GoogleNews")
    except Exception as e:
        resultado["noticias_risco"] = []
        resultado["_erro_news"] = str(e)

    # ── 4. DataJud CNJ — Processos Judiciais (GRATUITO, API pública) ──────────
    # A API do CNJ é pública mas requer credenciais para volume alto
    # Aqui fazemos a busca básica pela razão social
    try:
        nome_rf = resultado.get("razao_social", "")
        if nome_rf:
            headers_datajud = {
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
            }
            # API DataJud — endpoint de busca pública (sem autenticação para uso básico)
            payload = {
                "query": {
                    "match": {"partes.nome": nome_rf[:40]}
                },
                "size": 5
            }
            r_jud = requests.post(
                "https://api-publica.datajud.cnj.jus.br/api_publica_tjsp/_search",
                json=payload, headers=headers_datajud, timeout=10
            )
            if r_jud.status_code == 200:
                hits = r_jud.json().get("hits", {}).get("hits", [])
                processos = []
                for h in hits[:5]:
                    src = h.get("_source", {})
                    classe = src.get("classe", {}).get("nome", "")
                    assuntos = [a.get("nome", "") for a in src.get("assuntos", [])[:2]]
                    tribunal = src.get("tribunal", "")
                    processos.append({
                        "numero": src.get("numeroProcesso", ""),
                        "classe": classe,
                        "assuntos": assuntos,
                        "tribunal": tribunal,
                        "data_ajuizamento": src.get("dataAjuizamento", ""),
                        "ultima_mov": src.get("dataUltimaAtualizacao", ""),
                    })
                resultado["processos_judiciais"] = processos
                resultado["fontes_consultadas"].append("DataJud/CNJ")

                # Alertas por tipo de processo
                for p in processos:
                    classe_lower = p.get("classe", "").lower()
                    if any(w in classe_lower for w in ["falência", "recuperação", "insolvência", "execução"]):
                        resultado["alertas_risco"].append(
                            f"⚠ Processo judicial: {p.get('classe')} ({p.get('tribunal')})"
                        )
                        resultado["score_risco_cadastral"] -= 3
            elif r_jud.status_code == 401:
                resultado["_aviso_datajud"] = "DataJud requer credencial para buscas avançadas"
    except Exception as e:
        resultado["processos_judiciais"] = []
        resultado["_aviso_datajud"] = str(e)

    # Score final (mínimo 0, máximo 10)
    resultado["score_risco_cadastral"] = max(0, min(10, resultado["score_risco_cadastral"]))

    # Classificação
    sc = resultado["score_risco_cadastral"]
    resultado["nivel_risco_cadastral"] = (
        "BAIXO" if sc >= 8 else
        "MÉDIO" if sc >= 5 else
        "ALTO"  if sc >= 2 else
        "CRÍTICO"
    )

    return resultado
