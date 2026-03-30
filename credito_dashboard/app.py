#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dashboard de Análise de Crédito — Backend Flask
Extrai dados de DFP/FRE e gera relatório PDF institucional
"""

import os, io, re, json
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
import pdfplumber

# ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.lib.colors import HexColor

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 150 * 1024 * 1024

# ─── CORES ─────────────────────────────────────────────────────────────────────
NAVY       = HexColor('#0A1F44')
STEEL      = HexColor('#1D4E89')
ACCENT     = HexColor('#C8102E')
GOLD       = HexColor('#F4A22D')
GRAY_BG    = HexColor('#F4F5F7')
GRAY_DARK  = HexColor('#1E293B')
GRAY_MID   = HexColor('#64748B')
GRAY_LIGHT = HexColor('#E2E8F0')
GREEN_POS  = HexColor('#15803D')
RED_NEG    = HexColor('#DC2626')
ORANGE     = HexColor('#D97706')
PAGE_W, PAGE_H = A4
ML = 2.0*cm; MR = 2.0*cm; MT = 2.2*cm; MB = 2.2*cm
W = PAGE_W - ML - MR

# ─── EXTRAÇÃO ──────────────────────────────────────────────────────────────────
def extract_text_pdf(file_obj):
    txt = []
    with pdfplumber.open(file_obj) as pdf:
        for pg in pdf.pages:
            t = pg.extract_text()
            if t: txt.append(t)
    return '\n'.join(txt)

def sf(s):
    if not s: return None
    s = str(s).replace('.','').replace(',','.').replace('(', '-').replace(')','').strip()
    try: return float(s)
    except: return None

def fv(text, patterns, default=None):
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE | re.MULTILINE)
        if m:
            v = sf(m.group(1))
            if v is not None: return v
    return default

def parse_dfp(text):
    """Extrai dados de DFP/ITR/Release de resultados.
    Suporta formatos CVM, tabular e narrativo em português e inglês.
    Campos não encontrados retornam None (sem defaults de outras empresas).
    """
    d = {}
    if not text or len(text) < 50:
        return d

    # Receita Líquida
    d['receita_liquida'] = fv(text, [
        r'Receita\s+[Ll]íquida\s+de\s+[Vv]endas[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
        r'Receita\s+[Ll]íquida[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
        r'Receita\s+Operacional\s+Líquida[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
        r'Net\s+Revenue[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
    ])

    # Lucro Bruto
    d['lucro_bruto'] = fv(text, [
        r'Lucro\s+Bruto[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Gross\s+Profit[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])

    # Resultado Financeiro
    d['resultado_financeiro'] = fv(text, [
        r'Resultado\s+Financeiro[^\d]*(-?\d{1,3}(?:\.\d{3})*,\d+)',
        r'Receitas?\s+\(Despesas?\)\s+Financeiras?[^\d]*(-?\d{1,3}(?:\.\d{3})*,\d+)',
    ])

    # Lucro/Prejuízo Líquido
    ll_v = fv(text, [
        r'Lucro\s+(?:Líquido|Liquido)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Prejuízo\s+(?:Líquido|Liquido)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Net\s+(?:Income|Profit|Loss)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    if ll_v and re.search(r'[Pp]rejuízo', text[:2000]):
        ll_v = -abs(ll_v)
    d['lucro_liquido'] = ll_v

    # EBITDA — várias formas
    eb = None
    m = re.search(r'EBITDA\s*(?:Ajustado)?[^\d]*R\$\s*([\d,\.]+)\s*bilh', text, re.IGNORECASE)
    if m:
        v = sf(m.group(1))
        eb = v*1000 if v and v < 500 else v
    if not eb:
        eb = fv(text, [
            r'EBITDA\s+(?:Ajustado|Ajust\.?)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
            r'EBITDA[^\n\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        ])
    d['ebitda_ajustado'] = eb

    # Margens calculadas
    if d.get('ebitda_ajustado') and d.get('receita_liquida') and d['receita_liquida'] > 0:
        d['margem_ebitda'] = round(d['ebitda_ajustado'] / d['receita_liquida'] * 100, 1)
    if d.get('lucro_bruto') and d.get('receita_liquida') and d['receita_liquida'] > 0:
        d['margem_bruta'] = round(d['lucro_bruto'] / d['receita_liquida'] * 100, 1)

    # Dívida e caixa
    d['divida_liquida'] = fv(text, [
        r'[Dd]ívida\s+[Ll]íquida[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Net\s+Debt[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    d['divida_bruta'] = fv(text, [
        r'[Dd]ívida\s+[Bb]ruta[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'[Ee]mpréstimos\s+e\s+[Ff]inanciamentos[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    d['caixa'] = fv(text, [
        r'[Cc]aixa\s+e\s+[Ee]quivalentes[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'[Cc]aixa\s+[Gg]erencial[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Cash\s+and\s+[Ee]quivalents[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    if d.get('caixa') and d['caixa'] < 200: d['caixa'] *= 1000

    # Balanço
    d['patrimonio_liquido'] = fv(text, [
        r'[Pp]atrimônio\s+[Ll]íquido[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Total\s+do\s+[Pp]atrimônio[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    d['ativo_total'] = fv(text, [
        r'Total\s+do\s+[Aa]tivo[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Total\s+[Aa]ssets[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    d['passivo_circulante'] = fv(text, [
        r'[Pp]assivo\s+[Cc]irculante[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Current\s+[Ll]iabilities[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    d['ativo_circulante'] = fv(text, [
        r'[Aa]tivo\s+[Cc]irculante[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Current\s+[Aa]ssets[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])

    # Alavancagem
    if d.get('divida_liquida') and d.get('ebitda_ajustado') and d['ebitda_ajustado'] > 0:
        d['alavancagem'] = round(d['divida_liquida'] / d['ebitda_ajustado'], 2)
    else:
        d['alavancagem'] = fv(text, [
            r'(\d+[,\.]\d+)\s*[xX]\s*(?:DL/EBITDA|Dívida)',
            r'(?:DL/EBITDA|alavancagem)[^\d]*(\d+[,\.]\d+)',
        ])

    # Fluxo de Caixa
    d['fco'] = fv(text, [
        r'[Ff]luxo\s+de\s+[Cc]aixa\s+(?:das\s+)?[Oo]pera[^\d]*(-?\d{1,3}(?:\.\d{3})*,\d+)',
        r'(?:FCO|FCF)[^\d]*(-?\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    d['capex'] = fv(text, [
        r'[Cc]apex[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'[Ii]nvestimentos[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Capital\s+[Ee]xpenditures?[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    if d.get('capex') and d['capex'] < 100: d['capex'] *= 1000
    d['juros_pagos'] = fv(text, [
        r'[Jj]uros\s+(?:pagos|incorridos)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'[Ee]ncargos\s+[Ff]inanceiros[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    d['resultado_fin_bruto'] = fv(text, [
        r'[Dd]espesas?\s+[Ff]inanceiras[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    # Lucros/Prejuízos acumulados (necessário para Altman Z e Piotroski)
    d['lucros_retidos'] = fv(text, [
        r'[Ll]ucros\s+(?:[Aa]cumulados|[Rr]etidos)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'[Pp]rejuízos?\s+[Aa]cumulados[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Reservas\s+de\s+[Ll]ucros[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    # Ativo não circulante (Altman)
    d['ativo_nao_circulante'] = fv(text, [
        r'[Aa]tivo\s+[Nn]ão[\s-][Cc]irculante[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Non[\s-][Cc]urrent\s+[Aa]ssets[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    # Passivo não circulante (Piotroski — leverage)
    d['passivo_nao_circulante'] = fv(text, [
        r'[Pp]assivo\s+[Nn]ão[\s-][Cc]irculante[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Non[\s-][Cc]urrent\s+[Ll]iabilities[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    # Custo dos Produtos Vendidos (Piotroski — gross margin)
    d['cogs'] = fv(text, [
        r'Custo\s+(?:dos\s+)?(?:Produtos?\s+Vendidos?|das\s+Mercadorias?|dos\s+Serviços?)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Cost\s+of\s+(?:Revenue|Goods\s+Sold)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'CPV[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    # Ações em circulação
    d['acoes_emitidas'] = fv(text, [
        r'[Aa]ções?\s+[Ee]mitidas?[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
        r'[Ss]hares?\s+[Oo]utstanding[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
        r'[Qq]uantidade\s+de\s+[Aa]ções?[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
    ])
    # Dividendos pagos (sustentabilidade)
    d['dividendos_pagos'] = fv(text, [
        r'[Dd]ividendos\s+(?:[Pp]agos?|[Dd]istribuídos?)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'[Pp]agamento\s+de\s+[Dd]ividendos[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Dividends\s+[Pp]aid[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    # Depreciação e amortização (para EBITDA estimado se não explícito)
    d['depreciacao'] = fv(text, [
        r'[Dd]epreciação\s+e\s+[Aa]mortização[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'D&A[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Depreciation[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    # EBIT (Resultado Operacional)
    d['ebit'] = fv(text, [
        r'(?:Resultado|Lucro)\s+(?:Antes\s+de\s+)?(?:IR|Juros|LAJIR|Operacional)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'EBIT[^DA][^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'Operating\s+(?:Income|Profit)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])
    # Se EBIT calculável e EBITDA não encontrado, estimar
    if not d.get('ebitda_ajustado') and d.get('ebit') and d.get('depreciacao'):
        d['ebitda_ajustado'] = (d['ebit'] or 0) + (d['depreciacao'] or 0)
        if d.get('receita_liquida') and d['receita_liquida'] > 0:
            d['margem_ebitda'] = round(d['ebitda_ajustado'] / d['receita_liquida'] * 100, 1)

    d['volume_mineracao_mt'] = 0
    d['_campos_extraidos'] = sum(1 for v in d.values() if v is not None and str(v) not in ('0',''))
    return d

def parse_fre(text):
    """Extrai dados do FRE. Vencimentos ficam vazios se não encontrados."""
    d = {}
    if not text or len(text) < 100:
        d['vencimentos'] = {}
        d['covenants_ok'] = True
        return d

    # Tentar extrair dívida bruta
    d['divida_bruta'] = fv(text, [
        r'[Dd]ívida\s+[Bb]ruta[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
        r'[Ee]mpréstimos.*?total[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)',
    ])

    # Composição por moeda
    d['divida_me_pct']  = fv(text, [r'[Mm]oeda\s+[Ee]strangeira[^\d]*(\d{1,2}[,\.]\d+)\s*%'])
    d['divida_brl_pct'] = fv(text, [r'[Rr]eais[^\d]*(\d{1,2}[,\.]\d+)\s*%'])
    d['taxa_usd']       = fv(text, [r'USD.*?(\d{1,2}[,\.]\d+)\s*%\s*a\.a'])
    d['taxa_brl']       = fv(text, [r'CDI.*?(\d{2,3}[,\.]\d+)\s*%\s*a\.a'])
    d['taxa_eur']       = fv(text, [r'EUR.*?(\d{1,2}[,\.]\d+)\s*%\s*a\.a'])
    d['contingencias']  = fv(text, [r'[Cc]ontingências\s+[Pp]ossíveis[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)'])
    d['covenants_ok']   = True

    # Vencimentos — tentar extrair da tabela do FRE
    venc = {}
    for ano in ['2026','2027','2028','2029','2030','2031']:
        v = fv(text, [rf'{ano}[^\d]*(\d{{1,3}}(?:\.\d{{3}})*,\d+)'])
        if v: venc[ano] = v
    d['vencimentos'] = venc if venc else {}
    return d

# ─── ESTILOS PDF ────────────────────────────────────────────────────────────────
def build_styles():
    s = {}
    s['body']      = ParagraphStyle('body', fontName='Helvetica', fontSize=8.8,
                        textColor=GRAY_DARK, leading=13.5, spaceAfter=4, alignment=TA_JUSTIFY)
    s['sh']        = ParagraphStyle('sh', fontName='Helvetica-Bold', fontSize=12.5,
                        textColor=NAVY, leading=17, spaceBefore=10, spaceAfter=4)
    s['ssh']       = ParagraphStyle('ssh', fontName='Helvetica-Bold', fontSize=9.5,
                        textColor=STEEL, leading=13, spaceBefore=8, spaceAfter=3)
    s['th']        = ParagraphStyle('th', fontName='Helvetica-Bold', fontSize=7.8,
                        textColor=colors.white, leading=10, alignment=TA_CENTER)
    s['tc']        = ParagraphStyle('tc', fontName='Helvetica', fontSize=7.8,
                        textColor=GRAY_DARK, leading=10, alignment=TA_RIGHT)
    s['tl']        = ParagraphStyle('tl', fontName='Helvetica', fontSize=7.8,
                        textColor=GRAY_DARK, leading=10, alignment=TA_LEFT)
    s['tlb']       = ParagraphStyle('tlb', fontName='Helvetica-Bold', fontSize=7.8,
                        textColor=GRAY_DARK, leading=10, alignment=TA_LEFT)
    s['cap']       = ParagraphStyle('cap', fontName='Helvetica', fontSize=7,
                        textColor=GRAY_MID, leading=9.5, alignment=TA_LEFT, spaceAfter=3)
    return s

def P(t, sty): return Paragraph(t, sty)
def SP(h=3): return Spacer(1, h*mm)
def HR(): return HRFlowable(width='100%', thickness=1.2, color=NAVY, spaceAfter=4, spaceBefore=1)

def tbl(data, widths, extra_cmds=None):
    t = Table(data, colWidths=widths)
    cmds = [
        ('BACKGROUND', (0,0), (-1,0), NAVY),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 7.8),
        ('GRID', (0,0), (-1,-1), 0.25, GRAY_LIGHT),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, HexColor('#F8FAFC')]),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('ALIGN', (0,0), (0,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
    ]
    if extra_cmds: cmds.extend(extra_cmds)
    t.setStyle(TableStyle(cmds))
    return t

def make_hf(macro):
    _nome = macro.get("empresa_nome", "Empresa")
    _tk   = macro.get("empresa_ticker", "TICK3")
    def hf(canvas, doc):
        canvas.saveState()
        if doc.page == 1:
            canvas.restoreState()
            return
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H-1.0*cm, PAGE_W, 1.0*cm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont('Helvetica-Bold', 7.5)
        canvas.drawString(ML, PAGE_H-0.65*cm, f"{_nome.upper()} — ANÁLISE DE CRÉDITO  |  CONFIDENCIAL")
        canvas.setFont('Helvetica', 7)
        canvas.drawRightString(PAGE_W-MR, PAGE_H-0.65*cm, f"{_tk} | Data-base: {macro.get('empresa_database','31/12/2025')}")
        canvas.setFillColor(GRAY_BG)
        canvas.rect(0, 0, PAGE_W, 0.85*cm, fill=1, stroke=0)
        canvas.setFillColor(GRAY_MID)
        canvas.setFont('Helvetica', 6.5)
        canvas.drawString(ML, 0.3*cm, 'Documento gerado automaticamente. Não constitui recomendação formal de investimento.')
        canvas.setFillColor(NAVY)
        canvas.setFont('Helvetica-Bold', 7.5)
        canvas.drawRightString(PAGE_W-MR, 0.3*cm, f'{doc.page}')
        canvas.restoreState()
    return hf


def draw_cover(canvas, doc, dfp, fre, macro):
    """Capa do relatório — layout fixo sem sobreposição."""
    canvas.saveState()

    # ── Variáveis locais
    eb    = dfp.get('ebitda_ajustado') or 0
    dl    = dfp.get('divida_liquida') or 0
    rl    = dfp.get('receita_liquida') or 0
    caixa = dfp.get('caixa') or 0
    ll    = dfp.get('lucro_liquido') or 0
    mg_eb = dfp.get('margem_ebitda') or (round(eb/rl*100,1) if rl else 0)
    alav  = dfp.get('alavancagem') or (round(dl/eb,2) if eb > 0 else 0)
    venc_cp = (fre.get('vencimentos') or {}).get('2026') or 0

    # ── Blocos de fundo
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H*0.52, PAGE_W, PAGE_H*0.48, fill=1, stroke=0)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_H*0.52, PAGE_W, 0.3*cm, fill=1, stroke=0)
    canvas.setFillColor(HexColor('#F8FAFC'))
    canvas.rect(0, 0, PAGE_W, PAGE_H*0.52, fill=1, stroke=0)

    # ── Área superior — identidade
    canvas.setFillColor(HexColor('#64748B'))
    canvas.setFont('Helvetica', 8)
    canvas.drawString(ML, PAGE_H*0.92, 'ANÁLISE DE CRÉDITO CORPORATIVO — CONFIDENCIAL')

    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica-Bold', 22)
    # Nome da empresa — truncar se muito longo
    nm = macro.get('empresa_nome','Empresa')
    canvas.drawString(ML, PAGE_H*0.84, nm[:45])

    canvas.setFont('Helvetica', 9)
    canvas.setFillColor(HexColor('#7DD3FC'))
    tk = macro.get('empresa_ticker','')
    seg = macro.get('empresa_segmentos','')
    canvas.drawString(ML, PAGE_H*0.78, f"{tk}  ·  B3" + (f"   |   {seg[:60]}" if seg else ''))

    canvas.setFillColor(HexColor('#94A3B8'))
    canvas.setFont('Helvetica', 7.5)
    canvas.drawString(ML, PAGE_H*0.74,
        f"Data-base: {macro.get('empresa_database','')}   |   "
        f"USD/BRL: R$ {macro.get('usd_brl',5.80):.2f}   |   "
        f"Selic: {macro.get('selic',14.75):.2f}%   |   "
        f"IPCA: {macro.get('ipca',5.0):.2f}%")

    # ── 4 badges no limite superior/inferior
    by = PAGE_H*0.535
    bw, bh = 104, 38
    gap = (PAGE_W - ML - MR - 4*bw) / 3

    badges = [
        (ACCENT,       colors.white, 'RECOMENDAÇÃO',    macro.get('recomendacao','—'),     ''),
        (STEEL,        colors.white, 'RATING GLOBAL',   macro.get('rating','—'),           'Estimado'),
        (ORANGE if alav > 3 else GREEN_POS, colors.white, 'DL / EBITDA',
         f'{alav:.2f}x' if alav else '—', 'Alavancagem'),
        (GOLD,         NAVY,         'SPREAD ALVO',
         f"{macro.get('spread_alvo',425)} bps",
         f"Yield ~{macro.get('treasury_10y',4.3)+macro.get('spread_alvo',425)/100:.2f}% USD"),
    ]
    for i, (bg, fg, lbl, val, sub) in enumerate(badges):
        x = ML + i*(bw + gap)
        canvas.setFillColor(bg)
        canvas.roundRect(x, by, bw, bh, 4, fill=1, stroke=0)
        canvas.setFillColor(fg)
        canvas.setFont('Helvetica', 6.5)
        canvas.drawCentredString(x+bw/2, by+29, lbl)
        canvas.setFont('Helvetica-Bold', 14)
        canvas.drawCentredString(x+bw/2, by+16, str(val)[:12])
        if sub:
            canvas.setFont('Helvetica', 6)
            canvas.drawCentredString(x+bw/2, by+6, sub[:20])

    # ── KPI strip (6 boxes)
    ky = PAGE_H*0.415
    kpis = [
        (f"R$ {rl/1000:.1f}bi"    if rl    else '—', 'Receita Líquida',  STEEL),
        (f"R$ {eb/1000:.1f}bi"    if eb    else '—', 'EBITDA Ajustado',  GREEN_POS),
        (f"{mg_eb:.1f}%"          if mg_eb else '—', 'Mg. EBITDA',       GREEN_POS),
        (f"R$ {dl/1000:.1f}bi"    if dl    else '—', 'Dívida Líquida',   ORANGE),
        (f"R$ {caixa/1000:.1f}bi" if caixa else '—', 'Caixa',            STEEL),
        (f"R$ {ll/1000:.1f}bi"    if ll    else '—', 'Resultado Líq.',   RED_NEG if ll < 0 else GREEN_POS),
    ]
    kw2 = (PAGE_W - ML - MR - 5) / 6
    for i, (val, lbl, col) in enumerate(kpis):
        x = ML + i*(kw2+1)
        canvas.setFillColor(colors.white)
        canvas.rect(x, ky, kw2, 30, fill=1, stroke=0)
        canvas.setFillColor(col)
        canvas.rect(x, ky+27, kw2, 3, fill=1, stroke=0)
        canvas.setFillColor(col)
        canvas.setFont('Helvetica-Bold', 9.5)
        canvas.drawCentredString(x+kw2/2, ky+14, val)
        canvas.setFillColor(GRAY_MID)
        canvas.setFont('Helvetica', 6)
        canvas.drawCentredString(x+kw2/2, ky+5, lbl)

    # ── Tese resumida
    tese = macro.get('tese_resumo','')
    if not tese:
        tese = f"{nm}"
        if eb:    tese += f" — EBITDA R$ {eb/1000:.1f}bi (mg. {mg_eb:.1f}%)."
        if alav:  tese += f" Alavancagem {alav:.2f}x."
        if caixa: tese += f" Caixa R$ {caixa/1000:.1f}bi" + (f", cobre {caixa/venc_cp*100:.0f}% do venc. CP." if venc_cp > 0 else ".")
        if not any([eb, rl, dl]): tese = f"{nm} — preencha os dados financeiros no dashboard para análise completa."

    canvas.setFillColor(NAVY)
    canvas.setFont('Helvetica-Bold', 7.5)
    canvas.drawString(ML, PAGE_H*0.37, '▌ TESE')
    canvas.setFillColor(GRAY_DARK)
    canvas.setFont('Helvetica', 7.5)
    # Quebrar tese em no máximo 3 linhas
    words = tese.split()
    lines_tese = []
    cur = ''
    max_w = PAGE_W - ML - MR
    for w in words:
        test = (cur + ' ' + w).strip()
        if canvas.stringWidth(test, 'Helvetica', 7.5) < max_w:
            cur = test
        else:
            if cur: lines_tese.append(cur)
            cur = w
        if len(lines_tese) >= 3: break
    if cur and len(lines_tese) < 3: lines_tese.append(cur)
    for j, ln in enumerate(lines_tese[:3]):
        canvas.drawString(ML, PAGE_H*0.355 - j*10, ln)

    # ── Premissas (linha única)
    canvas.setStrokeColor(GRAY_LIGHT)
    canvas.setLineWidth(0.4)
    canvas.line(ML, PAGE_H*0.29, PAGE_W-MR, PAGE_H*0.29)
    canvas.setFillColor(NAVY)
    canvas.setFont('Helvetica-Bold', 6.5)
    canvas.drawString(ML, PAGE_H*0.275, 'PREMISSAS UTILIZADAS:')
    mac_items = [
        f"Selic {macro.get('selic',14.75):.2f}%",
        f"IPCA {macro.get('ipca',5.0):.2f}%",
        f"CDI {macro.get('cdi',14.65):.2f}%",
        f"USD/BRL R$ {macro.get('usd_brl',5.80):.2f}",
        f"Treasury 10Y {macro.get('treasury_10y',4.30):.2f}%",
        f"Spread {macro.get('spread_alvo',425)} bps",
    ]
    canvas.setFillColor(GRAY_DARK)
    canvas.setFont('Helvetica', 6.5)
    sp = (PAGE_W - ML - MR - 80) / (len(mac_items)-1)
    for i, mt in enumerate(mac_items):
        canvas.drawString(ML + 80 + i*sp if i > 0 else ML+80, PAGE_H*0.275, mt)

    # ── Disclaimer
    canvas.setFillColor(GRAY_MID)
    canvas.setFont('Helvetica', 6)
    canvas.drawCentredString(PAGE_W/2, 0.9*cm,
        f"Relatório gerado em {macro.get('empresa_ticker','—')} — {macro.get('empresa_database','')} | "
        "Não constitui oferta ou recomendação formal de investimento.")
    canvas.restoreState()


def gerar_pdf(dfp, fre, macro):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=ML, rightMargin=MR,
        topMargin=MT+1.0*cm, bottomMargin=MB+0.85*cm)

    s = build_styles()

    # Vars comuns
    rl    = dfp.get('receita_liquida') or 0
    eb    = dfp.get('ebitda_ajustado') or 0
    mg_eb = dfp.get('margem_ebitda') or (round(eb/rl*100,1) if rl else 0)
    ll    = dfp.get('lucro_liquido') or 0
    dl    = dfp.get('divida_liquida') or 0
    alav  = dfp.get('alavancagem') or (round(dl/eb,2) if eb else 0)
    caixa = dfp.get('caixa') or 0
    fco   = dfp.get('fco') or 0
    capex = dfp.get('capex') or 0
    db    = fre.get('divida_bruta') or 0
    venc  = fre.get('vencimentos') or {}
    venc_2026 = venc.get('2026') or 0
    juros = dfp.get('juros_pagos') or 0
    rf    = dfp.get('resultado_financeiro') or 0
    icj   = round(eb/juros, 2) if juros else 0
    pl    = dfp.get('patrimonio_liquido') or dfp.get('pl') or 0

    # Parâmetros macro
    selic     = macro.get('selic', 14.75)
    usd_brl   = macro.get('usd_brl', 5.80)
    ipca      = macro.get('ipca', 5.0)
    cdi       = macro.get('cdi', 14.65)
    minerio   = macro.get('minerio_fe', 102)
    hrc       = macro.get('hrc', 575)
    t10y      = macro.get('treasury_10y', 4.30)

    story = []

    # ────────────────────────────────────────────────────────────────────
    # 1. SUMÁRIO EXECUTIVO
    # ────────────────────────────────────────────────────────────────────
    story += [P('1. SUMÁRIO EXECUTIVO', s['sh']), HR()]
    _tese_usr = macro.get('tese_resumo','')
    _nm_sum = macro.get('empresa_nome','A empresa')
    _db_str = macro.get('empresa_database','')
    if _tese_usr:
        _txt_sum = _tese_usr
    else:
        _txt_sum = f'{_nm_sum}'
        if _db_str: _txt_sum += f' — data-base {_db_str}.'
        if eb:  _txt_sum += f' EBITDA Ajustado de R$ {eb/1000:.1f}bi (margem {mg_eb:.1f}%).'
        if rl:  _txt_sum += f' Receita Líquida de R$ {rl/1000:.1f}bi.'
        if alav: _txt_sum += f' Alavancagem de {alav:.2f}x DL/EBITDA.'
        if caixa: _txt_sum += f' Caixa de R$ {caixa/1000:.1f}bi' + (f', cobrindo {caixa/venc_2026*100:.0f}% do vencimento CP.' if venc_2026 > 0 else '.')
        if fco and fco < 0: _txt_sum += f' FCO: R$ {fco/1000:.1f}bi.'
        if not any([eb,rl,alav]): _txt_sum += ' Dados financeiros não preenchidos — carregue o DFP/ITR para análise completa.'
    story.append(P(_txt_sum, s['body']))
    story.append(SP(4))

    # Tabela KPI
    _ano_r = macro.get('empresa_database','').split('/')[-1] if '/' in macro.get('empresa_database','') else macro.get('empresa_database','2025')
    kpi_hdr = [P('Indicador', s['th']), P(f'{_ano_r}A', s['th']),
               P('Benchmark Setorial', s['th']), P('Situação', s['th'])]
    # Benchmarks genéricos por setor
    _st = macro.get('setor','')
    _alav_bm  = '< 2,5x' if _st in ('energia','logistica') else ('< 1,5x' if _st == 'bancos' else '< 3,0x')
    _icj_bm   = '> 3,0x' if _st in ('energia','logistica') else '> 2,5x'
    _mg_bm    = '> 40%' if _st == 'petroleo' else ('> 30%' if _st in ('energia','papel') else '> 20%')
    _fco_bm   = '> 0'
    kpi_rows = [
        ['Receita Líquida (R$ bi)',    f'{rl/1000:.1f}' if rl else '—',   '—',     '—'],
        ['EBITDA Ajustado (R$ bi)',    f'{eb/1000:.1f}' if eb else '—',   '—',     '—'],
        ['Margem EBITDA (%)',          f'{mg_eb:.1f}%' if mg_eb else '—', _mg_bm,  '✓ OK' if mg_eb and mg_eb > 20 else ('⚠' if mg_eb else '—')],
        ['DL / EBITDA (x)',            f'{alav:.2f}x' if alav else '—',   _alav_bm,'✓ OK' if alav and alav < 3.0 else ('⚠' if alav else '—')],
        ['ICJ — EBITDA/Juros (x)',     f'{icj:.2f}x' if icj else '—',    _icj_bm, '✓ OK' if icj and icj > 2.5 else ('⚠' if icj else '—')],
        ['FCO (R$ bi)', f'{fco/1000:.1f}' if fco else '—', _fco_bm, '✓ OK' if fco and fco > 0 else ('⚠ Negativo' if fco else '—')],
        ['Caixa / Dívida CP (%)', f'{caixa/venc_2026*100:.0f}%' if (caixa and venc_2026 > 0) else '—', '> 100%', '✓ OK' if caixa and venc_2026 > 0 and caixa/venc_2026 > 1 else '⚠'],
        ['Lucro (Prejuízo) Líquido (R$ bi)', f'{ll/1000:.1f}' if ll else '—', '—', '✓' if ll and ll > 0 else ('⚠' if ll else '—')],
    ]
    rc = []
    for i, r in enumerate(kpi_rows):
        sit = r[3]  # coluna Situação (índice 3)
        row_i = i+1
        if '✓' in sit:
            rc.append(('BACKGROUND', (3,row_i), (3,row_i), HexColor('#DCFCE7')))
            rc.append(('TEXTCOLOR', (3,row_i), (3,row_i), GREEN_POS))
        elif '⚠' in sit:
            rc.append(('BACKGROUND', (3,row_i), (3,row_i), HexColor('#FEE2E2')))
            rc.append(('TEXTCOLOR', (3,row_i), (3,row_i), RED_NEG))
        rc.append(('FONTNAME', (3,row_i), (3,row_i), 'Helvetica-Bold'))

    _ano_ref_kd = macro.get('empresa_database','').split('/')[-1] if '/' in macro.get('empresa_database','') else macro.get('empresa_database','')
    kd = [kpi_hdr] + [[P(r[j], s['tl'] if j==0 else s['tc']) for j in range(4)] for r in kpi_rows]
    story.append(tbl(kd, [W*0.34, W*0.18, W*0.22, W*0.26], rc))
    story.append(P(f'Fonte: documentos enviados pelo usuário. Data-base: {macro.get("empresa_database","informada")}.', s['cap']))
    story.append(SP(5))

    # ────────────────────────────────────────────────────────────────────
    # 2. PREMISSAS MACROECONÔMICAS
    # ────────────────────────────────────────────────────────────────────
    story += [P('2. PREMISSAS MACROECONÔMICAS (INSERIDAS PELO USUÁRIO)', s['sh']), HR()]
    story.append(P(
        'As premissas abaixo foram inseridas manualmente pelo usuário no dashboard antes da geração deste relatório. '
        'Todas as projeções, análises de sensibilidade e cenários presentes neste documento utilizam esses valores como base.', s['body']))
    story.append(SP(3))

    mac_hdr = [P('Variável', s['th']), P('Valor Utilizado', s['th']),
               P(f"Impacto em {macro.get('empresa_nome','Empresa')[:20]}", s['th']), P('Sensibilidade', s['th'])]
    _setor_mac = macro.get('setor','')
    _pct_me = fre.get('divida_me_pct') or 0
    _pct_brl = fre.get('divida_brl_pct') or (100 - _pct_me)
    divida_cdi = db * (_pct_brl/100) if db > 0 else 0
    imp_selic = divida_cdi * 0.01
    imp_cam = db * (_pct_me/100) * 0.01 if db > 0 else 0
    imp_ebitda_1pct = eb * 0.01 if eb > 0 else 0

    mac_rows = [
        ['USD / BRL (câmbio)', f'R$ {usd_brl:.2f}',
         f'Dívida ME ({_pct_me:.0f}% do total)' if _pct_me > 0 else 'Câmbio de referência',
         f'R$1,00 = ±R$ {imp_cam/1000:.1f}bi na dívida' if imp_cam > 0 else 'Sem dívida ME'],
        ['Selic (% a.a.)', f'{selic:.2f}%',
         f'Custo dívida BRL ({_pct_brl:.0f}% flutuante)' if divida_cdi > 0 else 'Taxa de referência BR',
         f'+1 p.p. = ±R$ {imp_selic:,.0f}mi desp. fin.' if imp_selic > 0 else 'Referência CDI'],
        ['IPCA (% a.a.)', f'{ipca:.2f}%',
         'Contratos indexados, reajustes de receita',
         '+1 p.p. = pressão em custos e receitas'],
        ['CDI (% a.a.)', f'{cdi:.2f}%',
         'Custo dívida CDI + e aplicações financeiras',
         f'Proxy Selic — spread atual: {(selic-cdi)*100:.0f} bps'],
        ['Treasury 10Y EUA (%)', f'{t10y:.2f}%',
         f"Benchmark dívida USD — {macro.get('empresa_ticker','')}",
         f'Yield alvo: {t10y+macro.get("spread_alvo",425)/100:.2f}% (spread {macro.get("spread_alvo",425)} bps)'],
    ]
    # Adicionar linha setorial relevante
    if _setor_mac in ('mineracao','siderurgia'):
        mac_rows.insert(4, ['Minério Fe 62% (US$/t)', f'US$ {minerio:.0f}',
            'Principal driver de receita e EBITDA', f'US$10/t = ±{imp_ebitda_1pct/eb*100*0.41:.1f}% EBITDA' if eb > 0 else '—'])
    elif _setor_mac == 'petroleo':
        mac_rows.insert(4, ['Petróleo Brent (US$/bbl)', f'US$ {macro.get("brent",78):.0f}',
            'Principal driver de receita E&P', 'US$10/bbl = ±impacto direto na receita'])
    elif _setor_mac == 'agro':
        mac_rows.insert(4, ['Commodities Agrícolas', 'Var.',
            'Commodities agrícolas impactam receita e margens', 'Alta correlação com câmbio e ciclo global'])
    md = [mac_hdr] + [[P(r[j], s['tl'] if j==0 else (s['tlb'] if j==1 else s['tl'])) for j in range(4)] for r in mac_rows]
    story.append(tbl(md, [W*0.22, W*0.14, W*0.32, W*0.32]))
    story.append(SP(5))

    # ────────────────────────────────────────────────────────────────────
    # 3. RESULTADOS OPERACIONAIS
    # ────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story += [P(f"3. RESULTADOS OPERACIONAIS — {macro.get('empresa_database','2025').split('/')[-1] if '/' in macro.get('empresa_database','2025') else macro.get('empresa_database','2025')}", s['sh']), HR()]

    # DRE
    story.append(P('3.1 Demonstração de Resultado Consolidada', s['ssh']))
    _ano_ref = macro.get('empresa_database','').split('/')[-1] if '/' in macro.get('empresa_database','') else macro.get('empresa_database','')
    lb = dfp.get('lucro_bruto') or 0
    mg_b = dfp.get('margem_bruta') or (round(lb/rl*100,1) if rl > 0 else 0)
    def fmt_val(v, neg=False):
        if v is None or v == 0: return '—'
        return f'({abs(v):,.0f})' if neg and v < 0 else f'{v:,.0f}'
    # Só mostrar linhas que têm dado real
    dre_hdr = [P('(R$ milhões)', s['th']),
               P(f'{_ano_ref}' if _ano_ref else 'Período', s['th']),
               P('Margem / Obs.', s['th'])]
    dre_rows = []
    if rl:  dre_rows.append(['Receita Líquida',          fmt_val(rl),         f'Base {_ano_ref}' if _ano_ref else '—'])
    if lb:  dre_rows.append(['Lucro Bruto',               fmt_val(lb),         f'Mg. Bruta: {mg_b:.1f}%'])
    if eb:  dre_rows.append(['EBITDA Ajustado',           fmt_val(eb),         f'Mg. EBITDA: {mg_eb:.1f}%'])
    if rf:  dre_rows.append(['Resultado Financeiro',      fmt_val(rf, True),   'Juros + variação cambial'])
    if ll is not None and ll != 0:
            dre_rows.append(['Lucro (Prejuízo) Líquido',  fmt_val(ll, True),   'Resultado líquido'])
    if fco: dre_rows.append(['FCO',                       fmt_val(fco),        'Fluxo de caixa operacional'])
    if capex: dre_rows.append(['CAPEX',                   fmt_val(capex,True), 'Investimentos'])
    if juros: dre_rows.append(['Juros Pagos',             fmt_val(juros,True), 'Despesas financeiras pagas'])
    if not dre_rows:
        dre_rows = [['Sem dados financeiros preenchidos', '—', '—']]
    bold_idx = {i for i,r in enumerate(dre_rows) if 'EBITDA' in r[0] or 'Lucro B' in r[0]}
    extra_dre = [('FONTNAME',(0,i+1),(-1,i+1),'Helvetica-Bold') for i in bold_idx]
    for i, r in enumerate(dre_rows):
        if 'EBITDA' in r[0]: extra_dre.append(('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#DCFCE7')))
        elif 'Prejuízo' in r[0] or (r[1].startswith('(') and 'Líquido' in r[0]):
            extra_dre.append(('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#FEE2E2')))
        elif 'Resultado Fin' in r[0]: extra_dre.append(('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#FEF3C7')))
    dd = [dre_hdr] + [[P(r[j], s['tl'] if j==0 else s['tc']) for j in range(3)] for r in dre_rows]
    story.append(tbl(dd, [W*0.38, W*0.22, W*0.40], extra_dre))
    story.append(P(f'Fonte: documentos enviados. Data-base: {macro.get("empresa_database","informada")}. Valores em R$ milhões.', s['cap']))
    story.append(SP(5))

    # Segmentos
    story.append(P('3.2 Desempenho por Segmento', s['ssh']))
    seg_hdr = [P('Segmento', s['th']), P('Receita (R$ mi)', s['th']), P('% Total', s['th']),
               P('EBITDA Aj. (R$ mi)', s['th']), P('Mg. EBITDA', s['th']), P('Destaque', s['th'])]
    # Segmentos: usar dados do usuário, ignorar segmentos sem receita
    _segs_raw = [
        (dfp.get('seg1_nome') or None, dfp.get('receita_seg1'), dfp.get('ebitda_seg1')),
        (dfp.get('seg2_nome') or None, dfp.get('receita_seg2'), dfp.get('ebitda_seg2')),
        (dfp.get('seg3_nome') or None, dfp.get('receita_seg3'), dfp.get('ebitda_seg3')),
        (dfp.get('seg4_nome') or None, dfp.get('receita_seg4'), dfp.get('ebitda_seg4')),
        (dfp.get('seg5_nome') or None, dfp.get('receita_seg5'), dfp.get('ebitda_seg5')),
    ]
    segs = [(nm, rc or 0, eb or 0, '') for nm, rc, eb in _segs_raw if nm and rc and rc > 0]
    if not segs:
        segs = []  # Sem segmentos — pula a tabela
    sd = [seg_hdr] + [
        [P(nm, s['tl']), P(f'{rc_s:,.0f}', s['tc']),
         P(f'{rc_s/rl*100:.1f}%' if rl > 0 else '—', s['tc']),
         P(f'{eb_s:,.0f}', s['tc']),
         P(f'{eb_s/rc_s*100:.1f}%', s['tc']),
         P(dest, s['tl'])]
        for nm, rc_s, eb_s, dest in segs
    ]
    story.append(tbl(sd, [W*0.13, W*0.14, W*0.09, W*0.14, W*0.10, W*0.40]))
    story.append(SP(5))

    # ────────────────────────────────────────────────────────────────────
    # 4. ESTRUTURA DE CAPITAL
    # ────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story += [P('4. ESTRUTURA DE CAPITAL E ALAVANCAGEM', s['sh']), HR()]

    _db_txt = f'A dívida bruta consolidada atingiu R$ {db/1000:.1f} bilhões na data-base.' if db > 0 else 'Dívida bruta não informada.'
    _me = fre.get('divida_me_pct')
    _brl = fre.get('divida_brl_pct')
    _tusd = fre.get('taxa_usd')
    _tbrl = fre.get('taxa_brl')
    _comp = ''
    if _me and _brl:
        _comp = f' Composição: {_me:.0f}% em moeda estrangeira'
        if _tusd: _comp += f' (USD {_tusd:.2f}% a.a.)'
        _comp += f' e {_brl:.0f}% em BRL'
        if _tbrl: _comp += f' ({_tbrl:.2f}% a.a.)'
        _comp += '.'
    story.append(P(_db_txt + _comp, s['body']))
    story.append(SP(4))

    story.append(P('4.1 Cronograma de Vencimentos', s['ssh']))
    vh = [P('Ano', s['th']), P('Total (R$ mi)', s['th']), P('% Dív. Bruta', s['th']),
          P('ME (R$ mi)', s['th']), P('BRL (R$ mi)', s['th']), P('Observação', s['th'])]
    # Calcular divisão ME/BRL dinamicamente a partir dos dados do FRE
    _pct_me_v = fre.get('divida_me_pct') or 0
    _pct_brl_v = fre.get('divida_brl_pct') or (100 - _pct_me_v)
    def _calc_me_brl(v, pct_me):
        me = round(v * pct_me / 100) if pct_me > 0 else 0
        brl = v - me
        return (me, brl)
    me_split = {}  # Dinâmico: calculado por vencimento abaixo
    venc_order = ['2026','2027','2028','2029','2030','2031','apos_2031']
    obs = {'2026':'⚠ CRÍTICO — coberto pelo caixa','2027':'Refinanciamento possível','2028':'⚠ MAIOR pico de amortização',
           '2029':'Gerenciável','2030':'Bonds de longo prazo','2031':'Longo prazo','apos_2031':'Bonds perpétuos / longo'}
    vrc = []
    vrows = []
    for i, ano in enumerate(venc_order):
        val = venc.get(ano, 0)
        me_v, brl_v = _calc_me_brl(val, _pct_me_v)  # Calculado dinamicamente pelo % ME do FRE
        lbl = 'Após 2031' if ano == 'apos_2031' else ano
        pct = val/db*100 if db else 0
        vrows.append([lbl, f'{val:,.0f}', f'{pct:.1f}%', f'{me_v:,.0f}', f'{brl_v:,.0f}', obs[ano]])
        if ano in ('2026','2028'): vrc.append(('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#FEE2E2')))
        elif ano == '2027': vrc.append(('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#FEF3C7')))
    total_v = sum(venc.get(k,0) for k in venc_order)
    total_me = sum(_calc_me_brl(venc.get(k,0), _pct_me_v)[0] for k in venc_order)
    total_brl = sum(_calc_me_brl(venc.get(k,0), _pct_me_v)[1] for k in venc_order)
    vrows.append(['TOTAL', f'{total_v:,.0f}', '100%', f'{total_me:,.0f}', f'{total_brl:,.0f}', ''])
    vrc.append(('BACKGROUND',(0,len(vrows)),(-1,len(vrows)), HexColor('#EFF6FF')))
    vrc.append(('FONTNAME',(0,len(vrows)),(-1,len(vrows)),'Helvetica-Bold'))
    vd = [vh] + [[P(r[0],s['tlb'] if 'TOTAL' in r[0] else s['tl'])] + [P(r[j],s['tc']) for j in range(1,5)] + [P(r[5],s['tl'])] for r in vrows]
    story.append(tbl(vd, [W*0.11,W*0.15,W*0.11,W*0.14,W*0.14,W*0.35], vrc))
    story.append(SP(4))

    # Composição da dívida por instrumento (se informado pelo usuário)
    _comp_div = fre.get('composicao_divida', [])
    if _comp_div and len(_comp_div) > 0:
        story.append(P('4.1.1 Composição por Instrumento', s['ssh']))
        cd_hdr = [P('Instrumento', s['th']), P('% Dívida', s['th']),
                  P('Taxa Emissão', s['th']), P('Vencimento', s['th']), P('Ticker/Código', s['th'])]
        cd_rows = []
        for inst in _comp_div:
            if inst.get('tipo') and inst.get('pct'):
                cd_rows.append([
                    P(inst.get('tipo','—'), s['tl']),
                    P(f"{inst.get('pct','—')}%", s['tc']),
                    P(inst.get('taxa','—'), s['tc']),
                    P(inst.get('venc','—'), s['tc']),
                    P(inst.get('ticker','—'), s['tc']),
                ])
        if cd_rows:
            cd_data = [cd_hdr] + cd_rows
            story.append(tbl(cd_data, [W*0.22, W*0.12, W*0.22, W*0.18, W*0.26]))
            story.append(SP(4))

    story.append(P('4.2 Indicadores de Crédito', s['ssh']))
    _ic_ano = macro.get('empresa_database','').split('/')[-1] if '/' in macro.get('empresa_database','') else macro.get('empresa_database','')
    ic_hdr = [P('Métrica', s['th']), P(f'{_ic_ano}A' if _ic_ano else 'Atual', s['th']),
              P('Benchmark', s['th']), P('Limite Mínimo', s['th']), P('Situação', s['th'])]
    cov_cp = caixa/venc_2026*100 if venc_2026 > 0 else 0
    ic_rows = [
        ['DL / EBITDA Ajustado (x)',    f'{alav:.2f}x',            '2,5x',          'abaixo 3,0x',   '⚠ Acima' if alav > 3.0 else '✓ OK'],
        ['ICJ — EBITDA / Juros (x)',    f'{icj:.2f}x',             'acima 2,5x',    'acima 2,0x',    '⚠ Abaixo' if icj < 2.0 else '✓ OK'],
        ['Dív. Bruta / EBITDA (x)',     f'{db/eb:.2f}x' if eb > 0 else '—', 'abaixo 5,0x', 'abaixo 5,5x', '⚠ Acima' if (eb > 0 and db/eb > 5.0) else '✓ OK'],
        ['FCO / Dívida Bruta (%)',      f'{fco/db*100:.1f}%' if db > 0 else '—', 'acima 10%', 'acima 8%', '⚠ Negativo' if fco < 0 else '✓ OK'],
        ['Caixa / Dív. CP (%)',         f'{cov_cp:.0f}%',          'acima 150%',    'acima 100%',    '✓ OK' if cov_cp > 150 else '⚠ Atenção'],
        ['DL / Patrimônio Líquido (x)', f'{dl/pl:.2f}x' if pl and pl > 0 else '—', 'abaixo 2,5x', 'abaixo 3,0x', '⚠ Elevado' if (pl and pl > 0 and dl/pl > 2.5) else '✓ OK'],
        ['Margem EBITDA Aj. (%)',       f'{mg_eb:.1f}%',           'acima 22%',     'acima 18%',     '✓ OK' if mg_eb > 22 else '⚠ Atenção'],
    ]
    ic_rc = []
    for i, r in enumerate(ic_rows):
        bg = HexColor('#DCFCE7') if '✓' in r[4] else HexColor('#FEE2E2')
        ic_rc.append(('BACKGROUND', (4,i+1),(4,i+1), bg))
        tc = GREEN_POS if '✓' in r[4] else RED_NEG
        ic_rc.append(('TEXTCOLOR', (4,i+1),(4,i+1), tc))
        ic_rc.append(('FONTNAME', (4,i+1),(4,i+1), 'Helvetica-Bold'))
    ic_d = [ic_hdr] + [[P(r[0],s['tl'])] + [P(r[j],s['tc']) for j in range(1,5)] for r in ic_rows]
    story.append(tbl(ic_d, [W*0.30, W*0.14, W*0.16, W*0.16, W*0.24], ic_rc))
    story.append(SP(5))

    # ────────────────────────────────────────────────────────────────────
    # 5. SENSIBILIDADE
    # ────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story += [P('5. ANÁLISE DE SENSIBILIDADE — PREMISSAS DO USUÁRIO', s['sh']), HR()]
    _setor = macro.get('setor','')
    _sens_desc = f'Com base nas premissas inseridas (Selic {selic:.2f}%, USD/BRL R$ {usd_brl:.2f}'
    if _setor in ['mineracao','siderurgia']:
        _sens_desc += f', minério Fe US$ {minerio:.0f}/t'
    if _setor == 'petroleo':
        _sens_desc += f', Brent ~US$ {macro.get("brent",78):.0f}/bbl'
    _sens_desc += f', Treasury 10Y {t10y:.2f}%), calculamos os impactos marginais sobre EBITDA e alavancagem:'
    story.append(P(_sens_desc, s['body']))
    story.append(SP(3))

    _pct_me2 = fre.get('divida_me_pct') or 0
    imp_selic_100 = divida_cdi * 0.01 if divida_cdi > 0 else eb * 0.02
    imp_ebitda_5pct = eb * 0.05 if eb > 0 else 0
    imp_cam_db = db * (_pct_me2/100) / usd_brl * 0.50 if db > 0 and _pct_me2 > 0 else 0

    sh_hdr = [P('Variável', s['th']), P('Choque', s['th']), P('Impacto EBITDA (R$ mi)', s['th']),
              P('Impacto DL (R$ mi)', s['th']), P('DL/EBITDA pós-choque', s['th'])]

    def alav_safe(dl_n, eb_n):
        return f'{dl_n/eb_n:.2f}x' if eb_n > 0 else 'n/a'

    sh_rows = [
        ['Selic / CDI', '+1,0 p.p.',
         f'-{imp_selic_100:,.0f} (desp. fin.)' if imp_selic_100 else '—',
         '—', alav_safe(dl, eb - imp_selic_100)],
        ['Selic / CDI', '-1,0 p.p.',
         f'+{imp_selic_100:,.0f} (desp. fin.)' if imp_selic_100 else '—',
         '—', alav_safe(dl, eb + imp_selic_100)],
        ['EBITDA', '-5% (queda operacional)',
         f'-{imp_ebitda_5pct:,.0f}' if imp_ebitda_5pct else '—',
         '—', alav_safe(dl, eb - imp_ebitda_5pct)],
        ['EBITDA', '+5% (melhora operacional)',
         f'+{imp_ebitda_5pct:,.0f}' if imp_ebitda_5pct else '—',
         '—', alav_safe(dl, eb + imp_ebitda_5pct)],
        ['USD / BRL', '+R$ 0,50',
         '—',
         f'+{imp_cam_db:,.0f} (dívida ME)' if imp_cam_db else '—',
         alav_safe(dl + imp_cam_db, eb)],
        ['USD / BRL', '-R$ 0,50',
         '—',
         f'-{imp_cam_db:,.0f} (dívida ME)' if imp_cam_db else '—',
         alav_safe(dl - imp_cam_db, eb)],
    ]
    # Linha extra setorial
    _setor_s = macro.get('setor','')
    if _setor_s in ('mineracao','siderurgia'):
        imp_com = eb * 0.08
        _comm_nome = 'Minério Fe 62%' if _setor_s == 'mineracao' else 'Aço/HRC'
        sh_rows.insert(4, [f'{_comm_nome} +10%', '+10%', f'+{imp_com:,.0f}', '—', alav_safe(dl, eb+imp_com)])
        sh_rows.insert(5, [f'{_comm_nome} -10%', '-10%', f'-{imp_com:,.0f}', '—', alav_safe(dl, eb-imp_com)])
    elif _setor_s == 'petroleo':
        imp_brent = eb * 0.10
        sh_rows.insert(4, ['Brent (US$/bbl)', '+US$10/bbl', f'+{imp_brent:,.0f}', '—', alav_safe(dl, eb+imp_brent)])
        sh_rows.insert(5, ['Brent (US$/bbl)', '-US$10/bbl', f'-{imp_brent:,.0f}', '—', alav_safe(dl, eb-imp_brent)])

    sh_d = [sh_hdr] + [[P(r[0],s['tl'])] + [P(r[j],s['tc']) for j in range(1,5)] for r in sh_rows]
    story.append(tbl(sh_d, [W*0.20, W*0.18, W*0.22, W*0.20, W*0.20]))
    _base_txt = f'Dívida CDI-linked estimada: R$ {divida_cdi/1000:.1f}bi. Câmbio base: R$ {usd_brl:.2f}.'
    if _pct_me2 > 0: _base_txt += f' Dívida ME: {_pct_me2:.0f}% do total.'
    story.append(P(_base_txt, s['cap']))
    story.append(SP(4))

    # Cenários de alavancagem genéricos
    story.append(P('5.1 Cenários de Alavancagem — Projeção', s['ssh']))
    story.append(P(f'Simulação de DL/EBITDA sob diferentes hipóteses de desempenho operacional e redução de dívida. Base: EBITDA R$ {eb/1000:.1f}bi, DL R$ {dl/1000:.1f}bi.', s['body']))
    story.append(SP(3))

    c_rows_data = [
        ('Stress',   0.90, 0,         HexColor('#FEE2E2')),
        ('Base',     1.00, dl*0.05,   HexColor('#FEF3C7')),
        ('Otimista', 1.10, dl*0.15,   HexColor('#DCFCE7')),
    ]
    c_hdr = [P('Cenário', s['th']), P('EBITDA 2026E', s['th']),
             P('Redução DL', s['th']), P('DL 2026E', s['th']), P('DL/EBITDA', s['th']), P('Situação', s['th'])]
    cen_d = [c_hdr]
    cen_rc = []
    for i, (nome, eb_mult, dl_red, cor) in enumerate(c_rows_data):
        eb_c = eb * eb_mult
        dl_c = max(0, dl - dl_red)
        alav_c = dl_c / eb_c if eb_c > 0 else 0
        sit = '⚠ Crítico' if alav_c > 4.0 else ('⚠ Monitorar' if alav_c > 3.0 else '✓ Convergindo')
        cen_d.append([P(nome, s['tlb']),
                      P(f'R$ {eb_c/1000:.1f}bi', s['tc']),
                      P(f'R$ {dl_red/1000:.1f}bi', s['tc']),
                      P(f'R$ {dl_c/1000:.1f}bi', s['tc']),
                      P(f'{alav_c:.2f}x', s['tc']),
                      P(sit, s['tc'])])
        cen_rc.append(('BACKGROUND',(0,i+1),(-1,i+1), cor))
    story.append(tbl(cen_d, [W*0.13, W*0.15, W*0.15, W*0.15, W*0.13, W*0.29], cen_rc))
    story.append(SP(5))

    # ────────────────────────────────────────────────────────────────────
    # 6. RISCOS
    # ────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story += [P('6. MATRIZ DE RISCOS', s['sh']), HR()]

    r_hdr = [P('Risco', s['th']), P('Impacto', s['th']),
             P('Probabilidade', s['th']), P('Comentário', s['th'])]
    selic_risco = 'ALTO' if selic >= 15.0 else 'MÉDIO'
    _pct_me_r = fre.get('divida_me_pct') or 0
    _venc_cp = venc_2026 or (dl * 0.20)  # estimativa se não informado
    # Riscos genéricos baseados nos dados reais da empresa
    risk_rows = []

    # Risco de refinanciamento (se tiver vencimento relevante)
    if _venc_cp > 0:
        cob_cp = caixa / _venc_cp if _venc_cp > 0 else 0
        risco_refin = 'ALTO' if cob_cp < 1.0 else 'MÉDIO'
        risk_rows.append([
            f'Refinanciamento CP (R$ {_venc_cp/1000:.1f}bi)',
            risco_refin, 'MÉDIA' if cob_cp < 1.2 else 'BAIXA',
            f'Caixa R$ {caixa/1000:.1f}bi cobre {cob_cp*100:.0f}% do vencimento. Selic {selic:.2f}%.'
        ])

    # Risco de alavancagem
    alav_atual = dl/eb if eb > 0 else 0
    if alav_atual > 0:
        risco_alav = 'MUITO ALTO' if alav_atual > 5 else ('ALTO' if alav_atual > 3.5 else 'MÉDIO')
        risk_rows.append([
            f'Alavancagem elevada ({alav_atual:.2f}x DL/EBITDA)',
            risco_alav, 'MÉDIA',
            f'Redução depende de crescimento de EBITDA e/ou amortização de dívida.'
        ])

    # Risco de taxa de juros
    risk_rows.append([
        f'Selic / CDI elevado ({selic:.2f}% a.a.)',
        selic_risco, 'MÉDIA',
        f'Impacto estimado de R$ {imp_selic_100:,.0f}mi por +1pp na taxa.'
        if imp_selic_100 > 0 else f'Monitorar custo da dívida com Selic {selic:.2f}%.'
    ])

    # Risco cambial (se tiver dívida ME)
    if _pct_me_r > 20:
        risk_rows.append([
            f'Volatilidade cambial ({_pct_me_r:.0f}% dívida em ME)',
            'ALTO', 'MÉDIA',
            f'Hedge cambial reduz exposição. USD/BRL atual: R$ {usd_brl:.2f}.'
        ])

    # Risco de FCO negativo
    if fco < 0:
        risk_rows.append([
            'FCO Negativo — pressão de liquidez',
            'ALTO', 'ALTA',
            f'FCO de R$ {fco/1000:.1f}bi. Monitorar capital de giro e geração operacional.'
        ])

    # Risco setorial
    _setor_r = macro.get('setor','')
    if _setor_r == 'petroleo':
        risk_rows.append([f'Volatilidade do Brent (atual US$ {macro.get("brent",78):.0f}/bbl)', 'ALTO', 'MÉDIA',
            'Preço do petróleo impacta diretamente receita e EBITDA E&P.'])
    elif _setor_r in ('mineracao','siderurgia'):
        risk_rows.append(['Volatilidade de commodities', 'ALTO', 'MÉDIA',
            'Preços das commodities do setor correlacionados ao ciclo econômico global.'])
    elif _setor_r == 'varejo':
        risk_rows.append(['Inadimplência do consumidor', 'MÉDIO', 'MÉDIA',
            f'Selic {selic:.2f}% pressionando renda. Monitorar SSS e inadimplência.'])
    elif _setor_r == 'saude':
        risk_rows.append(['Inflação médica acima do repasse', 'ALTO', 'ALTA',
            'Sinistralidade elevada comprime margens se reajuste de ANS for insuficiente.'])
    elif _setor_r == 'bancos':
        risk_rows.append(['Inadimplência (NPL)', 'MÉDIO', 'MÉDIA',
            f'Selic {selic:.2f}% eleva risco de crédito da carteira. Monitorar provisões.'])

    # Risco regulatório/macro sempre presente
    risk_rows.append([
        'Risco macro / fiscal Brasil', 'MÉDIO', 'MÉDIA',
        f'Selic {selic:.2f}%, IPCA {ipca:.2f}%. Custo de capital elevado pressiona valuation e refinanciamento.'
    ])
    imp_colors = {'MUITO ALTO': RED_NEG, 'ALTO': ORANGE, 'MÉDIO': GOLD, 'BAIXO': GREEN_POS}
    prob_colors = {'ALTA': RED_NEG, 'MÉDIA': ORANGE, 'BAIXA': GREEN_POS}
    r_d = [r_hdr]
    for r in risk_rows:
        r_d.append([
            P(r[0], s['tl']),
            P(r[1], ParagraphStyle('imp', fontName='Helvetica-Bold', fontSize=7.5,
                textColor=imp_colors.get(r[1], GRAY_DARK), leading=10, alignment=TA_CENTER)),
            P(r[2], ParagraphStyle('pr', fontName='Helvetica-Bold', fontSize=7.5,
                textColor=prob_colors.get(r[2], GRAY_DARK), leading=10, alignment=TA_CENTER)),
            P(r[3], s['tl']),
        ])
    story.append(tbl(r_d, [W*0.28, W*0.11, W*0.12, W*0.49]))
    story.append(SP(5))

    # ────────────────────────────────────────────────────────────────────
    # 7. RECOMENDAÇÃO
    # ────────────────────────────────────────────────────────────────────
    story += [P('7. RECOMENDAÇÃO DE CRÉDITO', s['sh']), HR()]

    spread_min = int(350 + max(0,(alav-3.0)*60))
    spread_max = spread_min + 50
    yield_alvo = t10y + spread_min/100

    rec_inner = Table([
        [P(f"<b>RECOMENDAÇÃO: {macro.get('recomendacao','MANTER')} — Com Monitoramento Ativo</b>",
           ParagraphStyle('rh', fontName='Helvetica-Bold', fontSize=11,
                          textColor=colors.white, leading=16))],
        [P(
            f"Mantemos a recomendação de <b>{macro.get('recomendacao','MANTER')}</b> exposição seletiva a bonds curtos e médios de {macro.get('empresa_nome','Empresa')} "
            f'com spread alvo de {spread_min}-{spread_max} bps'
            + (f' sobre Treasuries (yield alvo ~{yield_alvo:.2f}% USD, Treasury 10Y {t10y:.2f}%).' if t10y > 0 else '.')
            + (f' EBITDA Ajustado de R$ {eb/1000:.1f}bi.' if eb > 0 else '')
            + (f' Margem EBITDA: {mg_eb:.1f}%.' if mg_eb > 0 else '')
            + (f' Caixa de R$ {caixa/1000:.1f}bi' + (f' cobre {caixa/venc_2026*100:.0f}% do vencimento CP.' if venc_2026 > 0 else '.') if caixa > 0 else '')
            + (f' Alavancagem: {dl/eb:.2f}x DL/EBITDA.' if eb > 0 and dl > 0 else ''),
            ParagraphStyle('rb', fontName='Helvetica', fontSize=8.5,
                           textColor=GRAY_DARK, leading=13, alignment=TA_JUSTIFY))
        ],
    ], colWidths=[W])
    rec_inner.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0), NAVY),
        ('BACKGROUND',(0,1),(-1,1), HexColor('#EFF6FF')),
        ('BOX',(0,0),(-1,-1), 1.2, NAVY),
        ('LINEBELOW',(0,0),(-1,0), 2.0, ACCENT),
        ('TOPPADDING',(0,0),(-1,-1), 9),
        ('BOTTOMPADDING',(0,0),(-1,-1), 9),
        ('LEFTPADDING',(0,0),(-1,-1), 12),
        ('RIGHTPADDING',(0,0),(-1,-1), 12),
    ]))
    story.append(KeepTogether([rec_inner]))
    story.append(SP(5))

    # Gatilhos
    story.append(P('7.1 Gatilhos de Revisão', s['ssh']))
    gt_hdr = [P('Direção', s['th']), P('Gatilho', s['th']), P('Métrica', s['th']), P('Monitoramento', s['th'])]
    _alav_tg = round(max(2.0, (dl/eb if eb > 0 else 3.5) - 0.5), 1) if eb > 0 else 3.0
    _caixa_min = round(caixa * 0.6 / 1000, 1) if caixa > 0 else 5.0
    _fco_tg = round(max(0, eb * 0.15) / 1000, 1) if eb > 0 else 1.0
    _setor_gt = macro.get('setor','')
    gt_rows = [
        ['⬆ UPGRADE', f'DL/EBITDA abaixo de {_alav_tg}x por dois trimestres consecutivos',
         'DL/EBITDA', 'Resultados trimestrais (ITR)'],
        ['⬆ UPGRADE', f'FCO acima de R$ {_fco_tg:.1f}bi por dois trimestres',
         'FCO trimestral', 'ITR / DFC'],
        ['⬇ DOWNGRADE', f'Deterioração de EBITDA acima de 15% sem perspectiva de recuperação',
         'EBITDA / Margem', 'Resultados trimestrais'],
        ['⬇ DOWNGRADE', f'Caixa abaixo de R$ {_caixa_min:.1f}bi (atual R$ {caixa/1000:.1f}bi)',
         'Caixa gerencial', 'ITR trimestral'],
        ['⬇ DOWNGRADE', f'Alavancagem acima de {round((dl/eb if eb > 0 else 4.0)+0.5,1)}x por dois trimestres',
         'DL/EBITDA', 'Resultados trimestrais'],
        ['⬇ DOWNGRADE', f'Selic acima de {selic+2:.2f}% por mais de dois trimestres',
         'Política monetária', 'COPOM / Focus'],
    ]
    # Adicionar gatilho setorial
    if _setor_gt in ('mineracao','siderurgia'):
        gt_rows.insert(2, ['⬇ DOWNGRADE', f'Commodity principal abaixo de -15% por dois trimestres',
            'Preço spot commodity', 'Diário (Bloomberg/SGX)'])
    elif _setor_gt == 'saude':
        gt_rows.insert(2, ['⬇ DOWNGRADE', 'Sinistralidade acima de 85% por dois trimestres',
            'Sinistralidade', 'Resultados trimestrais'])
    elif _setor_gt == 'bancos':
        gt_rows.insert(2, ['⬇ DOWNGRADE', 'Inadimplência >90d acima de 6%',
            'NPL', 'Nota de resultado'])
    gt_rc = [('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#DCFCE7') if '⬆' in r[0] else HexColor('#FEE2E2')) for i,r in enumerate(gt_rows)]
    gt_d = [gt_hdr] + [[P(r[0], ParagraphStyle('g', fontName='Helvetica-Bold', fontSize=7.5,
                textColor=GREEN_POS if '⬆' in r[0] else RED_NEG, leading=10, alignment=TA_LEFT)),
                P(r[1], s['tl']), P(r[2], s['tc']), P(r[3], s['tl'])] for r in gt_rows]
    story.append(tbl(gt_d, [W*0.17, W*0.42, W*0.18, W*0.23], gt_rc))
    story.append(SP(6))

    # ────────────────────────────────────────────────────────────────────
    # 8. ANÁLISE QUANTITATIVA AVANÇADA
    # ────────────────────────────────────────────────────────────────────
    _pf = macro.get('_piotroski') or {}
    _dscr_d = macro.get('_dscr') or {}
    _div_s = macro.get('_dividend_sust') or {}

    if _pf or _dscr_d:
        story.append(PageBreak())
        story += [P('8. ANÁLISE QUANTITATIVA AVANÇADA', s['sh']), HR()]
        story.append(P(
            'Esta seção apresenta modelos quantitativos globalmente reconhecidos usados por analistas de crédito e investidores '
            'institucionais para complementar a análise qualitativa. Os resultados são calculados automaticamente a partir '
            'dos dados extraídos dos documentos enviados.', s['body']))
        story.append(SP(4))

        # ── Piotroski F-Score ──
        if _pf:
            story.append(P('8.1 Piotroski F-Score (Qualidade Financeira)', s['ssh']))
            story.append(P(
                f"O F-Score de Joseph Piotroski (2000) avalia a qualidade financeira da empresa em 9 critérios binários "
                f"distribuídos em 3 pilares. Score atual: <b>{_pf.get('f_score', '—')}/9 — {_pf.get('qualidade','—')}</b>. "
                f"{_pf.get('interpretacao','')}",
                s['body']))
            story.append(SP(3))

            pf_hdr = [P('Critério', s['th']), P('Pilar', s['th']), P('Valor', s['th']), P('Ponto', s['th'])]
            pilar_nomes = {
                'F1_ROA': 'Rentabilidade', 'F2_FCO': 'Rentabilidade',
                'F3_DELTA_ROA': 'Rentabilidade', 'F4_ACCRUAL': 'Rentabilidade',
                'F5_LEVER': 'Alavancagem/Liq.', 'F6_LIQUID': 'Alavancagem/Liq.',
                'F7_SHARES': 'Alavancagem/Liq.',
                'F8_GROSS_MG': 'Ef. Operacional', 'F9_ASSET_TURN': 'Ef. Operacional',
            }
            pf_rows = []
            cor_pf = []
            for i, det in enumerate(_pf.get('detalhes', [])):
                pnt = det.get('ponto', 0)
                crit_key = list((_pf.get('pontos') or {}).keys())[i] if i < len(_pf.get('pontos',{})) else ''
                pf_rows.append([det.get('criterio',''), pilar_nomes.get(crit_key,''),
                                 det.get('valor',''), '✓' if pnt else '✗'])
                bg = HexColor('#DCFCE7') if pnt else HexColor('#FEE2E2')
                cor_pf.append(('BACKGROUND',(3,i+1),(3,i+1), bg))
                cor_pf.append(('TEXTCOLOR',(3,i+1),(3,i+1), GREEN_POS if pnt else RED_NEG))
                cor_pf.append(('FONTNAME',(3,i+1),(3,i+1),'Helvetica-Bold'))
            # Total
            pf_rows.append(['TOTAL F-SCORE', '9 critérios', '',
                             f"{_pf.get('f_score','—')}/9"])
            cor_pf.append(('BACKGROUND',(0,len(pf_rows)),(-1,len(pf_rows)), HexColor('#EFF6FF')))
            cor_pf.append(('FONTNAME',(0,len(pf_rows)),(-1,len(pf_rows)),'Helvetica-Bold'))

            pf_data = [pf_hdr] + [[P(r[j], s['tl'] if j<2 else s['tc']) for j in range(4)] for r in pf_rows]
            story.append(tbl(pf_data, [W*0.44, W*0.20, W*0.22, W*0.14], cor_pf))
            story.append(P('Fonte: Piotroski (2000), Journal of Accounting Research. Critérios calculados a partir dos documentos enviados.', s['cap']))
            story.append(SP(5))

        # ── DSCR ──
        if _dscr_d:
            story.append(P('8.2 DSCR — Debt Service Coverage Ratio', s['ssh']))
            _dscr_v = _dscr_d.get('dscr', 0)
            _dscr_nivel = _dscr_d.get('nivel','—')
            _dscr_cor = GREEN_POS if _dscr_nivel == 'SEGURO' else (ORANGE if _dscr_nivel in ('ADEQUADO','LIMITE') else RED_NEG)
            story.append(P(
                f"O DSCR mede a capacidade do FCO de cobrir o serviço da dívida (juros + amortizações). "
                f"DSCR atual: <b>{_dscr_v:.2f}x — {_dscr_nivel}</b>. {_dscr_d.get('interpretacao','')}",
                s['body']))
            story.append(SP(3))

            dscr_hdr = [P('Componente', s['th']), P('Valor (R$ mi)', s['th']), P('Interpretação', s['th'])]
            dscr_rows = [
                ['FCO (Fluxo de Caixa Operacional)', f"{_dscr_d.get('fco',0):,.0f}", 'Capacidade de pagamento'],
                ['Juros Pagos', f"{_dscr_d.get('juros_pagos',0):,.0f}", 'Encargo financeiro'],
                ['Amortização CP', f"{_dscr_d.get('amortizacao_cp',0):,.0f}", 'Vencimento no curto prazo'],
                ['Serviço Total da Dívida', f"{_dscr_d.get('debt_service',0):,.0f}", 'Denominador do DSCR'],
                [f'DSCR = {_dscr_v:.3f}x', f'Nível: {_dscr_nivel}', _dscr_d.get('interpretacao','')],
            ]
            dscr_extra = [('BACKGROUND',(0,5),(-1,5), HexColor('#DCFCE7') if _dscr_nivel == 'SEGURO' else HexColor('#FEE2E2')),
                         ('FONTNAME',(0,5),(-1,5),'Helvetica-Bold')]
            dscr_data = [dscr_hdr] + [[P(r[j], s['tlb'] if j==0 else s['tc']) for j in range(3)] for r in dscr_rows]
            story.append(tbl(dscr_data, [W*0.36, W*0.22, W*0.42], dscr_extra))
            story.append(SP(5))

        # ── Sustentabilidade de Dividendos ──
        if _div_s and _div_s.get('nivel') != 'SEM DIVIDENDOS':
            story.append(P('8.3 Sustentabilidade de Dividendos', s['ssh']))
            _div_nivel = _div_s.get('nivel','—')
            _div_score = _div_s.get('score')
            story.append(P(
                f"Score de sustentabilidade de dividendos: <b>{_div_score}/10 — {_div_nivel}</b>. "
                f"{_div_s.get('interpretacao','')} "
                + (f"Flags: {'; '.join(_div_s.get('flags',[]))}." if _div_s.get('flags') else ''),
                s['body']))
            story.append(SP(3))

            div_hdr = [P('Indicador', s['th']), P('Valor', s['th']), P('Referência', s['th'])]
            div_rows = [
                ['Dividendos Pagos (R$ mi)', f"{_div_s.get('dividendos_pagos',0):,.0f}", '—'],
                ['Payout / Lucro Líquido (%)', f"{_div_s.get('payout_lucro','—')}%", '< 100%'],
                ['Payout / FCO (%)', f"{_div_s.get('payout_fco','—')}%", '< 80%'],
                ['Payout / FCL (%)', f"{_div_s.get('payout_fcl','—')}%", '< 100%'],
                ['FCL (FCO - CAPEX) (R$ mi)', f"{_div_s.get('free_cash_flow',0):,.0f}", '> Dividendos'],
            ]
            div_data = [div_hdr] + [[P(r[j], s['tl'] if j==0 else s['tc']) for j in range(3)] for r in div_rows]
            story.append(tbl(div_data, [W*0.40, W*0.22, W*0.38]))
            story.append(SP(5))

    story.append(HRFlowable(width='100%', thickness=0.5, color=GRAY_LIGHT, spaceAfter=3))
    story.append(P(
        f"DISCLAIMER: Este relatório sobre {macro.get('empresa_nome','a empresa')} foi gerado com base nos documentos e "
        'nas premissas macroeconômicas inseridas manualmente no dashboard. As projeções, análises e estimativas aqui '
        'contidas são de caráter puramente informativo e não constituem oferta, solicitação ou recomendação formal de '
        'compra ou venda de quaisquer valores mobiliários. Rentabilidade passada não é garantia de performance futura.',
        ParagraphStyle('disc', fontName='Helvetica', fontSize=7, textColor=GRAY_MID,
                       leading=10, alignment=TA_JUSTIFY, spaceAfter=2)))

    def fp(canvas, doc): draw_cover(canvas, doc, dfp, fre, macro)
    doc.build(story, onFirstPage=fp, onLaterPages=make_hf(macro))
    buf.seek(0)
    return buf.getvalue()

# ─── ROTAS ─────────────────────────────────────────────────────────────────────

# ── Cache CVM carregado uma vez por sessão ──────────────────────────
_cvm_cache = {}

def _load_cvm_cache():
    """Carrega cadastro CVM em memória (CNPJ → ticker, cod_cvm, segmento)."""
    global _cvm_cache
    if _cvm_cache:
        return _cvm_cache
    try:
        import requests as rq
        import io as _io
        import csv
        r = rq.get('https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv',
                   timeout=15, headers={'User-Agent':'Mozilla/5.0'})
        if r.status_code == 200:
            reader = csv.DictReader(_io.StringIO(r.content.decode('latin-1')), delimiter=';')
            for row in reader:
                cnpj_raw = ''.join(filter(str.isdigit, row.get('CNPJ_CIA','')))
                if cnpj_raw:
                    _cvm_cache[cnpj_raw] = {
                        'cod_cvm': row.get('CD_CVM',''),
                        'nome_cvm': row.get('DENOM_SOCIAL',''),
                        'nome_pregao': row.get('DENOM_COMERC',''),
                        'segmento': row.get('SEGM',''),
                        'categoria': row.get('CATEG_REG',''),
                        'situacao_cvm': row.get('SIT',''),
                    }
    except Exception as e:
        pass
    return _cvm_cache

# Tabela de mapeamento CNPJ → Ticker (principais empresas B3)
# Reduz dependência de busca dinâmica e aumenta precisão
_CNPJ_TICKER_MAP = {
    '00000000000191': 'BBAS3',  # Banco do Brasil
    '60701190000104': 'ITUB4',  # Itaú Unibanco
    '60746948000112': 'BBDC4',  # Bradesco
    '90400888000142': 'BRSR6',  # Banrisul
    '00362305000104': 'PETR4',  # Petrobras
    '33592510000154': 'VALE3',  # Vale
    '33650044000180': 'CSNA3',  # CSN
    '60889128000180': 'GGBR4',  # Gerdau
    '92702067000196': 'USIM5',  # Usiminas
    '02558157000162': 'SUZB3',  # Suzano
    '89637490000114': 'KLBN11', # Klabin
    '00017677000155': 'MGLU3',  # Magazine Luiza
    '07526557000100': 'LREN3',  # Lojas Renner
    '45543915000181': 'WEGE3',  # WEG
    '00395288000130': 'ABEV3',  # Ambev
    '00805753000155': 'HAPV3',  # Hapvida
    '00108786000165': 'RAIL3',  # Rumo Logística
    '04196821000103': 'PRIO3',  # PetroRio
    '21076791000116': 'CSAN3',  # Cosan
    '09346601000125': 'RENT3',  # Localiza
    '59056342000108': 'VIVT3',  # Telefônica Brasil
    '04164616000158': 'TIMS3',  # TIM
    '00001180000126': 'EGIE3',  # Engie Brasil
    '03467321000199': 'CPFE3',  # CPFL Energia
    '09274232000118': 'ENGI11', # Energisa
    '60553066000161': 'CMIG4',  # Cemig
    '07343017000195': 'CYRE3',  # Cyrela
    '08851605000102': 'MRVE3',  # MRV Engenharia
    '43098826000156': 'SLCE3',  # SLC Agrícola
    '79609481000133': 'JALL3',  # Jalles Machado
    '02316522000164': 'JBSS3',  # JBS
    '73295854000193': 'MRFG3',  # Marfrig
    '09512920000176': 'BEEF3',  # Minerva
    '07960688000106': 'COGN3',  # Cogna
    '01800019000185': 'YDUQ3',  # Ânima
    '11598259000115': 'BBSE3',  # BB Seguridade
    '56994502000150': 'PSSA3',  # Porto Seguro
    '03853896000140': 'DASA3',  # DASA
    '61189288000189': 'FLRY3',  # Fleury
    '47508411000156': 'SANB11', # Santander Brasil
    '61472676000169': 'BPAC11', # BTG Pactual
    '09266298000146': 'VBBR3',  # Vibra Energia
}

def _buscar_ticker_brapi(nome_pregao, nome_social, cod_cvm, cnpj_raw=''):
    """Tenta encontrar ticker via mapa estático (CNPJ) ou Brapi search (fallback)."""
    # 1. Mapa estático de CNPJ → Ticker (mais rápido e confiável)
    if cnpj_raw and cnpj_raw in _CNPJ_TICKER_MAP:
        return _CNPJ_TICKER_MAP[cnpj_raw]

    BRAPI_TOKEN = os.environ.get('BRAPI_TOKEN','ucaHWHuWF7tLMv47tpzQB8')
    try:
        import requests as rq
        # 2. Busca por nome no Brapi (fallback)
        nome_pregao_limpo = (nome_pregao or '').strip()[:20]
        termos = [t for t in [nome_pregao_limpo] if t]
        for termo in termos:
            r = rq.get('https://brapi.dev/api/quote/list',
                       params={'search': termo, 'token': BRAPI_TOKEN},
                       timeout=8)
            if r.status_code == 200:
                stocks = r.json().get('stocks', [])
                if stocks:
                    # Preferir ações ON (3) ou PN (4) ou UNT (11)
                    for sufixo in ['3','4','11','5','6']:
                        for s in stocks:
                            if s.get('stock','').endswith(sufixo):
                                return s['stock']
                    return stocks[0].get('stock','')
    except Exception:
        pass
    return ''

def _buscar_dados_brapi(ticker):
    """Busca cotação e múltiplos via Brapi para um ticker."""
    if not ticker:
        return {}
    BRAPI_TOKEN = os.environ.get('BRAPI_TOKEN','ucaHWHuWF7tLMv47tpzQB8')
    try:
        import requests as rq
        r = rq.get(f'https://brapi.dev/api/quote/{ticker}',
                   params={'token': BRAPI_TOKEN, 'modules': 'summaryProfile,defaultKeyStatistics,financialData'},
                   timeout=10)
        if r.status_code != 200:
            return {}
        results = r.json().get('results', [])
        if not results:
            return {}
        q = results[0]
        return {
            'cotacao': q.get('regularMarketPrice'),
            'variacao_dia': q.get('regularMarketChangePercent'),
            'market_cap': q.get('marketCap'),
            'market_cap_bi': round(q.get('marketCap',0)/1e9, 2) if q.get('marketCap') else None,
            'volume': q.get('regularMarketVolume'),
            'pl': q.get('priceEarningsRatio') or q.get('trailingPE'),
            'pvp': q.get('priceToBook'),
            'ev_ebitda': q.get('enterpriseToEbitda'),
            'dividend_yield': q.get('dividendYield'),
            'beta': q.get('beta'),
            'min_52s': q.get('fiftyTwoWeekLow'),
            'max_52s': q.get('fiftyTwoWeekHigh'),
            'nome_pregao': q.get('shortName') or q.get('longName',''),
            'setor_brapi': q.get('sector',''),
            'subsetor': q.get('industry',''),
            'descricao': q.get('longBusinessSummary','')[:300] if q.get('longBusinessSummary') else '',
            'funcionarios': q.get('fullTimeEmployees'),
            'website': q.get('website',''),
            'pais': q.get('country','Brasil'),
            'moeda': q.get('currency','BRL'),
        }
    except Exception as e:
        return {}

@app.route('/api/cnpj/<cnpj>')
def consultar_cnpj(cnpj):
    """Pipeline completo: CNPJ → BrasilAPI + CVM + Brapi."""
    raw = ''.join(filter(str.isdigit, cnpj))
    if len(raw) != 14:
        return jsonify({'error': 'CNPJ inválido'}), 400

    import requests as rq

    resultado = {
        'cnpj': raw,
        'razao_social': '',
        'nome_fantasia': '',
        'cnae': '',
        'cnae_codigo': '',
        'situacao': '',
        'municipio': '',
        'uf': '',
        'capital_social': None,
        'data_abertura': '',
        'qsa': [],
        # CVM
        'cod_cvm': '',
        'segmento_b3': '',
        'categoria_cvm': '',
        'situacao_cvm': '',
        # Mercado
        'ticker': '',
        'cotacao': None,
        'variacao_dia': None,
        'market_cap_bi': None,
        'pl': None,
        'pvp': None,
        'ev_ebitda': None,
        'dividend_yield': None,
        'beta': None,
        'min_52s': None,
        'max_52s': None,
        'setor_brapi': '',
        'subsetor': '',
        'funcionarios': None,
        'website': '',
        'descricao': '',
    }

    # ── 1. BrasilAPI (Receita Federal) ──────────────────────────────
    try:
        r = rq.get(f'https://brasilapi.com.br/api/cnpj/v1/{raw}',
                   headers={'User-Agent':'Mozilla/5.0'}, timeout=12)
        if r.status_code == 200:
            d = r.json()
            resultado['razao_social'] = d.get('razao_social','')
            resultado['nome_fantasia'] = d.get('nome_fantasia','') or d.get('razao_social','')
            cnae_desc = d.get('cnae_fiscal_descricao','')
            if not cnae_desc and d.get('cnaes_secundarios'):
                cnae_desc = d['cnaes_secundarios'][0].get('descricao','')
            resultado['cnae'] = cnae_desc
            resultado['cnae_codigo'] = str(d.get('cnae_fiscal',''))
            resultado['situacao'] = d.get('descricao_situacao_cadastral','')
            resultado['municipio'] = d.get('municipio','')
            resultado['uf'] = d.get('uf','')
            resultado['capital_social'] = d.get('capital_social')
            resultado['data_abertura'] = d.get('data_inicio_atividade','')
            qsa = d.get('qsa',[])
            resultado['qsa'] = [{'nome': s.get('nome_socio',''), 'qualificacao': s.get('qualificacao_socio','')} for s in qsa[:10]]
        else:
            # Fallback ReceitaWS
            r2 = rq.get(f'https://receitaws.com.br/v1/cnpj/{raw}',
                        headers={'User-Agent':'Mozilla/5.0'}, timeout=10)
            if r2.status_code == 200:
                d2 = r2.json()
                resultado['razao_social'] = d2.get('nome','')
                resultado['nome_fantasia'] = d2.get('fantasia','') or d2.get('nome','')
                ativ = d2.get('atividade_principal',[{}])
                resultado['cnae'] = ativ[0].get('text','') if ativ else ''
                resultado['situacao'] = d2.get('situacao','')
                resultado['municipio'] = d2.get('municipio','')
                resultado['uf'] = d2.get('uf','')
                resultado['qsa'] = [{'nome': s.get('nome',''), 'qualificacao': s.get('qual','')} for s in d2.get('qsa',[])[:10]]
    except Exception as e:
        resultado['_erro_receita'] = str(e)

    # ── 2. CVM — código, segmento B3, ticker ────────────────────────
    try:
        # Tentar primeiro no mapa estático (mais rápido)
        if raw in _CNPJ_TICKER_MAP:
            resultado['ticker'] = _CNPJ_TICKER_MAP[raw]
        cvm = _load_cvm_cache()
        if raw in cvm:
            info = cvm[raw]
            resultado['cod_cvm'] = info.get('cod_cvm','')
            resultado['segmento_b3'] = info.get('segmento','')
            resultado['categoria_cvm'] = info.get('categoria','')
            resultado['situacao_cvm'] = info.get('situacao_cvm','')
            nome_pregao = info.get('nome_pregao','') or resultado['nome_fantasia']
            if not resultado['ticker']:
                tk = _buscar_ticker_brapi(nome_pregao, resultado['razao_social'], info.get('cod_cvm',''), raw)
                if tk:
                    resultado['ticker'] = tk
        elif not resultado['ticker']:
            # CVM não tem a empresa — tentar pelo nome (empresa de capital fechado)
            nome_pregao = resultado.get('nome_fantasia') or resultado.get('razao_social','')
            tk = _buscar_ticker_brapi(nome_pregao, resultado['razao_social'], '', raw)
            if tk:
                resultado['ticker'] = tk
    except Exception as e:
        resultado['_erro_cvm'] = str(e)

    # ── 3. Brapi — cotação e múltiplos ──────────────────────────────
    if resultado['ticker']:
        try:
            dados_mkt = _buscar_dados_brapi(resultado['ticker'])
            resultado.update({k: v for k, v in dados_mkt.items() if v not in (None, '', 0)})
        except Exception as e:
            resultado['_erro_brapi'] = str(e)

    # ── 4. Dados adicionais CVM (DFP mais recente via download) ──────
    try:
        if resultado.get('ticker'):
            resultado['_cvm_dfp_url'] = (
                f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/"
            )
    except Exception:
        pass

    # ── 5. Notícias recentes (Google News RSS) ────────────────────
    try:
        import urllib.request, urllib.parse
        from xml.etree import ElementTree as ET
        noticias = []
        vistas_cnpj = set()
        nome_busca = resultado.get('nome_fantasia') or resultado.get('razao_social') or ''
        ticker_busca = resultado.get('ticker') or ''
        queries_cnpj = []
        if ticker_busca:
            queries_cnpj.append(f'{ticker_busca} resultados crédito B3')
        if nome_busca:
            nome_curto = nome_busca.split()[0] if nome_busca else ''
            queries_cnpj.append(f'{nome_curto} empresa financeiro Brasil')
        for qr in queries_cnpj[:2]:
            try:
                q_enc = urllib.parse.quote(qr)
                url_n = f'https://news.google.com/rss/search?q={q_enc}&hl=pt-BR&gl=BR&ceid=BR:pt-419'
                req_n = urllib.request.Request(url_n, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req_n, timeout=8) as rn:
                    xml_n = rn.read()
                root_n = ET.fromstring(xml_n)
                for item in root_n.findall('.//item')[:6]:
                    titulo = item.findtext('title','').split(' - ')[0].strip()
                    if titulo and titulo not in vistas_cnpj:
                        vistas_cnpj.add(titulo)
                        noticias.append({
                            'titulo': titulo,
                            'link': item.findtext('link',''),
                            'publicado': item.findtext('pubDate',''),
                        })
            except Exception:
                continue
        resultado['noticias_recentes'] = noticias[:8]
    except Exception as e:
        resultado['noticias_recentes'] = []
        resultado['_erro_noticias'] = str(e)

    # ── 6. Verificação de situação cadastral e alertas ────────────
    alertas = []
    sit = resultado.get('situacao','').upper()
    if sit and sit not in ('ATIVA','ATIVO','REGULAR'):
        alertas.append(f'⚠ Situação cadastral: {sit}')
    if resultado.get('situacao_cvm') and resultado['situacao_cvm'].upper() not in ('A','ATIVO','ATIVA','NORMAL'):
        alertas.append(f'⚠ Situação CVM: {resultado["situacao_cvm"]}')
    capital = resultado.get('capital_social') or 0
    if capital and capital < 100000:
        alertas.append('⚠ Capital social muito baixo (< R$ 100 mil)')
    resultado['alertas'] = alertas

    return jsonify(resultado)

@app.route('/api/treasury')
def treasury():
    """Retorna Treasury 10Y via FRED API."""
    try:
        import requests as req2
        FRED_KEY = os.environ.get('FRED_KEY','b22fa17b11e3e89d8c73dce4b08a0cd9')
        r = req2.get('https://api.stlouisfed.org/fred/series/observations',
                     params={'series_id':'DGS10','api_key':FRED_KEY,'file_type':'json',
                             'sort_order':'desc','limit':5},
                     timeout=10)
        data = r.json()
        obs = [o for o in data.get('observations',[]) if o.get('value') and o['value']!='.']
        if obs:
            return jsonify({'value': float(obs[0]['value']), 'date': obs[0]['date']})
    except:
        pass
    return jsonify({'value': 4.30, 'date': 'estimado'})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/health')
def health():
    return jsonify({'ok': True})

# Cache em memória para textos extraídos dos PDFs (por token de sessão)
# Suporta múltiplos usuários simultâneos
import threading
_pdf_cache_lock = threading.Lock()
_pdf_cache_store = {}  # {session_token: {'dfp_text': '', 'fre_text': '', 'ts': float}}
_pdf_cache = {'dfp_text': '', 'fre_text': ''}  # fallback legado

def _get_session_token(req):
    return req.headers.get('X-Session-Token') or req.args.get('session') or 'default'

def _get_pdf_cache(token='default'):
    with _pdf_cache_lock:
        return dict(_pdf_cache_store.get(token, {'dfp_text': '', 'fre_text': ''}))

def _set_pdf_cache(token, key, value):
    import time
    with _pdf_cache_lock:
        if token not in _pdf_cache_store:
            _pdf_cache_store[token] = {'dfp_text': '', 'fre_text': '', 'ts': time.time()}
        _pdf_cache_store[token][key] = value
        _pdf_cache_store[token]['ts'] = time.time()
        # Limpar tokens com mais de 2h
        cutoff = time.time() - 7200
        expired = [k for k, v in _pdf_cache_store.items() if v.get('ts', 0) < cutoff]
        for k in expired:
            del _pdf_cache_store[k]

@app.route('/api/upload', methods=['POST'])
def upload():
    result = {'dfp': {}, 'fre': {}, 'errors': [], 'success': False}
    session_tok = _get_session_token(request)
    if 'dfp' in request.files:
        f = request.files['dfp']
        try:
            text = extract_text_pdf(f)
            _set_pdf_cache(session_tok, 'dfp_text', text)
            _pdf_cache['dfp_text'] = text  # backward compat
            result['dfp'] = parse_dfp(text)
            result['dfp']['_nome'] = f.filename
            result['dfp']['_chars'] = len(text)
            result['dfp']['_extraiu'] = sum(1 for v in result['dfp'].values() if v and str(v) not in ('', 'None', '0'))
        except Exception as e:
            import traceback
            result['errors'].append(f'DFP: {str(e)} — {traceback.format_exc()[-200:]}')
    if 'fre' in request.files:
        f = request.files['fre']
        try:
            text = extract_text_pdf(f)
            _set_pdf_cache(session_tok, 'fre_text', text)
            _pdf_cache['fre_text'] = text  # backward compat
            result['fre'] = parse_fre(text)
            result['fre']['_nome'] = f.filename
        except Exception as e:
            import traceback
            result['errors'].append(f'FRE: {str(e)} — {traceback.format_exc()[-200:]}')
    result['success'] = len(result['errors']) == 0
    return jsonify(result)

@app.route('/api/generate', methods=['POST'])
def generate():
    body = request.json or {}
    # Dados do formulário (preenchidos pelo usuário)
    form_dfp = body.get('dfp') or {}
    form_fre = body.get('fre') or {}
    # Re-parsear PDFs em cache se disponíveis
    cached_dfp = parse_dfp(_pdf_cache.get('dfp_text','')) if _pdf_cache.get('dfp_text') else {}
    cached_fre = parse_fre(_pdf_cache.get('fre_text','')) if _pdf_cache.get('fre_text') else {}
    # Prioridade: formulário > PDF > zero
    # Se o campo veio do formulário com valor real, usa formulário
    # Se não, usa o que veio do PDF
    def merge(form, pdf):
        result = dict(pdf)  # começa com PDF
        for k, v in form.items():
            if v not in (None, '', 0, 0.0):  # formulário tem dado real
                result[k] = v
        return result
    dfp_data = merge(form_dfp, cached_dfp)
    fre_data = merge(form_fre, cached_fre)
    macro = body.get('macro', {})
    # empresa_nome vem dentro do macro (enviado pelo frontend)
    macro.setdefault('empresa_nome', 'Empresa')
    macro.setdefault('empresa_ticker', 'TICK3')
    macro.setdefault('empresa_segmentos', '')
    macro.setdefault('empresa_database', '31/12/2025')
    macro.setdefault('recomendacao', 'MANTER')
    macro.setdefault('rating', 'B1/BB-')
    macro.setdefault('rating_br', 'brBB')
    macro.setdefault('tese_resumo', '')
    macro.setdefault('usd_brl', 5.80)
    macro.setdefault('selic', 14.75)
    macro.setdefault('ipca', 5.00)
    macro.setdefault('cdi', 14.65)
    macro.setdefault('minerio_fe', 102)
    macro.setdefault('hrc', 575)
    macro.setdefault('treasury_10y', 4.30)
    macro.setdefault('spread_alvo', 425)
    macro.setdefault('brent', 78)
    # Detectar setor automaticamente se não fornecido
    if not macro.get('setor'):
        setor_detect = _setor_do_texto(
            dfp_data.get('descricao','') or macro.get('empresa_segmentos',''),
            macro.get('empresa_ticker','')
        )
        if setor_detect:
            macro['setor'] = setor_detect
    # Calcular scores avançados e incluir no macro para uso no PDF
    try:
        _pf = calcular_piotroski(dfp_data)
        _dscr = calcular_dscr(dfp_data, fre_data)
        _div_sust = calcular_dividend_sustainability(dfp_data)
        macro['_piotroski'] = _pf
        macro['_dscr'] = _dscr
        macro['_dividend_sust'] = _div_sust
    except Exception:
        pass
    try:
        pdf_bytes = gerar_pdf(dfp_data, fre_data, macro)
        _salvar_relatorio(pdf_bytes, macro)
        return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf',
                         as_attachment=True, download_name=f"{macro.get('empresa_ticker','Empresa').replace(' ','_')}_Analise_Credito.pdf")
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


import os, json, hashlib
from datetime import datetime

# ── Cache e storage de relatórios ─────────────────────────────────
REPORTS_DIR = os.path.join(os.path.dirname(__file__), 'reports_history')
os.makedirs(REPORTS_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════════════════
# MAPA DE PEERS POR SETOR
# ══════════════════════════════════════════════════════════════════
PEERS_MAP = {
    'mineracao':  ['VALE3','CMIN3','CSNA3'],
    'siderurgia': ['CSNA3','GGBR4','USIM5','GCPG3'],
    'petroleo':   ['PETR4','PETR3','PRIO3','RECV3','RRRP3','CSAN3'],
    'varejo':     ['MGLU3','LREN3','AMER3','PCAR3','CRFB3','SOMA3'],
    'saude':      ['HAPV3','RDRD3','FLRY3','DASA3','PNVL3','ONCO3'],
    'agro':       ['SLCE3','SMTO3','JALL3','MRFG3','BEEF3','CAML3'],
    'bancos':     ['ITUB4','BBDC4','BBAS3','SANB11','BRSR6','BPAC11'],
    'telecom':    ['VIVT3','TIMS3','OIBR3'],
    'energia':    ['EGIE3','CPFE3','ENGI11','CMIG4','ENBR3','TAEE11'],
    'logistica':  ['RAIL3','ECOR3','TGMA3','VLID3'],
    'imobiliario':['CYRE3','MRVE3','EZTC3','TEND3','DIRR3'],
    'papel':      ['SUZB3','KLBN11','DXCO3'],
    'alimentos':  ['ABEV3','BRFS3','JBSS3','MDIA3'],
    'educacao':   ['COGN3','YDUQ3','ANIM3'],
    'seguros':    ['BBSE3','SULA11','PSSA3'],
}

def _setor_do_texto(texto, ticker=''):
    t = (texto or '').lower()
    tk = (ticker or '').upper()
    if 'miner' in t or 'ferro' in t or tk in ['VALE3','CMIN3']: return 'mineracao'
    if 'sider' in t or 'aço' in t or tk in ['CSNA3','GGBR4','USIM5']: return 'siderurgia'
    if 'petro' in t or 'óleo' in t or 'gás' in t or tk in ['PETR4','PETR3','PRIO3']: return 'petroleo'
    if 'varejo' in t or 'comércio' in t or tk in ['MGLU3','LREN3','AMER3']: return 'varejo'
    if 'saúde' in t or 'saude' in t or 'hospital' in t or tk in ['HAPV3','RDRD3','FLRY3']: return 'saude'
    if 'agro' in t or 'soja' in t or 'carne' in t or tk in ['SLCE3','SMTO3','JALL3']: return 'agro'
    if 'banco' in t or 'financ' in t or tk in ['ITUB4','BBDC4','BBAS3','SANB11']: return 'bancos'
    if 'telecom' in t or 'celular' in t or tk in ['VIVT3','TIMS3']: return 'telecom'
    if 'energia' in t or 'elétric' in t or tk in ['EGIE3','CPFE3','CMIG4']: return 'energia'
    if 'logíst' in t or 'ferrovi' in t or tk in ['RAIL3','ECOR3']: return 'logistica'
    if 'papel' in t or 'celulose' in t or tk in ['SUZB3','KLBN11']: return 'papel'
    if 'alimento' in t or 'carne' in t or tk in ['ABEV3','BRFS3','JBSS3']: return 'alimentos'
    if 'educ' in t or tk in ['COGN3','YDUQ3']: return 'educacao'
    if 'seguro' in t or tk in ['BBSE3','SULA11']: return 'seguros'
    return None

# ══════════════════════════════════════════════════════════════════
# SCORECARD DE RATING POR SETOR
# ══════════════════════════════════════════════════════════════════
SCORECARD_PESOS = {
    'default': {
        'alavancagem':     {'peso': 0.30, 'desc': 'DL/EBITDA'},
        'icj':             {'peso': 0.20, 'desc': 'ICJ (EBITDA/Juros)'},
        'liquidez':        {'peso': 0.15, 'desc': 'Caixa/Dívida CP'},
        'margem':          {'peso': 0.15, 'desc': 'Margem EBITDA'},
        'fco':             {'peso': 0.10, 'desc': 'FCO/Receita'},
        'escala':          {'peso': 0.10, 'desc': 'Escala (Receita)'},
    },
    'bancos': {
        'basileia':        {'peso': 0.30, 'desc': 'Basileia Tier 1'},
        'inadimplencia':   {'peso': 0.25, 'desc': 'Inadimplência >90d'},
        'nim':             {'peso': 0.20, 'desc': 'NIM'},
        'eficiencia':      {'peso': 0.15, 'desc': 'Índice de Eficiência'},
        'liquidez':        {'peso': 0.10, 'desc': 'LCR'},
    },
    'energia': {
        'alavancagem':     {'peso': 0.25, 'desc': 'DL/EBITDA'},
        'icj':             {'peso': 0.20, 'desc': 'ICJ'},
        'contrato':        {'peso': 0.25, 'desc': '% Receita Contratada'},
        'liquidez':        {'peso': 0.15, 'desc': 'Liquidez'},
        'margem':          {'peso': 0.15, 'desc': 'Margem EBITDA'},
    },
}

def calcular_scorecard(dfp_data, setor):
    """Calcula score de crédito 0-10 baseado nos indicadores."""
    eb  = dfp_data.get('ebitda_ajustado') or 0
    dl  = dfp_data.get('divida_liquida') or 0
    rl  = dfp_data.get('receita_liquida') or 0
    cx  = dfp_data.get('caixa') or 0
    jur = dfp_data.get('juros_pagos') or 0
    fco = dfp_data.get('fco') or 0
    dcp = dfp_data.get('divida_cp') or dfp_data.get('dcp') or 0

    alav = dl/eb if eb > 0 else 99
    icj  = eb/jur if jur > 0 else 0
    liq  = cx/dcp if dcp > 0 else (2 if cx > 0 else 0)
    mg   = eb/rl*100 if rl > 0 else 0
    fco_r = fco/rl*100 if rl > 0 else 0

    def score_alav(x):
        if x <= 1: return 10
        if x <= 1.5: return 9
        if x <= 2: return 8
        if x <= 2.5: return 7
        if x <= 3: return 6
        if x <= 3.5: return 5
        if x <= 4: return 4
        if x <= 5: return 3
        if x <= 6: return 2
        return 1

    def score_icj(x):
        if x >= 6: return 10
        if x >= 4: return 8
        if x >= 3: return 7
        if x >= 2: return 6
        if x >= 1.5: return 5
        if x >= 1: return 4
        return 2

    def score_liq(x):
        if x >= 2: return 10
        if x >= 1.5: return 8
        if x >= 1: return 6
        if x >= 0.75: return 4
        return 2

    def score_mg(x):
        if x >= 35: return 10
        if x >= 25: return 8
        if x >= 20: return 7
        if x >= 15: return 6
        if x >= 10: return 5
        if x >= 5: return 3
        return 1

    def score_fco(x):
        if x >= 15: return 10
        if x >= 10: return 8
        if x >= 5: return 6
        if x >= 0: return 4
        return 2

    def score_escala(r):
        if r >= 50000: return 10
        if r >= 20000: return 8
        if r >= 10000: return 7
        if r >= 5000: return 6
        if r >= 2000: return 5
        if r >= 500: return 4
        return 3

    scores = {
        'alavancagem': score_alav(alav),
        'icj':         score_icj(icj),
        'liquidez':    score_liq(liq),
        'margem':      score_mg(mg),
        'fco':         score_fco(fco_r),
        'escala':      score_escala(rl),
    }
    pesos = SCORECARD_PESOS.get(setor, SCORECARD_PESOS['default'])
    total_peso = sum(p['peso'] for p in pesos.values())
    score_final = 0
    detalhes = []
    for k, cfg in pesos.items():
        s = scores.get(k, 5)
        contrib = s * cfg['peso']
        score_final += contrib
        detalhes.append({
            'fator': cfg['desc'],
            'score': round(s, 1),
            'peso': f"{cfg['peso']*100:.0f}%",
            'contribuicao': round(contrib, 2),
        })

    score_final = min(10, max(1, score_final / total_peso * total_peso))

    # Mapear score para rating
    def score_to_rating(s):
        if s >= 9.5: return ('brAAA', 'Aaa/AAA', 'IG Alto')
        if s >= 8.5: return ('brAA', 'Aa2/AA', 'IG Alto')
        if s >= 7.5: return ('brA+', 'A1/A+', 'IG Médio')
        if s >= 6.5: return ('brA-', 'Baa1/BBB+', 'IG Médio')
        if s >= 5.5: return ('brBBB', 'Baa3/BBB-', 'IG Baixo')
        if s >= 4.5: return ('brBB+', 'Ba1/BB+', 'HY Alto')
        if s >= 3.5: return ('brBB', 'Ba2/BB', 'HY Médio')
        if s >= 2.5: return ('brB+', 'B1/B+', 'HY Baixo')
        if s >= 1.5: return ('brB-', 'B3/B-', 'Especulativo')
        return ('brCCC', 'Caa1/CCC', 'Distressed')

    br, gl, cat = score_to_rating(score_final)
    return {
        'score': round(score_final, 2),
        'rating_br': br,
        'rating_global': gl,
        'categoria': cat,
        'detalhes': detalhes,
        'inputs': {
            'alavancagem': round(alav, 2) if alav < 99 else None,
            'icj': round(icj, 2),
            'liquidez': round(liq, 2),
            'margem_ebitda': round(mg, 1),
            'fco_receita': round(fco_r, 1),
        }
    }

# ══════════════════════════════════════════════════════════════════
# ALTMAN Z-SCORE (versão mercados emergentes)
# ══════════════════════════════════════════════════════════════════
def calcular_zscore(dfp_data, market_cap_bi=None):
    """Altman Z-Score adaptado para mercados emergentes (Z\'').
    Z'' = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
    Zonas: >2.6 seguro, 1.1-2.6 cinza, <1.1 distress
    """
    try:
        at  = (dfp_data.get('ativo_total') or 0)
        ac  = (dfp_data.get('ativo_circulante') or dfp_data.get('caixa') or 0)
        pc  = (dfp_data.get('passivo_circulante') or dfp_data.get('divida_cp') or 0)
        laj = (dfp_data.get('ebitda_ajustado') or 0)
        ll  = (dfp_data.get('lucros_retidos') or dfp_data.get('lucro_liquido') or 0)
        pl  = (dfp_data.get('patrimonio_liquido') or 0)
        dt  = (dfp_data.get('divida_bruta') or 0)
        rl  = (dfp_data.get('receita_liquida') or 0)

        if at <= 0: return None

        X1 = (ac - pc) / at        # Capital de giro / Ativo total
        X2 = ll / at               # Lucros retidos / Ativo total
        X3 = laj / at              # LAJIR / Ativo total
        X4 = pl / dt if dt > 0 else 1  # PL / Dívida total

        Z = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4

        if Z > 2.6: zona = 'segura'
        elif Z > 1.1: zona = 'cinza'
        else: zona = 'distress'

        return {
            'z_score': round(Z, 3),
            'zona': zona,
            'zona_desc': {
                'segura': 'Zona Segura (Z > 2,6) — baixo risco de default',
                'cinza': 'Zona Cinza (1,1 < Z < 2,6) — risco moderado, monitorar',
                'distress': 'Zona de Distress (Z < 1,1) — alto risco de insolvência',
            }[zona],
            'componentes': {
                'X1_capital_giro': round(X1, 4),
                'X2_lucros_retidos': round(X2, 4),
                'X3_rentabilidade': round(X3, 4),
                'X4_solvencia': round(X4, 4),
            }
        }
    except:
        return None



# ══════════════════════════════════════════════════════════════════
# PIOTROSKI F-SCORE (9 pontos)
# Sistema desenvolvido por Joseph Piotroski (2000)
# Avalia qualidade financeira em 3 dimensões: Rentabilidade, Alavancagem/Liquidez, Eficiência Operacional
# ══════════════════════════════════════════════════════════════════
def calcular_piotroski(dfp_data, dfp_anterior=None):
    """
    Calcula Piotroski F-Score (0-9).
    0-2 = empresa fraca, 7-9 = empresa forte.
    dfp_anterior: dados do período anterior (para calcular variações).
    """
    d = dfp_data
    dp = dfp_anterior or {}

    pontos = {}
    detalhes = []

    # ── PILAR 1: RENTABILIDADE (4 pontos) ─────────────────────────
    # F1: ROA positivo (Lucro Líquido / Ativo Total)
    ll = d.get('lucro_liquido') or 0
    at = d.get('ativo_total') or 0
    roa = ll / at if at > 0 else 0
    pontos['F1_ROA'] = 1 if roa > 0 else 0
    detalhes.append({'criterio': 'F1 — ROA positivo', 'valor': f'{roa*100:.2f}%', 'ponto': pontos['F1_ROA'],
                     'desc': 'Lucro líquido / Ativo total > 0'})

    # F2: FCO positivo
    fco = d.get('fco') or 0
    pontos['F2_FCO'] = 1 if fco > 0 else 0
    detalhes.append({'criterio': 'F2 — FCO positivo', 'valor': f'{fco:,.0f}', 'ponto': pontos['F2_FCO'],
                     'desc': 'Fluxo de caixa operacional > 0'})

    # F3: Melhora do ROA (comparar com período anterior)
    roa_ant = (dp.get('lucro_liquido', 0) or 0) / (dp.get('ativo_total', 1) or 1) if dp else None
    if roa_ant is not None:
        pontos['F3_DELTA_ROA'] = 1 if roa > roa_ant else 0
        detalhes.append({'criterio': 'F3 — ROA melhorou', 'valor': f'{roa*100:.2f}% vs {roa_ant*100:.2f}%',
                         'ponto': pontos['F3_DELTA_ROA'], 'desc': 'ROA atual > ROA anterior'})
    else:
        pontos['F3_DELTA_ROA'] = 0
        detalhes.append({'criterio': 'F3 — ROA melhorou', 'valor': 'Sem período anterior',
                         'ponto': 0, 'desc': 'Requer dados do período anterior'})

    # F4: Accrual (FCO > ROA — qualidade do lucro)
    accrual = roa - (fco / at if at > 0 else 0)
    pontos['F4_ACCRUAL'] = 1 if fco / at > roa and at > 0 else 0
    detalhes.append({'criterio': 'F4 — FCO > ROA (qualidade do lucro)', 'valor': f'FCO/AT={fco/at*100:.2f}% vs ROA={roa*100:.2f}%' if at > 0 else 'AT=0',
                     'ponto': pontos['F4_ACCRUAL'], 'desc': 'FCO/Ativo > ROA (lucro de caixa, não contábil)'})

    # ── PILAR 2: ALAVANCAGEM / LIQUIDEZ (3 pontos) ────────────────
    # F5: Redução de alavancagem (Dívida Longo Prazo / Ativo Total)
    dlp = (d.get('passivo_nao_circulante') or d.get('divida_bruta') or 0)
    lev = dlp / at if at > 0 else 0
    dlp_ant = (dp.get('passivo_nao_circulante') or dp.get('divida_bruta') or 0) if dp else None
    at_ant = (dp.get('ativo_total') or at) if dp else at
    lev_ant = dlp_ant / at_ant if (dlp_ant is not None and at_ant > 0) else None
    if lev_ant is not None:
        pontos['F5_LEVER'] = 1 if lev < lev_ant else 0
        detalhes.append({'criterio': 'F5 — Alavancagem reduziu', 'valor': f'{lev:.3f} vs {lev_ant:.3f}',
                         'ponto': pontos['F5_LEVER'], 'desc': 'Dívida LP / Ativo caiu'})
    else:
        pontos['F5_LEVER'] = 1 if lev < 0.5 else 0
        detalhes.append({'criterio': 'F5 — Alavancagem (LP/AT)', 'valor': f'{lev:.3f}',
                         'ponto': pontos['F5_LEVER'], 'desc': 'Sem período anterior — bonus se < 0,5'})

    # F6: Melhora de liquidez corrente
    ac = d.get('ativo_circulante') or 0
    pc = d.get('passivo_circulante') or 0
    liq_c = ac / pc if pc > 0 else 2.0
    ac_ant = (dp.get('ativo_circulante') or ac) if dp else ac
    pc_ant = (dp.get('passivo_circulante') or pc) if dp else pc
    liq_c_ant = ac_ant / pc_ant if pc_ant > 0 else 2.0
    pontos['F6_LIQUID'] = 1 if liq_c >= liq_c_ant else 0
    detalhes.append({'criterio': 'F6 — Liquidez corrente melhorou', 'valor': f'{liq_c:.2f} vs {liq_c_ant:.2f}',
                     'ponto': pontos['F6_LIQUID'], 'desc': 'Ativo Circ / Passivo Circ atual ≥ anterior'})

    # F7: Sem emissão de novas ações (diluição)
    acoes = d.get('acoes_emitidas') or 0
    acoes_ant = (dp.get('acoes_emitidas') or acoes) if dp else acoes
    pontos['F7_SHARES'] = 1 if acoes <= acoes_ant or acoes_ant == 0 else 0
    detalhes.append({'criterio': 'F7 — Sem emissão de ações', 'valor': f'{acoes:,.0f} vs {acoes_ant:,.0f}',
                     'ponto': pontos['F7_SHARES'], 'desc': 'Nº ações não aumentou (sem diluição)'})

    # ── PILAR 3: EFICIÊNCIA OPERACIONAL (2 pontos) ────────────────
    # F8: Melhora de margem bruta
    rl = d.get('receita_liquida') or 0
    lb = d.get('lucro_bruto') or 0
    mg_b = lb / rl if rl > 0 else 0
    rl_ant = (dp.get('receita_liquida') or rl) if dp else rl
    lb_ant = (dp.get('lucro_bruto') or lb) if dp else lb
    mg_b_ant = lb_ant / rl_ant if rl_ant > 0 else 0
    pontos['F8_GROSS_MG'] = 1 if mg_b >= mg_b_ant else 0
    detalhes.append({'criterio': 'F8 — Margem bruta melhorou', 'valor': f'{mg_b*100:.1f}% vs {mg_b_ant*100:.1f}%',
                     'ponto': pontos['F8_GROSS_MG'], 'desc': 'Lucro Bruto / Receita atual ≥ anterior'})

    # F9: Melhora de giro do ativo
    giro = rl / at if at > 0 else 0
    rl_ant2 = (dp.get('receita_liquida') or rl) if dp else rl
    at_ant2 = (dp.get('ativo_total') or at) if dp else at
    giro_ant = rl_ant2 / at_ant2 if at_ant2 > 0 else 0
    pontos['F9_ASSET_TURN'] = 1 if giro >= giro_ant else 0
    detalhes.append({'criterio': 'F9 — Giro do ativo melhorou', 'valor': f'{giro:.3f} vs {giro_ant:.3f}',
                     'ponto': pontos['F9_ASSET_TURN'], 'desc': 'Receita / Ativo Total atual ≥ anterior'})

    f_score = sum(pontos.values())

    if f_score >= 7:   qualidade = 'FORTE'
    elif f_score >= 5: qualidade = 'MODERADA'
    elif f_score >= 3: qualidade = 'FRACA'
    else:              qualidade = 'MUITO FRACA'

    return {
        'f_score': f_score,
        'qualidade': qualidade,
        'pontos': pontos,
        'detalhes': detalhes,
        'pilares': {
            'rentabilidade': sum(pontos.get(k, 0) for k in ['F1_ROA','F2_FCO','F3_DELTA_ROA','F4_ACCRUAL']),
            'alavancagem_liquidez': sum(pontos.get(k, 0) for k in ['F5_LEVER','F6_LIQUID','F7_SHARES']),
            'eficiencia': sum(pontos.get(k, 0) for k in ['F8_GROSS_MG','F9_ASSET_TURN']),
        },
        'interpretacao': {
            'FORTE': 'Empresa financeiramente sólida — alta qualidade',
            'MODERADA': 'Empresa em situação intermediária — monitorar',
            'FRACA': 'Sinais de fragilidade financeira — cautela',
            'MUITO FRACA': 'Alta probabilidade de deterioração — evitar exposição',
        }[qualidade]
    }


# ══════════════════════════════════════════════════════════════════
# DSCR — DEBT SERVICE COVERAGE RATIO
# Métrica central de crédito corporativo (bancos e bonds)
# ══════════════════════════════════════════════════════════════════
def calcular_dscr(dfp_data, fre_data=None):
    """
    DSCR = FCO / (Amortização + Juros Pagos)
    DSCR > 1.25 = saudável | 1.0-1.25 = monitorar | < 1.0 = risco de default
    """
    fco    = dfp_data.get('fco') or 0
    juros  = dfp_data.get('juros_pagos') or 0
    venc   = (fre_data or {}).get('vencimentos') or {}
    # Amortizações do próximo ano
    amort  = venc.get('2026') or venc.get('2025') or 0
    debt_service = juros + amort

    if debt_service <= 0:
        # Fallback: usar resultado financeiro
        res_fin = abs(dfp_data.get('resultado_financeiro') or 0)
        debt_service = res_fin

    if debt_service <= 0 or fco == 0:
        return None

    dscr_val = fco / debt_service

    if dscr_val >= 1.5:   nivel = 'SEGURO'
    elif dscr_val >= 1.25: nivel = 'ADEQUADO'
    elif dscr_val >= 1.0:  nivel = 'LIMITE'
    else:                  nivel = 'RISCO'

    return {
        'dscr': round(dscr_val, 3),
        'nivel': nivel,
        'fco': fco,
        'juros_pagos': juros,
        'amortizacao_cp': amort,
        'debt_service': debt_service,
        'interpretacao': {
            'SEGURO': 'DSCR ≥ 1,5 — FCO cobre confortavelmente o serviço da dívida',
            'ADEQUADO': 'DSCR 1,25-1,5 — FCO cobre o serviço da dívida com margem',
            'LIMITE': 'DSCR 1,0-1,25 — FCO mal cobre o serviço da dívida, sem margem de segurança',
            'RISCO': 'DSCR < 1,0 — FCO INSUFICIENTE para cobrir o serviço da dívida',
        }[nivel]
    }


# ══════════════════════════════════════════════════════════════════
# SCORE DE SUSTENTABILIDADE DE DIVIDENDOS
# ══════════════════════════════════════════════════════════════════
def calcular_dividend_sustainability(dfp_data):
    """
    Avalia se os dividendos são sustentáveis com base em:
    - Payout ratio vs. FCO (não vs. lucro contábil)
    - Dívida / EBITDA (empresas alavancadas não deveriam pagar dividendos altos)
    - Histórico de crescimento de FCO
    Retorna score 0-10 e nível de sustentabilidade.
    """
    ll       = dfp_data.get('lucro_liquido') or 0
    fco      = dfp_data.get('fco') or 0
    div_pago = dfp_data.get('dividendos_pagos') or 0
    eb       = dfp_data.get('ebitda_ajustado') or 0
    dl       = dfp_data.get('divida_liquida') or 0
    capex    = dfp_data.get('capex') or 0

    if div_pago == 0:
        return {'nivel': 'SEM DIVIDENDOS', 'score': None, 'payout_fco': None,
                'free_cash_flow': None, 'interpretacao': 'Empresa não pagou dividendos no período.'}

    fcl = fco - capex  # Free Cash Flow (FCO - CAPEX)
    payout_lucro = div_pago / ll if ll > 0 else 999
    payout_fco   = div_pago / fco if fco > 0 else 999
    payout_fcl   = div_pago / fcl if fcl > 0 else 999
    alav         = dl / eb if eb > 0 else 99

    score = 10
    flags = []

    if payout_fco > 1.0:
        score -= 3
        flags.append(f'Payout FCO > 100% ({payout_fco*100:.0f}%) — dividendo pago com endividamento')
    elif payout_fco > 0.8:
        score -= 1
        flags.append(f'Payout FCO alto ({payout_fco*100:.0f}%) — pouca margem de segurança')

    if payout_fcl > 1.0:
        score -= 2
        flags.append(f'Payout FCL > 100% ({payout_fcl*100:.0f}%) — sem FCL após CAPEX')

    if alav > 3.5:
        score -= 2
        flags.append(f'Alavancagem {alav:.1f}x — DL/EBITDA elevado limita distribuição')
    elif alav > 2.5:
        score -= 1
        flags.append(f'Alavancagem {alav:.1f}x — monitorar capacidade de distribuição')

    if fco < 0:
        score -= 3
        flags.append('FCO negativo — dividendo insustentável')

    score = max(0, min(10, score))

    if score >= 8:   nivel = 'ALTA SUSTENTABILIDADE'
    elif score >= 6: nivel = 'MODERADA'
    elif score >= 4: nivel = 'BAIXA'
    else:            nivel = 'INSUSTENTÁVEL'

    return {
        'nivel': nivel,
        'score': score,
        'payout_lucro': round(payout_lucro * 100, 1) if ll > 0 else None,
        'payout_fco': round(payout_fco * 100, 1) if fco > 0 else None,
        'payout_fcl': round(payout_fcl * 100, 1) if fcl > 0 else None,
        'free_cash_flow': fcl,
        'dividendos_pagos': div_pago,
        'flags': flags,
        'interpretacao': {
            'ALTA SUSTENTABILIDADE': 'Dividendos cobertos pelo FCO e FCL — distribuição saudável',
            'MODERADA': 'Dividendos pagos mas com margem limitada — acompanhar',
            'BAIXA': 'Riscos de sustentabilidade — dividendo pode ser cortado',
            'INSUSTENTÁVEL': 'Dividendo insustentável — risco de suspensão',
        }.get(nivel, nivel)
    }

# ══════════════════════════════════════════════════════════════════
# ROTA: /api/peers/<ticker>
# ══════════════════════════════════════════════════════════════════
@app.route('/api/peers/<ticker>')
def get_peers(ticker):
    """Busca peers do mesmo setor e retorna dados comparativos via Brapi."""
    import requests as rq
    BRAPI_TOKEN = os.environ.get('BRAPI_TOKEN','ucaHWHuWF7tLMv47tpzQB8')
    tk = ticker.upper()
    # Detectar setor
    setor = None
    for s, tickers in PEERS_MAP.items():
        if tk in tickers:
            setor = s
            break
    if not setor:
        # Tentar via query string
        setor = request.args.get('setor', '')

    peers = list(set(PEERS_MAP.get(setor, []) + [tk]))[:6]

    resultado = {'ticker': tk, 'setor': setor, 'peers': []}
    tickers_str = ','.join(peers)
    try:
        r = rq.get(f'https://brapi.dev/api/quote/{tickers_str}',
                   params={'token': BRAPI_TOKEN,
                           'modules': 'defaultKeyStatistics,financialData'},
                   timeout=12)
        if r.status_code == 200:
            for q in r.json().get('results', []):
                resultado['peers'].append({
                    'ticker': q.get('symbol',''),
                    'nome': q.get('shortName',''),
                    'cotacao': q.get('regularMarketPrice'),
                    'variacao': q.get('regularMarketChangePercent'),
                    'market_cap_bi': round(q.get('marketCap',0)/1e9, 2) if q.get('marketCap') else None,
                    'pl': q.get('trailingPE') or q.get('priceEarningsRatio'),
                    'pvp': q.get('priceToBook'),
                    'ev_ebitda': q.get('enterpriseToEbitda'),
                    'dividend_yield': q.get('dividendYield'),
                    'beta': q.get('beta'),
                    'eh_principal': q.get('symbol','').upper() == tk,
                })
    except Exception as e:
        resultado['erro'] = str(e)

    return jsonify(resultado)

# ══════════════════════════════════════════════════════════════════
# ROTA: /api/scorecard
# ══════════════════════════════════════════════════════════════════
@app.route('/api/scorecard', methods=['POST'])
def scorecard():
    """Calcula scorecard de rating e Z-Score a partir dos dados financeiros."""
    body = request.json or {}
    dfp_data = body.get('dfp', {})
    setor = body.get('setor', 'default')
    market_cap_bi = body.get('market_cap_bi')

    sc  = calcular_scorecard(dfp_data, setor)
    zs  = calcular_zscore(dfp_data, market_cap_bi)
    pf  = calcular_piotroski(dfp_data, body.get('dfp_anterior'))
    dscr = calcular_dscr(dfp_data, body.get('fre', {}))
    div_sust = calcular_dividend_sustainability(dfp_data)

    return jsonify({
        'scorecard': sc,
        'zscore': zs,
        'piotroski': pf,
        'dscr': dscr,
        'dividend_sustainability': div_sust,
    })

# ══════════════════════════════════════════════════════════════════
# ROTA: /api/historico/<ticker>
# ══════════════════════════════════════════════════════════════════
@app.route('/api/historico/<ticker>')
def historico_financeiro(ticker):
    """Busca histórico financeiro trimestral via CVM (últimos 8 trimestres)."""
    import requests as rq, io, zipfile, csv

    tk = ticker.upper()
    anos = [2023, 2024, 2025]
    series = {}

    try:
        cvm = _load_cvm_cache()
        # Encontrar cod_cvm pelo ticker
        cod_cvm = None
        for cnpj_raw, info in cvm.items():
            if info.get('nome_pregao','').upper() in tk or tk in info.get('nome_pregao','').upper():
                cod_cvm = info.get('cod_cvm')
                break

        if not cod_cvm:
            # Tentar via Brapi — retornar série histórica simplificada
            BRAPI_TOKEN = os.environ.get('BRAPI_TOKEN','ucaHWHuWF7tLMv47tpzQB8')
            r = rq.get(f'https://brapi.dev/api/quote/{tk}',
                       params={'token': BRAPI_TOKEN,
                               'modules': 'incomeStatementHistory,balanceSheetHistory,cashflowStatementHistory'},
                       timeout=12)
            if r.status_code == 200:
                res = r.json().get('results', [{}])[0]
                income = res.get('incomeStatementHistory', {}).get('incomeStatementHistory', [])
                balance = res.get('balanceSheetHistory', {}).get('balanceSheetHistory', [])
                series['fonte'] = 'brapi'
                series['trimestres'] = []
                for i, item in enumerate(income[:8]):
                    dt = item.get('endDate', {}).get('fmt', f'T{i+1}')
                    bal = balance[i] if i < len(balance) else {}
                    series['trimestres'].append({
                        'periodo': dt,
                        'receita': item.get('totalRevenue', {}).get('raw'),
                        'ebitda': item.get('ebitda', {}).get('raw'),
                        'lucro': item.get('netIncome', {}).get('raw'),
                        'divida_total': bal.get('longTermDebt', {}).get('raw'),
                        'caixa': bal.get('cash', {}).get('raw'),
                    })
                return jsonify(series)

        series['fonte'] = 'cvm'
        series['cod_cvm'] = cod_cvm
        series['trimestres'] = []
        return jsonify(series)

    except Exception as e:
        return jsonify({'erro': str(e), 'trimestres': []})

# ══════════════════════════════════════════════════════════════════
# ROTA: /api/debentures/<identificador>
# ══════════════════════════════════════════════════════════════════
@app.route('/api/debentures/<identificador>')
def buscar_debentures(identificador):
    """Busca debêntures, CRAs e CRIs via debentures.com.br."""
    import requests as rq
    resultado = {'emissoes': [], 'fonte': 'debentures.com.br'}
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # API pública da ANBIMA / Status Invest como alternativa
        r = rq.get(
            f'https://sistemasweb.b3.com.br/DebenturesToPublic/rest/debentures/emissao?search={identificador}',
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json() if r.headers.get('content-type','').startswith('application/json') else {}
            emissoes = data.get('debentures', data.get('result', []))
            for e in emissoes[:10]:
                resultado['emissoes'].append({
                    'codigo': e.get('codigoEmissao') or e.get('codigo',''),
                    'emissor': e.get('nomeEmissor') or e.get('emissor',''),
                    'tipo': e.get('tipoDebenture', 'Debênture'),
                    'vencimento': e.get('dataVencimento') or e.get('vencimento',''),
                    'taxa': e.get('taxaEmissao') or e.get('taxa',''),
                    'volume': e.get('volumeTotal') or e.get('volume'),
                    'rating': e.get('ratingEmissao') or e.get('rating',''),
                    'indexador': e.get('indexador',''),
                })
        else:
            # Fallback: Status Invest
            r2 = rq.get(
                f'https://statusinvest.com.br/debentures/GetDebentures',
                params={'search': identificador, 'type': 0},
                headers=headers, timeout=10
            )
            if r2.status_code == 200:
                try:
                    items = r2.json().get('list', [])
                    for e in items[:10]:
                        resultado['emissoes'].append({
                            'codigo': e.get('code',''),
                            'emissor': e.get('companyName',''),
                            'tipo': e.get('type','Debênture'),
                            'vencimento': e.get('maturityDate',''),
                            'taxa': e.get('indexerDescription','') + (f" + {e.get('rate','')}" if e.get('rate') else ''),
                            'volume': e.get('totalPapers'),
                            'rating': e.get('rating',''),
                            'indexador': e.get('indexer',''),
                        })
                    resultado['fonte'] = 'statusinvest'
                except: pass
    except Exception as e:
        resultado['erro'] = str(e)
    return jsonify(resultado)

# ══════════════════════════════════════════════════════════════════
# ROTA: /api/noticias/<ticker>
# ══════════════════════════════════════════════════════════════════
@app.route('/api/noticias/<ticker>')
def get_noticias(ticker):
    """Busca notícias via Google News RSS — por ticker e nome da empresa."""
    import urllib.request, urllib.parse
    from xml.etree import ElementTree as ET

    tk = ticker.upper().strip()
    nome = request.args.get('nome', tk)
    resultado = {'ticker': tk, 'noticias': []}

    # Tentar 2 queries: ticker e nome da empresa
    queries_raw = [
        f'{tk} B3 resultados',
        f'{nome} crédito debêntures' if nome != tk else f'{tk} debêntures bonds',
    ]
    vistas = set()

    for qr in queries_raw:
        try:
            q = urllib.parse.quote(qr)
            url = f'https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            with urllib.request.urlopen(req, timeout=12) as r:
                xml = r.read()
            root = ET.fromstring(xml)
            for item in root.findall('.//item')[:8]:
                titulo = item.findtext('title','').split(' - ')[0].strip()
                link   = item.findtext('link','')
                pub    = item.findtext('pubDate','')
                fonte  = item.findtext('source','') or item.findtext('{http://purl.org/rss/1.0/modules/content/}source','')
                if titulo and titulo not in vistas:
                    vistas.add(titulo)
                    resultado['noticias'].append({
                        'titulo': titulo,
                        'link':   link,
                        'publicado': pub,
                        'fonte': fonte,
                    })
            if len(resultado['noticias']) >= 8:
                break
        except Exception as e:
            resultado['_erro_parcial'] = str(e)
            continue

    if not resultado['noticias']:
        resultado['aviso'] = 'Nenhuma notícia encontrada. Tente pesquisar diretamente no Google Notícias.'

    return jsonify(resultado)

# ══════════════════════════════════════════════════════════════════
# ROTA: /api/relatorios — histórico de relatórios gerados
# ══════════════════════════════════════════════════════════════════
@app.route('/api/relatorios', methods=['GET'])
def listar_relatorios():
    """Lista histórico de relatórios gerados."""
    try:
        meta_file = os.path.join(REPORTS_DIR, 'index.json')
        if not os.path.exists(meta_file):
            return jsonify({'relatorios': []})
        with open(meta_file) as f:
            data = json.load(f)
        return jsonify({'relatorios': data.get('relatorios', [])})
    except:
        return jsonify({'relatorios': []})

@app.route('/api/relatorios/<relatorio_id>', methods=['GET'])
def baixar_relatorio(relatorio_id):
    """Download de relatório salvo."""
    try:
        safe_id = ''.join(c for c in relatorio_id if c.isalnum() or c in '-_')
        path = os.path.join(REPORTS_DIR, f'{safe_id}.pdf')
        if not os.path.exists(path):
            return jsonify({'error': 'Relatório não encontrado'}), 404
        return send_file(path, mimetype='application/pdf', as_attachment=True,
                        download_name=f'{safe_id}.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/relatorios/<relatorio_id>', methods=['DELETE'])
def deletar_relatorio(relatorio_id):
    """Remove relatório do histórico."""
    try:
        safe_id = ''.join(c for c in relatorio_id if c.isalnum() or c in '-_')
        meta_file = os.path.join(REPORTS_DIR, 'index.json')
        with open(meta_file) as f:
            data = json.load(f)
        data['relatorios'] = [r for r in data.get('relatorios',[]) if r.get('id') != safe_id]
        with open(meta_file,'w') as f:
            json.dump(data, f)
        pdf_path = os.path.join(REPORTS_DIR, f'{safe_id}.pdf')
        if os.path.exists(pdf_path): os.remove(pdf_path)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _salvar_relatorio(pdf_bytes, macro):
    """Salva PDF gerado no histórico."""
    try:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        tk = macro.get('empresa_ticker','TICK').replace(' ','_').upper()
        rel_id = f"{tk}_{ts}"
        # Salvar PDF
        pdf_path = os.path.join(REPORTS_DIR, f'{rel_id}.pdf')
        with open(pdf_path, 'wb') as f:
            f.write(pdf_bytes)
        # Atualizar índice
        meta_file = os.path.join(REPORTS_DIR, 'index.json')
        data = {'relatorios': []}
        if os.path.exists(meta_file):
            with open(meta_file) as f:
                data = json.load(f)
        data['relatorios'].insert(0, {
            'id': rel_id,
            'ticker': macro.get('empresa_ticker',''),
            'empresa': macro.get('empresa_nome',''),
            'data': datetime.now().strftime('%d/%m/%Y %H:%M'),
            'recomendacao': macro.get('recomendacao',''),
            'rating_br': macro.get('rating_br',''),
            'rating_gl': macro.get('rating',''),
            'tamanho_kb': round(len(pdf_bytes)/1024, 1),
        })
        # Manter só os últimos 50
        data['relatorios'] = data['relatorios'][:50]
        with open(meta_file,'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        import traceback
        print(f'[WARN] _salvar_relatorio: {e}\n{traceback.format_exc()}')

# ══════════════════════════════════════════════════════════════════════════════
# CVM DFP AUTO-DOWNLOAD — integração com cvm_dfp.py
# ══════════════════════════════════════════════════════════════════════════════

def _importar_cvm_dfp():
    """Importa o módulo cvm_dfp.py de forma lazy para não quebrar a app se ausente."""
    try:
        import importlib.util, sys
        # Tenta importar do mesmo diretório da app
        base = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location('cvm_dfp', os.path.join(base, 'cvm_dfp.py'))
        if spec is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules['cvm_dfp'] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f'[AVISO] cvm_dfp.py não encontrado ou com erro: {e}')
        return None


@app.route('/api/cvm-dfp/<cnpj>')
def api_cvm_dfp(cnpj):
    """
    Busca dados financeiros (DFP/ITR) da CVM para um CNPJ.

    Query params:
      ano=2024          (opcional, padrão = ano anterior)
      trimestral=1      (opcional, força busca de ITR em vez de DFP anual)

    Retorna campos financeiros extraídos (mesma estrutura esperada por /api/generate):
      receita_liquida, lucro_liquido, ebitda, ativo_total, patrimonio_liquido,
      divida_bruta, caixa, fco, capex, dividendos_pagos, etc.
    """
    cvm = _importar_cvm_dfp()
    if cvm is None:
        return jsonify({'error': 'Módulo cvm_dfp.py não disponível. Verifique se o arquivo está na pasta da aplicação.'}), 503

    cnpj_clean = re.sub(r'\D', '', cnpj)
    if len(cnpj_clean) != 14:
        return jsonify({'error': 'CNPJ inválido — informe 14 dígitos.'}), 400

    # Formata com máscara
    cnpj_fmt = f'{cnpj_clean[:2]}.{cnpj_clean[2:5]}.{cnpj_clean[5:8]}/{cnpj_clean[8:12]}-{cnpj_clean[12:]}'

    from datetime import datetime as _dt
    ano_param = request.args.get('ano')
    trimestral = request.args.get('trimestral', '0') == '1'

    try:
        ano = int(ano_param) if ano_param else _dt.now().year - 1
    except ValueError:
        return jsonify({'error': 'Parâmetro ano inválido.'}), 400

    try:
        dados = cvm.buscar_dfp_por_cnpj(cnpj_fmt, ano, trimestral=trimestral)
    except Exception as e:
        import traceback
        print(f'[ERRO] api_cvm_dfp {cnpj_fmt}: {e}\n{traceback.format_exc()}')
        return jsonify({'error': f'Erro ao buscar dados CVM: {str(e)}'}), 500

    if not dados or dados.get('erro'):
        return jsonify({
            'aviso': dados.get('erro', 'Nenhum dado encontrado na CVM para este CNPJ.'),
            'cnpj': cnpj_fmt,
            'ano': ano,
            'dados': {}
        }), 200

    return jsonify({
        'cnpj': cnpj_fmt,
        'ano': ano,
        'trimestral': trimestral,
        'razao_social': dados.get('razao_social', ''),
        'cod_cvm': dados.get('cod_cvm', ''),
        'dados': dados,
    })


@app.route('/api/cvm-dfp/ticker/<ticker>')
def api_cvm_dfp_ticker(ticker):
    """
    Busca dados financeiros (DFP/ITR) da CVM pelo ticker B3.

    Query params: mesmo que /api/cvm-dfp/<cnpj>
    """
    cvm = _importar_cvm_dfp()
    if cvm is None:
        return jsonify({'error': 'Módulo cvm_dfp.py não disponível.'}), 503

    tk = ticker.upper().strip()
    from datetime import datetime as _dt
    ano_param = request.args.get('ano')
    trimestral = request.args.get('trimestral', '0') == '1'

    try:
        ano = int(ano_param) if ano_param else _dt.now().year - 1
    except ValueError:
        return jsonify({'error': 'Parâmetro ano inválido.'}), 400

    try:
        dados = cvm.buscar_dfp_por_ticker(tk, ano, trimestral=trimestral)
    except Exception as e:
        import traceback
        print(f'[ERRO] api_cvm_dfp_ticker {tk}: {e}\n{traceback.format_exc()}')
        return jsonify({'error': f'Erro ao buscar dados CVM: {str(e)}'}), 500

    if not dados or dados.get('erro'):
        return jsonify({
            'aviso': dados.get('erro', f'Ticker {tk} não encontrado no mapa CNPJ ou sem dados CVM.'),
            'ticker': tk,
            'dados': {}
        }), 200

    return jsonify({
        'ticker': tk,
        'ano': ano,
        'trimestral': trimestral,
        'razao_social': dados.get('razao_social', ''),
        'cnpj': dados.get('cnpj', ''),
        'cod_cvm': dados.get('cod_cvm', ''),
        'dados': dados,
    })


@app.route('/api/risco-cadastral/<cnpj>')
def api_risco_cadastral(cnpj):
    """
    Consolida dados de risco cadastral gratuitos para um CNPJ:
      - Situação cadastral (BrasilAPI / ReceitaWS)
      - Situação CVM (ativa, suspensa, cancelada)
      - Processos judiciais (DataJud/CNJ)
      - Notícias negativas (Google News RSS: recuperação judicial, falência, protesto)
      - Score de risco 0-10 e nível (BAIXO / MÉDIO / ALTO / CRÍTICO)

    Resposta:
      {
        "cnpj": "xx.xxx.xxx/xxxx-xx",
        "score_risco": float,      # 0 = sem risco, 10 = risco máximo
        "nivel_risco": str,        # BAIXO | MÉDIO | ALTO | CRÍTICO
        "situacao_receita": str,
        "situacao_cvm": str,
        "processos_judiciais": int,
        "noticias_negativas": [...],
        "alertas": [...],
        "detalhes": {...}
      }
    """
    cvm = _importar_cvm_dfp()
    if cvm is None:
        return jsonify({'error': 'Módulo cvm_dfp.py não disponível.'}), 503

    cnpj_clean = re.sub(r'\D', '', cnpj)
    if len(cnpj_clean) != 14:
        return jsonify({'error': 'CNPJ inválido — informe 14 dígitos.'}), 400

    cnpj_fmt = f'{cnpj_clean[:2]}.{cnpj_clean[2:5]}.{cnpj_clean[5:8]}/{cnpj_clean[8:12]}-{cnpj_clean[12:]}'

    try:
        resultado = cvm.buscar_risco_cadastral(cnpj_fmt)
    except Exception as e:
        import traceback
        print(f'[ERRO] api_risco_cadastral {cnpj_fmt}: {e}\n{traceback.format_exc()}')
        return jsonify({'error': f'Erro ao buscar risco cadastral: {str(e)}'}), 500

    return jsonify(resultado)


@app.route('/api/auto-preencher/<cnpj>')
def api_auto_preencher(cnpj):
    """
    Rota de conveniência para o frontend: busca CNPJ cadastral + DFP CVM
    em uma única chamada e retorna um dict pronto para pré-preencher o formulário.

    Retorna:
      {
        "empresa_nome": str,
        "empresa_ticker": str,
        "setor": str,
        "campos_dfp": { ... }  # todos os campos financeiros disponíveis
        "score_risco": float,
        "nivel_risco": str,
        "alertas": [...]
      }
    """
    cvm_mod = _importar_cvm_dfp()
    cnpj_clean = re.sub(r'\D', '', cnpj)
    if len(cnpj_clean) != 14:
        return jsonify({'error': 'CNPJ inválido.'}), 400
    cnpj_fmt = f'{cnpj_clean[:2]}.{cnpj_clean[2:5]}.{cnpj_clean[5:8]}/{cnpj_clean[8:12]}-{cnpj_clean[12:]}'

    from datetime import datetime as _dt
    resultado = {'cnpj': cnpj_fmt}

    # 1. Dados cadastrais (rota /api/cnpj já existente — chama internamente)
    try:
        import requests as _req
        port = int(os.environ.get('PORT', 5000))
        r = _req.get(f'http://localhost:{port}/api/cnpj/{cnpj_clean}', timeout=15)
        if r.ok:
            cad = r.json()
            resultado['empresa_nome']   = cad.get('razao_social', '')
            resultado['empresa_ticker'] = cad.get('ticker', '')
            resultado['setor']          = cad.get('setor', '')
            resultado['alertas']        = cad.get('alertas', [])
    except Exception as e:
        print(f'[AVISO] auto-preencher cadastral: {e}')
        resultado.setdefault('alertas', [])

    # 2. DFP CVM
    if cvm_mod:
        try:
            ano = _dt.now().year - 1
            dados_dfp = cvm_mod.buscar_dfp_por_cnpj(cnpj_fmt, ano)
            if dados_dfp and not dados_dfp.get('erro'):
                resultado['campos_dfp'] = dados_dfp
                resultado['razao_social_cvm'] = dados_dfp.get('razao_social', '')
                resultado['cod_cvm'] = dados_dfp.get('cod_cvm', '')
            else:
                resultado['campos_dfp'] = {}
                resultado['aviso_dfp'] = (dados_dfp or {}).get('erro', 'Sem dados DFP disponíveis na CVM.')
        except Exception as e:
            print(f'[AVISO] auto-preencher DFP: {e}')
            resultado['campos_dfp'] = {}
    else:
        resultado['campos_dfp'] = {}
        resultado['aviso_dfp'] = 'Módulo cvm_dfp.py não disponível.'

    # 3. Risco cadastral (resumido)
    if cvm_mod:
        try:
            risco = cvm_mod.buscar_risco_cadastral(cnpj_fmt)
            resultado['score_risco'] = risco.get('score_risco_cadastral', 0)
            resultado['nivel_risco'] = risco.get('nivel_risco_cadastral', 'BAIXO')
            # Mescla alertas
            alertas_risco = risco.get('alertas', [])
            resultado['alertas'] = resultado.get('alertas', []) + alertas_risco
        except Exception as e:
            print(f'[AVISO] auto-preencher risco: {e}')

    return jsonify(resultado)


if __name__ == '__main__':
    print('\n🚀  Dashboard Análise de Crédito → http://localhost:5000\n')
    app.run(debug=os.environ.get('FLASK_DEBUG','0')=='1', port=int(os.environ.get('PORT',5000)))
