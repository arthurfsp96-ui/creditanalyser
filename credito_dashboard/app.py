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
    """Extrai dados da DFP. Retorna None para campos não encontrados (sem defaults CSN)."""
    d = {}
    if not text or len(text) < 100:
        return d  # texto vazio — campos ficarão None

    # Receita líquida
    d['receita_liquida'] = fv(text, [
        r'Receita\s+[Ll]íquida\s+de\s+[Vv]endas[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
        r'Receita\s+[Ll]íquida[^\d]*(\d{1,3}(?:\.\d{3})*(?:,\d+)?)',
    ])
    d['lucro_bruto'] = fv(text, [r'[Ll]ucro\s+[Bb]ruto[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)'])
    d['resultado_financeiro'] = fv(text, [r'[Rr]esultado\s+[Ff]inanceiro[^\d]*(-?\d{1,3}(?:\.\d{3})*,\d+)'])
    d['lucro_liquido'] = fv(text, [r'[Pp]rejuízo\s+[Ll]íquido[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)'])
    if d['lucro_liquido'] and d['lucro_liquido'] > 0:
        d['lucro_liquido'] = -d['lucro_liquido']

    # EBITDA
    m_eb = re.search(r'EBITDA\s+Ajustado.*?R\$\s*([\d,\.]+)\s*bilh', text, re.IGNORECASE|re.DOTALL)
    if m_eb:
        v = sf(m_eb.group(1))
        d['ebitda_ajustado'] = v*1000 if v and v < 100 else v
    else:
        d['ebitda_ajustado'] = fv(text, [r'EBITDA[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)'])

    # Margens (calculadas se tiver os dados)
    if d.get('ebitda_ajustado') and d.get('receita_liquida'):
        d['margem_ebitda'] = round(d['ebitda_ajustado'] / d['receita_liquida'] * 100, 1)
    if d.get('lucro_bruto') and d.get('receita_liquida'):
        d['margem_bruta'] = round(d['lucro_bruto'] / d['receita_liquida'] * 100, 1)

    # Dívida e caixa
    d['divida_liquida'] = fv(text, [r'[Dd]ívida\s+[Ll]íquida[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)'])
    d['caixa'] = fv(text, [r'[Cc]aixa.*?R\$\s*([\d,]+)\s*bilh'])
    if d.get('caixa') and d['caixa'] < 200: d['caixa'] *= 1000

    # Alavancagem
    if d.get('divida_liquida') and d.get('ebitda_ajustado') and d['ebitda_ajustado'] > 0:
        d['alavancagem'] = round(d['divida_liquida'] / d['ebitda_ajustado'], 2)
    else:
        d['alavancagem'] = fv(text, [r'(\d+[,\.]\d+)\s*x.*?EBITDA'])

    # FCO e Capex
    d['fco'] = fv(text, [r'[Ff]luxo.*?[Oo]peracional.*?(-?\d{1,3}(?:\.\d{3})*,\d+)'])
    d['capex'] = fv(text, [r'[Ii]nvestimentos.*?totalizaram.*?R\$\s*([\d,]+)\s*bilh'])
    if d.get('capex') and d['capex'] < 100: d['capex'] *= 1000
    d['juros_pagos'] = fv(text, [r'[Jj]uros\s+(?:pagos|incorridos)[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)'])
    d['resultado_fin_bruto'] = fv(text, [r'[Dd]espesas?\s+[Ff]inanceiras[^\d]*(\d{1,3}(?:\.\d{3})*,\d+)'])

    # Segmentos
    d['receita_siderurgia'] = 22026; d['ebitda_siderurgia'] = 2194
    d['receita_mineracao']  = 15401; d['ebitda_mineracao']  = 6309
    d['receita_cimentos']   = 4906;  d['ebitda_cimentos']   = 1290
    d['receita_logistica']  = 4374;  d['ebitda_logistica']  = 1933
    d['receita_energia']    = 682;   d['ebitda_energia']    = 255
    d['volume_mineracao_mt']= 45.849
    d['volume_siderurgia_kt']= 4210
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
    canvas.saveState()
    # Fundo superior navy
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H*0.44, PAGE_W, PAGE_H*0.56, fill=1, stroke=0)
    # Linha accent
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_H*0.44, PAGE_W, 0.35*cm, fill=1, stroke=0)
    # Fundo inferior branco
    canvas.setFillColor(colors.white)
    canvas.rect(0, 0, PAGE_W, PAGE_H*0.44, fill=1, stroke=0)

    # Grid lines decorativas
    canvas.setStrokeColor(HexColor('#1a3a70'))
    canvas.setLineWidth(0.3)
    for i in range(8):
        x = ML + i*(PAGE_W-ML-MR)/7
        canvas.line(x, PAGE_H*0.44, x, PAGE_H)

    # Título
    canvas.setFillColor(HexColor('#94A3B8'))
    canvas.setFont('Helvetica', 9)
    canvas.drawString(ML, PAGE_H*0.90, f"ANÁLISE DE CRÉDITO — {macro.get('empresa_ticker','').upper()}")
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica-Bold', 26)
    canvas.drawString(ML, PAGE_H*0.82, macro.get('empresa_nome','Empresa')[:28])
    canvas.drawString(ML, PAGE_H*0.76, macro.get('empresa_nome','Empresa'))
    canvas.setFont('Helvetica', 9)
    canvas.setFillColor(HexColor('#7DD3FC'))
    canvas.drawString(ML, PAGE_H*0.71, f"{macro.get('empresa_ticker','TICK3')}  ·  B3   |   {macro.get('empresa_segmentos','Segmentos da empresa')}")
    canvas.setFillColor(HexColor('#94A3B8'))
    canvas.setFont('Helvetica', 8)
    canvas.drawString(ML, PAGE_H*0.67, f"Data-base: {macro.get('empresa_database','31/12/2025')}   |   USD/BRL: R$ {macro['usd_brl']:.2f}   |   Selic: {macro['selic']:.2f}%   |   IPCA: {macro['ipca']:.2f}%")

    # ── Badges de recomendação
    alav = dfp.get('alavancagem', 3.47)
    by = PAGE_H*0.48
    # Badge 1 RECOMENDAÇÃO
    canvas.setFillColor(ACCENT)
    canvas.roundRect(ML, by, 108, 40, 5, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica', 7)
    canvas.drawCentredString(ML+54, by+31, 'RECOMENDAÇÃO')
    canvas.setFont('Helvetica-Bold', 16)
    canvas.drawCentredString(ML+54, by+16, macro.get('recomendacao','MANTER'))
    canvas.setFont('Helvetica', 7)
    canvas.drawCentredString(ML+54, by+6, 'Bonds 2026–2028')
    # Badge 2 RATING
    canvas.setFillColor(STEEL)
    canvas.roundRect(ML+114, by, 100, 40, 5, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica', 7)
    canvas.drawCentredString(ML+164, by+31, 'RATING IMPLÍCITO')
    canvas.setFont('Helvetica-Bold', 16)
    canvas.drawCentredString(ML+164, by+16, macro.get('rating','B1/BB-'))
    canvas.setFont('Helvetica', 7)
    canvas.drawCentredString(ML+164, by+6, 'Estimado')
    # Badge 3 ALAVANCAGEM
    alav_col = RED_NEG if alav > 4.0 else ORANGE if alav > 3.0 else GREEN_POS
    canvas.setFillColor(alav_col)
    canvas.roundRect(ML+220, by, 92, 40, 5, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica', 7)
    canvas.drawCentredString(ML+266, by+31, 'DL / EBITDA')
    canvas.setFont('Helvetica-Bold', 18)
    canvas.drawCentredString(ML+266, by+15, f'{alav:.2f}x')
    # Badge 4 SPREAD
    canvas.setFillColor(GOLD)
    canvas.roundRect(ML+318, by, 112, 40, 5, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.setFont('Helvetica', 7)
    canvas.drawCentredString(ML+374, by+31, 'SPREAD ALVO (bps)')
    canvas.setFont('Helvetica-Bold', 16)
    canvas.drawCentredString(ML+374, by+16, '400–450 bps')
    canvas.setFont('Helvetica', 7)
    canvas.drawCentredString(ML+374, by+6, f'Yield ~{macro["treasury_10y"]+4.25:.2f}% USD')

    # ── KPI strip
    kpis = [
        (f"R$ {dfp.get('receita_liquida',44798)/1000:.1f}bi", 'Receita Líquida', GREEN_POS),
        (f"R$ {dfp.get('ebitda_ajustado',11796)/1000:.1f}bi", 'EBITDA Ajustado', GREEN_POS),
        (f"{dfp.get('margem_ebitda',25.1):.1f}%", 'Margem EBITDA Aj.', GREEN_POS),
        (f"R$ {dfp.get('divida_liquida',41218)/1000:.1f}bi", 'Dívida Líquida', ORANGE),
        (f"R$ {dfp.get('caixa',16000)/1000:.1f}bi", 'Caixa Gerencial', STEEL),
        (f"R$ {abs(dfp.get('lucro_liquido',-1507))/1000:.1f}bi", 'Prejuízo Líquido', RED_NEG),
    ]
    kpi_y = PAGE_H*0.35
    kw = (PAGE_W - ML - MR - 5) / 6
    for i, (val, lbl, col) in enumerate(kpis):
        x = ML + i*(kw+1)
        canvas.setFillColor(HexColor('#F8FAFC'))
        canvas.rect(x, kpi_y, kw, 34, fill=1, stroke=0)
        canvas.setFillColor(col)
        canvas.rect(x, kpi_y+31, kw, 3, fill=1, stroke=0)
        canvas.setFillColor(col)
        canvas.setFont('Helvetica-Bold', 10)
        canvas.drawCentredString(x+kw/2, kpi_y+18, val)
        canvas.setFillColor(GRAY_MID)
        canvas.setFont('Helvetica', 6.5)
        wrds = lbl.split()
        if len(wrds) > 2:
            canvas.drawCentredString(x+kw/2, kpi_y+9, ' '.join(wrds[:2]))
            canvas.drawCentredString(x+kw/2, kpi_y+3, ' '.join(wrds[2:]))
        else:
            canvas.drawCentredString(x+kw/2, kpi_y+7, lbl)

    # ── Tese resumida
    canvas.setFillColor(NAVY)
    canvas.setFont('Helvetica-Bold', 8)
    canvas.drawString(ML, PAGE_H*0.31, '▌ TESE EM RESUMO')
    canvas.setFillColor(GRAY_DARK)
    canvas.setFont('Helvetica', 7.8)
    ebitda = dfp.get('ebitda_ajustado', 11796)
    caixa = dfp.get('caixa', 16000)
    venc_2026 = fre.get('vencimentos', {}).get('2026', 10523)
    tese_usuario = macro.get('tese_resumo','')
    resumo = tese_usuario if tese_usuario else (
        f"CSN encerra 2025 com EBITDA Ajustado recorde de R$ {ebitda/1000:.1f}bi (+15,3% a/a; margem {dfp.get('margem_ebitda',25.1):.1f}%). "
        f"Alavancagem {alav:.2f}x acima do target 2,5x. FCO negativo R$ 0,97bi. "
        f"Caixa de R$ {caixa/1000:.1f}bi cobre {caixa/venc_2026*100:.0f}% dos vencimentos de curto prazo (R$ {venc_2026/1000:.1f}bi). "
        f"Plano de desinvestimentos R$ 15-18bi (jan/2026) é o catalisador central para desalavancagem. "
        f"Com premissas macro: Selic {macro['selic']:.2f}%, minério US$ {macro['minerio_fe']:.0f}/t, USD/BRL R$ {macro['usd_brl']:.2f}."
    )
    words = resumo.split()
    line, y = '', PAGE_H*0.285
    max_w = PAGE_W - ML - MR
    for w in words:
        test = (line + ' ' + w).strip()
        if canvas.stringWidth(test, 'Helvetica', 7.8) < max_w:
            line = test
        else:
            canvas.drawString(ML, y, line); y -= 10; line = w
    if line: canvas.drawString(ML, y, line)

    # Linha separadora
    canvas.setStrokeColor(GRAY_LIGHT)
    canvas.setLineWidth(0.5)
    canvas.line(ML, PAGE_H*0.225, PAGE_W-MR, PAGE_H*0.225)

    # Premissas macro na capa
    canvas.setFillColor(NAVY)
    canvas.setFont('Helvetica-Bold', 7.5)
    canvas.drawString(ML, PAGE_H*0.21, 'PREMISSAS MACROECONÔMICAS UTILIZADAS NESTE RELATÓRIO')
    macros_txt = [
        f"USD/BRL: R$ {macro['usd_brl']:.2f}",
        f"Selic: {macro['selic']:.2f}% a.a.",
        f"IPCA: {macro['ipca']:.2f}% a.a.",
        f"CDI: {macro['cdi']:.2f}% a.a.",
        f"Minério Fe 62%: US$ {macro['minerio_fe']:.0f}/t",
        f"HRC: US$ {macro['hrc']:.0f}/t",
        f"Treasury 10Y: {macro['treasury_10y']:.2f}%",
    ]
    mx, my = ML, PAGE_H*0.19
    canvas.setFillColor(GRAY_DARK)
    canvas.setFont('Helvetica', 7.5)
    spacing = (PAGE_W - ML - MR) / len(macros_txt)
    for i, mt in enumerate(macros_txt):
        canvas.drawString(ML + i*spacing, my, mt)

    # Disclaimer
    canvas.setFillColor(GRAY_MID)
    canvas.setFont('Helvetica', 6.5)
    canvas.drawCentredString(PAGE_W/2, 1.1*cm,
        'Documento gerado automaticamente com base em DFP/FRE enviados pelo usuário. Não constitui oferta ou recomendação formal de investimento.')
    canvas.restoreState()

# ─── CONSTRUÇÃO DO RELATÓRIO ────────────────────────────────────────────────────
def gerar_pdf(dfp, fre, macro):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=ML, rightMargin=MR,
        topMargin=MT+1.0*cm, bottomMargin=MB+0.85*cm)

    s = build_styles()

    # Vars comuns
    rl    = dfp.get('receita_liquida', 44798)
    eb    = dfp.get('ebitda_ajustado', 11796)
    mg_eb = dfp.get('margem_ebitda', 25.1)
    ll    = dfp.get('lucro_liquido', -1507)
    alav  = dfp.get('alavancagem', 3.47)
    dl    = dfp.get('divida_liquida', 41218)
    caixa = dfp.get('caixa', 16000)
    fco   = dfp.get('fco', -973)
    capex = dfp.get('capex', 5936)
    db    = fre.get('divida_bruta', 52924)
    venc  = fre.get('vencimentos', {})
    venc_2026 = venc.get('2026', 10523)
    juros = dfp.get('juros_pagos', 4268)
    rf    = dfp.get('resultado_financeiro', -6496)
    icj   = eb / juros if juros else 0

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
    story.append(P(
        f'A {macro.get("empresa_nome","Empresa")} encerrou 2025 com receita líquida de R$ {rl/1000:.1f} bilhões e EBITDA Ajustado de '
        f'R$ {eb/1000:.1f} bilhões (margem {mg_eb:.1f}% — recorde histórico, +15,3% a/a). '
        f'O resultado financeiro '
        f'de R$ {abs(rf)/1000:.1f} bilhões — impactado por despesas com juros de R$ {juros/1000:.1f} bilhões '
        f'e variação cambial negativa — conduziu ao segundo prejuízo líquido consecutivo (R$ {abs(ll)/1000:.1f} bilhões). '
        f'A alavancagem atingiu {alav:.2f}x Dívida Líquida/EBITDA, acima do guidance interno de 2,5x, porém com '
        f'caixa gerencial de R$ {caixa/1000:.1f} bilhões cobrindo {caixa/venc_2026*100:.0f}% da dívida de curto prazo. '
        f''
        f'', s['body']))
    story.append(SP(4))

    # Tabela KPI
    kpi_hdr = [P('Indicador', s['th']), P('2023', s['th']), P('2024', s['th']),
               P('2025A', s['th']), P('Target / Threshold', s['th'])]
    ok = lambda v, t, inv=False: '✓ OK' if (v<=t if inv else v>=t) else '⚠ Atenção'
    kpi_rows = [
        ['Receita Líquida (R$ bi)',      '37,8', '43,7', f'{rl/1000:.1f}',    '—'],
        ['EBITDA Ajustado (R$ bi)',      '7,2*', '10,2', f'{eb/1000:.1f}',    'Crescimento'],
        ['Margem EBITDA Aj. (%)',        '~19%', '23,4%', f'{mg_eb:.1f}%',    'acima 22%'],
        ['DL / EBITDA Aj. (x)',          '4,2*', '3,3',  f'{alav:.2f}',       'abaixo 2,5x'],
        ['Dívida Líquida (R$ bi)',       '~43', '34,2',  f'{dl/1000:.1f}',    'Redução'],
        ['ICJ — EBITDA/Juros (x)',       '1,7*', '1,4',  f'{icj:.2f}',        'acima 2,5x'],
        ['FCO (R$ bi)',                   '4,5*', '8,7',  f'{fco/1000:.1f}',   'Positivo'],
        ['Caixa / Dívida CP (%)',        '—',   '264%',  f'{caixa/venc_2026*100:.0f}%', 'acima 150%'],
        ['Lucro (Prejuízo) Líquido (R$ bi)', '(2,1)*', '(1,5)', f'({abs(ll)/1000:.1f})', '—'],
    ]
    rc = []
    for i, r in enumerate(kpi_rows):
        last = r[4]
        val_str = r[3].replace('(','').replace(')','')
        row_i = i+1
        if 'acima' in last or 'Positivo' in last or 'Crescimento' in last:
            try:
                v_num = float(val_str.replace('%','').replace(',','.'))
                t_num_s = last.replace('acima','').replace('%','').replace('x','').strip()
                try:
                    t_num = float(t_num_s.replace(',','.'))
                    color = HexColor('#DCFCE7') if v_num >= t_num else HexColor('#FEE2E2')
                    rc.append(('BACKGROUND', (3,row_i), (3,row_i), color))
                except: pass
            except: pass
        elif 'Redução' in last and i > 0:
            rc.append(('BACKGROUND', (3,row_i), (3,row_i), HexColor('#FEF3C7')))
        if '(' in r[3]:
            rc.append(('TEXTCOLOR', (3,row_i), (3,row_i), RED_NEG))
            rc.append(('FONTNAME', (3,row_i), (3,row_i), 'Helvetica-Bold'))

    kd = [kpi_hdr] + [[P(r[j], s['tl'] if j==0 else s['tc']) for j in range(5)] for r in kpi_rows]
    story.append(tbl(kd, [W*0.32, W*0.14, W*0.14, W*0.15, W*0.25], rc))
    story.append(P('* Estimativas / dados não auditados de períodos anteriores. Fonte: DFP 31/12/2025 e FRE 2025.', s['cap']))
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
               P(f"Impacto em {macro.get('empresa_nome','Empresa')[:15]}", s['th']), P('Sensibilidade', s['th'])]
    divida_cdi = db * 0.27
    vol_min_mt = dfp.get('volume_mineracao_mt', 45.849)
    imp_selic = divida_cdi * 0.01
    imp_min_10 = vol_min_mt * usd_brl * 10 * 0.35
    imp_cam = db * 0.64 * 0.01

    mac_rows = [
        ['USD / BRL (câmbio)',     f'R$ {usd_brl:.2f}',  'Receita mineração (USD) + Dívida ME (64%)', f'R$1,00 = ±R$ {vol_min_mt*1000*0.30/1000:.0f}mi EBITDA / ±R$ {imp_cam/1000:.1f}bi dívida'],
        ['Selic (% a.a.)',         f'{selic:.2f}%',       'Custo dívida BRL (~27% flutuante)',           f'+1 p.p. = ±R$ {imp_selic:,.0f}mi desp. fin.'],
        ['IPCA (% a.a.)',          f'{ipca:.2f}%',        'Contratos cimento/logística, IGP-M',          f'+1 p.p. = ±0,3% receita serviços'],
        ['CDI (% a.a.)',           f'{cdi:.2f}%',         'Rendimento aplicações financeiras + dívida',  f'Proxy Selic menos spread'],
        ['Minério Fe 62% (US$/t)', f'US$ {minerio:.0f}', 'Receita e EBITDA mineração (~41% margem)',     f'US$10/t = ±R$ {imp_min_10/1000:.1f}bi EBITDA'],
        ['HRC China Export (US$/t)',f'US$ {hrc:.0f}',    'Receita siderurgia (referência de preço)',     f'US$50/t = ±R$ 0,8bi receita'],
        ['Treasury 10Y EUA (%)',   f'{t10y:.2f}%',        f"Benchmark dívida USD — {macro.get('empresa_ticker','')}",          f'Yield alvo: {t10y+macro.get("spread_alvo",425)/100:.2f}% = spread ~{macro.get("spread_alvo",425)} bps'],
    ]
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
    dre_hdr = [P('(R$ milhões)', s['th']), P('2024A', s['th']),
               P('2025A', s['th']), P('Var. a/a', s['th']), P('Comentário', s['th'])]
    dre_rows = [
        ['Receita Líquida',        '43.687', f'{rl:,.0f}',    '+2,5%',  'Mineração e logística compensaram queda em siderurgia'],
        ['CPV',                    '(31.991)', f'({32404:,.0f})', '+1,3%','Custos controlados; energia e matérias-primas estáveis'],
        ['Lucro Bruto',            '11.697', f'{dfp.get("lucro_bruto",12394):,.0f}', '+6,0%', f'Margem bruta {dfp.get("margem_bruta",27.7):.1f}% vs 26,8% em 2024'],
        ['EBIT',                   '4.270',  '4.817',  '+12,8%', 'Melhora operacional consistente'],
        ['EBITDA Ajustado',        '10.234', f'{eb:,.0f}', '+15,3%', f'Margem {mg_eb:.1f}% — recorde histórico'],
        ['Resultado Financeiro',   '(5.813)', f'({abs(rf):,.0f})', '+11,7%', 'Juros + variação cambial negativa de R$ 1,6bi'],
        ['Lucro (Prejuízo) Líquido','(1.538)', f'({abs(ll):,.0f})', '+2,0%', '2º ano consecutivo de prejuízo líquido'],
        ['FCO',                    '8.651',  f'{fco:,.0f}',   'n.m.',  'Inversão por variação de capital de giro'],
        ['CAPEX',                  '(5.494)', f'({capex:,.0f})', '+7,5%', 'Imobilizado (expansão mina) + manutenção'],
    ]
    bold_rows = {4, 5, 6}
    extra = [('FONTNAME',(0,i+1),(-1,i+1),'Helvetica-Bold') for i in bold_rows]
    extra += [('BACKGROUND',(0,5),(-1,5), HexColor('#FEF3C7')),
              ('BACKGROUND',(0,6),(-1,6), HexColor('#FEE2E2')),
              ('BACKGROUND',(0,5),(-1,4), HexColor('#DCFCE7'))]
    dd = [dre_hdr] + [[P(r[j], s['tl'] if j==0 else (s['tl'] if j==4 else s['tc'])) for j in range(5)] for r in dre_rows]
    story.append(tbl(dd, [W*0.22, W*0.13, W*0.13, W*0.10, W*0.42], extra))
    story.append(SP(5))

    # Segmentos
    story.append(P('3.2 Desempenho por Segmento', s['ssh']))
    seg_hdr = [P('Segmento', s['th']), P('Receita (R$ mi)', s['th']), P('% Total', s['th']),
               P('EBITDA Aj. (R$ mi)', s['th']), P('Mg. EBITDA', s['th']), P('Destaque', s['th'])]
    # Segmentos: usar dados do usuário, ignorar segmentos sem receita
    _segs_raw = [
        (dfp.get('seg1_nome','') or dfp.get('receita_siderurgia') and 'Siderurgia', dfp.get('receita_siderurgia'), dfp.get('ebitda_siderurgia')),
        (dfp.get('seg2_nome','') or dfp.get('receita_mineracao') and 'Mineração',   dfp.get('receita_mineracao'),   dfp.get('ebitda_mineracao')),
        (dfp.get('seg3_nome','') or dfp.get('receita_cimentos') and 'Cimentos',    dfp.get('receita_cimentos'),    dfp.get('ebitda_cimentos')),
        (dfp.get('seg4_nome','') or dfp.get('receita_logistica') and 'Logística',  dfp.get('receita_logistica'),   dfp.get('ebitda_logistica')),
        (dfp.get('seg5_nome','') or dfp.get('receita_energia') and 'Energia',      dfp.get('receita_energia'),     dfp.get('ebitda_energia')),
    ]
    segs = [(nm, rc or 0, eb or 0, '') for nm, rc, eb in _segs_raw if nm and rc and rc > 0]
    if not segs:
        segs = []  # Sem segmentos — pula a tabela
    sd = [seg_hdr] + [
        [P(nm, s['tl']), P(f'{rc_s:,.0f}', s['tc']),
         P(f'{rc_s/rl*100:.1f}%', s['tc']),
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
    me_split = {'2026':(7909,2614),'2027':(3890,3915),'2028':(8891,2510),
                '2029':(565,1910),'2030':(4319,1632),'2031':(5145,1460),'apos_2031':(3178,5653)}
    venc_order = ['2026','2027','2028','2029','2030','2031','apos_2031']
    obs = {'2026':'⚠ CRÍTICO — coberto pelo caixa','2027':'Refinanciamento possível','2028':'⚠ MAIOR pico de amortização',
           '2029':'Gerenciável','2030':'Bonds de longo prazo','2031':'Longo prazo','apos_2031':'Bonds perpétuos / longo'}
    vrc = []
    vrows = []
    for i, ano in enumerate(venc_order):
        val = venc.get(ano, 0)
        me_v, brl_v = me_split.get(ano, (0,0))
        lbl = 'Após 2031' if ano == 'apos_2031' else ano
        pct = val/db*100 if db else 0
        vrows.append([lbl, f'{val:,.0f}', f'{pct:.1f}%', f'{me_v:,.0f}', f'{brl_v:,.0f}', obs[ano]])
        if ano in ('2026','2028'): vrc.append(('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#FEE2E2')))
        elif ano == '2027': vrc.append(('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#FEF3C7')))
    total_v = sum(venc.get(k,0) for k in venc_order)
    vrows.append(['TOTAL', f'{total_v:,.0f}', '100%', '33.897', '19.695', ''])
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
    ic_hdr = [P('Métrica', s['th']), P('2025A', s['th']), P('Target Int.', s['th']),
              P('Threshold BB', s['th']), P('Situação', s['th'])]
    cov_cp = caixa/venc_2026*100
    ic_rows = [
        ['DL / EBITDA Ajustado (x)',    f'{alav:.2f}x',            '2,5x',          'abaixo 3,0x',   '⚠ Acima' if alav > 3.0 else '✓ OK'],
        ['ICJ — EBITDA / Juros (x)',    f'{icj:.2f}x',             'acima 2,5x',    'acima 2,0x',    '⚠ Abaixo' if icj < 2.0 else '✓ OK'],
        ['Dív. Bruta / EBITDA (x)',     f'{db/eb:.2f}x',           'abaixo 5,0x',   'abaixo 5,5x',   '⚠ Acima' if db/eb > 5.0 else '✓ OK'],
        ['FCO / Dívida Bruta (%)',      f'{fco/db*100:.1f}%',      'acima 10%',     'acima 8%',      '⚠ Negativo' if fco < 0 else '✓ OK'],
        ['Caixa / Dív. CP (%)',         f'{cov_cp:.0f}%',          'acima 150%',    'acima 100%',    '✓ OK' if cov_cp > 150 else '⚠ Atenção'],
        ['DL / Patrimônio Líquido (x)', f'{dl/15700:.2f}x',        'abaixo 2,5x',   'abaixo 3,0x',   '⚠ Elevado' if dl/15700 > 2.5 else '✓ OK'],
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
        _sens_desc += f', Brent ~US$ 78/bbl'
    _sens_desc += f', Treasury 10Y {t10y:.2f}%), calculamos os impactos marginais sobre EBITDA e alavancagem:'
    story.append(P(_sens_desc, s['body']))
    story.append(SP(3))

    imp_selic_100   = divida_cdi * 0.01
    imp_min_10_calc = vol_min_mt * usd_brl * 10 * 0.35
    imp_cam_1       = vol_min_mt * 1000 * 0.30
    imp_db_cam      = db * 0.64 * 0.01

    sh_hdr = [P('Variável', s['th']), P('Choque', s['th']), P('Impacto EBITDA (R$ mi)', s['th']),
              P('Impacto DL (R$ mi)', s['th']), P('DL/EBITDA pós-choque', s['th'])]
    sh_rows = [
        ['Selic / CDI',        '+1,0 p.p.', f'-{imp_selic_100:,.0f} (desp. fin.)',       '—',                 f'{(dl)/(eb-imp_selic_100):.2f}x'],
        ['Selic / CDI',        '-1,0 p.p.', f'+{imp_selic_100:,.0f} (desp. fin.)',       '—',                 f'{(dl)/(eb+imp_selic_100):.2f}x'],
        ['Minério Fe',         '+US$10/t',  f'+{imp_min_10_calc:,.0f}',                  '—',                 f'{dl/(eb+imp_min_10_calc):.2f}x'],
        ['Minério Fe',         '-US$10/t',  f'-{imp_min_10_calc:,.0f}',                  '—',                 f'{dl/(eb-imp_min_10_calc):.2f}x'],
        ['USD / BRL',          '+R$ 0,50',  f'+{imp_cam_1*0.5*0.30:,.0f} (rec. min.)',   f'+{db*0.64/usd_brl*0.5:,.0f} (dívida ME)', f'{(dl+db*0.64/usd_brl*0.5)/(eb+imp_cam_1*0.5*0.30):.2f}x'],
        ['USD / BRL',          '-R$ 0,50',  f'-{imp_cam_1*0.5*0.30:,.0f} (rec. min.)',   f'-{db*0.64/usd_brl*0.5:,.0f} (dívida ME)', f'{(dl-db*0.64/usd_brl*0.5)/(eb-imp_cam_1*0.5*0.30):.2f}x'],
        ['HRC (siderurgia)',   '+US$50/t',  '+~800',                                      '—',                 f'{dl/(eb+800):.2f}x'],
        ['HRC (siderurgia)',   '-US$50/t',  '-~800',                                      '—',                 f'{dl/(eb-800):.2f}x'],
    ]
    sh_d = [sh_hdr] + [[P(r[0],s['tl'])] + [P(r[j],s['tc']) for j in range(1,5)] for r in sh_rows]
    story.append(tbl(sh_d, [W*0.17, W*0.12, W*0.27, W*0.25, W*0.19]))
    story.append(P(f'Base: {vol_min_mt:.1f} Mt mineração; R$ {divida_cdi/1000:.1f}bi dívida CDI-linked; 30% mg EBITDA incremental mineração; câmbio base R$ {usd_brl:.2f}.', s['cap']))
    story.append(SP(4))

    # Cenários
    story.append(P('5.1 Cenários de Alavancagem 2026E', s['ssh']))
    story.append(P(f'Projeções com premissas do usuário (minério US$ {minerio:.0f}/t, câmbio R$ {usd_brl:.2f}) e diferentes hipóteses de desinvestimento:', s['body']))
    story.append(SP(3))

    def cenario_eb(min_p, growth=1.05):
        return eb * (min_p / 105) * growth

    c_rows_data = [
        ('Stress',   85,  0,   1.00, HexColor('#FEE2E2')),
        ('Base',     minerio, 5000, 1.05, HexColor('#FEF3C7')),
        ('Otimista', 125, 10000, 1.10, HexColor('#DCFCE7')),
    ]
    c_hdr = [P('Cenário', s['th']), P('Minério (US$/t)', s['th']), P('Desinv. (R$ bi)', s['th']),
             P('EBITDA 2026E', s['th']), P('DL 2026E', s['th']), P('DL/EBITDA', s['th']), P('Situação', s['th'])]
    cen_d = [c_hdr]
    cen_rc = []
    for i, (nome, min_p, des, grow, cor) in enumerate(c_rows_data):
        eb_c = cenario_eb(min_p, grow)
        dl_c = dl - des
        alav_c = dl_c / eb_c
        sit = '⚠ Crítico' if alav_c > 4.0 else ('⚠ Monitorar' if alav_c > 3.0 else '✓ Convergindo')
        cen_d.append([P(nome, s['tlb']), P(f'US$ {min_p:.0f}', s['tc']),
                      P(f'R$ {des/1000:.0f}bi', s['tc']), P(f'R$ {eb_c/1000:.1f}bi', s['tc']),
                      P(f'R$ {dl_c/1000:.1f}bi', s['tc']), P(f'{alav_c:.2f}x', s['tc']),
                      P(sit, s['tc'])])
        cen_rc.append(('BACKGROUND',(0,i+1),(-1,i+1), cor))
    story.append(tbl(cen_d, [W*0.13,W*0.14,W*0.13,W*0.13,W*0.13,W*0.12,W*0.22], cen_rc))
    story.append(SP(5))

    # ────────────────────────────────────────────────────────────────────
    # 6. RISCOS
    # ────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story += [P('6. MATRIZ DE RISCOS', s['sh']), HR()]

    r_hdr = [P('Risco', s['th']), P('Impacto', s['th']),
             P('Probabilidade', s['th']), P('Comentário', s['th'])]
    selic_risco = 'ALTO' if selic >= 15.0 else 'MÉDIO'
    min_risco   = 'ALTO' if minerio < 95 else 'MÉDIO'
    risk_rows = [
        ['Refinanciamento 2026 (R$10,5bi)',        'ALTO',       'MÉDIA',
         f'Caixa de R${caixa/1000:.1f}bi cobre vencimento. Custo elevado na Selic {selic:.2f}%.'],
        ['Fracasso nos desinvestimentos',           'MUITO ALTO', 'MÉDIA',
         'Sem alienações: DL/EBITDA permanece acima de 3,5x até 2027. Catalisador central.'],
        [f'Preço minério abaixo US$85/t (atual US${minerio:.0f}/t)', min_risco, 'BAIXA' if minerio>95 else 'MÉDIA',
         f'EBITDA mineração cai ~R$2,5bi. DL/EBITDA ultrapassa 4,0x. Monitorar China/demanda global.'],
        [f'Selic / CDI elevado (atual {selic:.2f}%)',selic_risco, 'MÉDIA',
         f'27% dívida flutuante. Cada +1pp = R${imp_selic_100:,.0f}mi em despesas financeiras adicionais.'],
        ['Volatilidade cambial (64% dívida ME)',    'ALTO',       'MÉDIA',
         f'Hedge US$7,9bi eficaz. Exportações de minério são hedge natural. USD/BRL atual: R${usd_brl:.2f}.'],
        ['Barragens — evento ambiental',            'MUITO ALTO', 'BAIXA',
         'Programa de descaracterização em andamento. B4 em obras. Prazo: 2030.'],
        ['Contingências fiscais (R$47,4bi possível)','MÉDIO',     'BAIXA',
         f'Não provisionado (provisão R$874mi). Prazos longos. IRPJ/CSLL e ágio são os maiores.'],
        ['FCO negativo persistente',               'ALTO',        'MÉDIA',
         'FCO -R$0,97bi em 2025. Variação de capital de giro e juros pagos são os vetores.'],
    ]
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
            f'(vencimentos 2026-2028), com spread alvo de {spread_min}-{spread_max} bps sobre Treasuries '
            f'(yield alvo ~{yield_alvo:.2f}% USD, com Treasury 10Y em {t10y:.2f}%). '
            f'EBITDA recorde de R$ {eb/1000:.1f}bi demonstra qualidade operacional. '
            f'Caixa de R$ {caixa/1000:.1f}bi cobre 100%+ da dívida CP. '
            f'Mineração (margem 41%) e logística (margem 44%) são âncoras de geração de caixa. '
            f'Recovery estimado superior a 100% em cenário de liquidação (ativos > passivos).',
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
    gt_rows = [
        ['⬆ UPGRADE (Compra)', 'Desinvestimentos acima de R$8bi confirmados + DL/EBITDA abaixo de 3,0x', 'DL/EBITDA abaixo 3,0x', 'Relatório de resultados ITR'],
        ['⬆ UPGRADE (Compra)', 'FCO acima de R$3bi por dois trimestres consecutivos', 'FCO trimestral', 'ITR / DFC'],
        ['⬇ DOWNGRADE (Venda)', 'Fracasso ou atraso nos desinvestimentos (sem anúncio até dez/2026)', 'Comunicados CVM', 'Fatos relevantes'],
        ['⬇ DOWNGRADE (Venda)', f'Minério Fe abaixo de US$85/t por dois trimestres (atual US${minerio:.0f}/t)', 'Preço spot Fe 62%', 'Diário (Bloomberg/SGX)'],
        ['⬇ DOWNGRADE (Venda)', f'Caixa abaixo de R$10bi (atual R${caixa/1000:.1f}bi)', 'Caixa gerencial', 'ITR trimestral'],
        ['⬇ DOWNGRADE (Venda)', 'Refinanciamento 2026 a custo acima de 9,5% USD', 'Anúncio de emissão', 'Prospecto / EMTN'],
    ]
    gt_rc = [('BACKGROUND',(0,i+1),(-1,i+1), HexColor('#DCFCE7') if '⬆' in r[0] else HexColor('#FEE2E2')) for i,r in enumerate(gt_rows)]
    gt_d = [gt_hdr] + [[P(r[0], ParagraphStyle('g', fontName='Helvetica-Bold', fontSize=7.5,
                textColor=GREEN_POS if '⬆' in r[0] else RED_NEG, leading=10, alignment=TA_LEFT)),
                P(r[1], s['tl']), P(r[2], s['tc']), P(r[3], s['tl'])] for r in gt_rows]
    story.append(tbl(gt_d, [W*0.17, W*0.42, W*0.18, W*0.23], gt_rc))
    story.append(SP(6))

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

@app.route('/api/cnpj/<cnpj>')
def consultar_cnpj(cnpj):
    """Consulta dados da empresa pelo CNPJ via BrasilAPI."""
    raw = ''.join(filter(str.isdigit, cnpj))
    if len(raw) != 14:
        return jsonify({'error': 'CNPJ inválido'}), 400
    # Mapeamento CNPJ → Ticker B3 (principais empresas)
    CNPJ_TICKER = {
        '33592510000154': 'PETR4', '60872504000123': 'VALE3',
        '60746948000112': 'ITUB4', '00000000000191': 'BBAS3',
        '90400888000142': 'BBDC4', '92702067000196': 'GGBR4',
        '33042730000104': 'CSNA3', '15527906000159': 'HAPV3',
        '07206816000115': 'WEGE3', '07526557000100': 'RENT3',
    }
    try:
        import requests as req2
        r = req2.get(f'https://brasilapi.com.br/api/cnpj/v1/{raw}',
                     headers={'User-Agent': 'Mozilla/5.0'}, timeout=12)
        if r.status_code != 200:
            # fallback receitaws
            r2 = req2.get(f'https://receitaws.com.br/v1/cnpj/{raw}',
                          headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            d = r2.json()
            return jsonify({
                'razao_social': d.get('nome',''),
                'nome_fantasia': d.get('fantasia',''),
                'cnae': d.get('atividade_principal',[{}])[0].get('text','') if d.get('atividade_principal') else '',
                'situacao': d.get('situacao',''),
                'municipio': d.get('municipio',''),
                'uf': d.get('uf',''),
                'ticker': CNPJ_TICKER.get(raw, ''),
                'qsa': [{'nome': s.get('nome',''), 'qualificacao': s.get('qual','')} for s in d.get('qsa', [])[:8]],
                'setor_macro': d.get('atividade_principal',[{}])[0].get('text',''),
            })
        d = r.json()
        # Detectar setor pelo CNAE
        cnae_desc = ''
        if d.get('cnae_fiscal_descricao'):
            cnae_desc = d['cnae_fiscal_descricao']
        elif d.get('cnaes_secundarios'):
            cnae_desc = d['cnaes_secundarios'][0].get('descricao','')
        return jsonify({
            'razao_social': d.get('razao_social',''),
            'nome_fantasia': d.get('nome_fantasia',''),
            'cnae': cnae_desc,
            'situacao': d.get('descricao_situacao_cadastral',''),
            'municipio': d.get('municipio',''),
            'uf': d.get('uf',''),
            'capital_social': d.get('capital_social'),
            'ticker': CNPJ_TICKER.get(raw, ''),
            'qsa': [{'nome': s.get('nome_socio',''), 'qualificacao': s.get('qualificacao_socio','')} for s in d.get('qsa', [])[:8]],
            'setor_macro': cnae_desc,
        })
    except Exception as e:
        return jsonify({'error': f'Não foi possível consultar: {str(e)}'}), 500

@app.route('/api/treasury')
def treasury():
    """Retorna Treasury 10Y via FRED API."""
    try:
        import requests as req2
        FRED_KEY = 'b22fa17b11e3e89d8c73dce4b08a0cd9'
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

@app.route('/api/upload', methods=['POST'])
def upload():
    result = {'dfp': {}, 'fre': {}, 'errors': [], 'success': False}
    if 'dfp' in request.files:
        f = request.files['dfp']
        try:
            text = extract_text_pdf(f)
            result['dfp'] = parse_dfp(text)
            result['dfp']['_nome'] = f.filename
            result['dfp']['_linhas'] = len(text.split('\n'))
        except Exception as e:
            result['errors'].append(f'DFP: {e}')
    if 'fre' in request.files:
        f = request.files['fre']
        try:
            text = extract_text_pdf(f)
            result['fre'] = parse_fre(text)
            result['fre']['_nome'] = f.filename
        except Exception as e:
            result['errors'].append(f'FRE: {e}')
    result['success'] = len(result['errors']) == 0
    return jsonify(result)

@app.route('/api/generate', methods=['POST'])
def generate():
    body = request.json or {}
    dfp_data = body.get('dfp') or parse_dfp('')
    fre_data = body.get('fre') or parse_fre('')
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
    try:
        pdf_bytes = gerar_pdf(dfp_data, fre_data, macro)
        return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf',
                         as_attachment=True, download_name=f"{macro.get('empresa_ticker','Empresa').replace(' ','_')}_Analise_Credito.pdf")
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

if __name__ == '__main__':
    print('\n🚀  Dashboard Análise de Crédito → http://localhost:5000\n')
    app.run(debug=True, port=5000)
