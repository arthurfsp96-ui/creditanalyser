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

    d['volume_mineracao_mt'] = 0
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
    canvas.setFont('Helvetica-Bold', 22)
    canvas.drawString(ML, PAGE_H*0.80, macro.get('empresa_nome','Empresa')[:40])
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
    canvas.drawCentredString(ML+374, by+16, f"{macro.get('spread_alvo',425)} bps")
    canvas.setFont('Helvetica', 7)
    _spd = macro.get('spread_alvo',425)
    canvas.drawCentredString(ML+374, by+6, f'Yield ~{macro["treasury_10y"]+_spd/100:.2f}% USD')

    # ── KPI strip
    kpis = [
        (f"R$ {dfp.get('receita_liquida',0)/1000:.1f}bi" if dfp.get('receita_liquida') else '—', 'Receita Líquida', GREEN_POS),
        (f"R$ {dfp.get('ebitda_ajustado',0)/1000:.1f}bi" if dfp.get('ebitda_ajustado') else '—', 'EBITDA Ajustado', GREEN_POS),
        (f"{dfp.get('margem_ebitda',0):.1f}%" if dfp.get('margem_ebitda') else '—', 'Margem EBITDA Aj.', GREEN_POS),
        (f"R$ {dfp.get('divida_liquida',0)/1000:.1f}bi" if dfp.get('divida_liquida') else '—', 'Dívida Líquida', ORANGE),
        (f"R$ {dfp.get('caixa',0)/1000:.1f}bi" if dfp.get('caixa') else '—', 'Caixa Gerencial', STEEL),
        (f"R$ {abs(dfp.get('lucro_liquido',0))/1000:.1f}bi" if dfp.get('lucro_liquido') else '—', 'Resultado Líquido', RED_NEG),
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
    ebitda = dfp.get('ebitda_ajustado') or 0
    caixa = dfp.get('caixa', 16000)
    venc_2026 = fre.get('vencimentos', {}).get('2026') or 0
    tese_usuario = macro.get('tese_resumo','')
    _nm = macro.get("empresa_nome","A empresa")
    resumo = tese_usuario if tese_usuario else (
        f"{_nm} — análise de crédito baseada nos documentos enviados."
        + (f" EBITDA Ajustado: R$ {ebitda/1000:.1f}bi." if ebitda else "")
        + (f" Alavancagem: {alav:.2f}x DL/EBITDA." if alav else "")
        + (f" Caixa: R$ {caixa/1000:.1f}bi." if caixa else "")
        + f" Selic {macro['selic']:.2f}%, USD/BRL R$ {macro['usd_brl']:.2f}."
    )
    words = resumo.split()
    line, y = '', PAGE_H*0.285
    max_w = PAGE_W - ML - MR
    min_y = PAGE_H*0.235  # não ultrapassar área de premissas
    for w in words:
        test = (line + ' ' + w).strip()
        if canvas.stringWidth(test, 'Helvetica', 7.8) < max_w:
            line = test
        else:
            if y > min_y:
                canvas.drawString(ML, y, line)
            y -= 10; line = w
    if line and y > min_y:
        canvas.drawString(ML, y, line)

    # Linha separadora
    canvas.setStrokeColor(GRAY_LIGHT)
    canvas.setLineWidth(0.5)
    canvas.line(ML, PAGE_H*0.225, PAGE_W-MR, PAGE_H*0.225)

    # Premissas macro na capa
    canvas.setFillColor(NAVY)
    canvas.setFont('Helvetica-Bold', 7.5)
    canvas.drawString(ML, PAGE_H*0.21, 'PREMISSAS MACROECONÔMICAS UTILIZADAS NESTE RELATÓRIO')
    _setor_m = macro.get('setor','')
    macros_txt = [
        f"USD/BRL: R$ {macro['usd_brl']:.2f}",
        f"Selic: {macro['selic']:.2f}% a.a.",
        f"IPCA: {macro['ipca']:.2f}% a.a.",
        f"CDI: {macro['cdi']:.2f}% a.a.",
        f"Treasury 10Y: {macro['treasury_10y']:.2f}%",
        f"Spread Alvo: {macro.get('spread_alvo',425)} bps",
    ]
    if _setor_m in ('mineracao','siderurgia'):
        macros_txt.insert(4, f"Minério Fe: US$ {macro.get('minerio_fe',102):.0f}/t")
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
        f'e variação cambial negativa — conduziu ao resultado líquido de R$ {ll/1000:.1f} bilhões. '
        f'A alavancagem atingiu {alav:.2f}x Dívida Líquida/EBITDA, porém com '
        f'caixa gerencial de R$ {caixa/1000:.1f} bilhões' + (f' cobrindo {caixa/venc_2026*100:.0f}% da dívida de curto prazo.' if venc_2026 > 0 else '.') +
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
        ['Caixa / Dívida CP (%)',        '—',   '264%',  f'{caixa/venc_2026*100:.0f}%' if venc_2026 > 0 else '—', 'acima 150%'],
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
        mac_rows.insert(4, ['Petróleo Brent (US$/bbl)', 'US$ 78',
            'Principal driver de receita E&P', 'US$10/bbl = ±impacto direto na receita'])
    elif _setor_mac == 'agro':
        mac_rows.insert(4, ['Commodities Agrícolas', 'Var.',
            'Preços de soja/milho impactam receita', 'Correlação com câmbio e demanda China'])
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
    _ano_ref = macro.get('empresa_database','2025').split('/')[-1] if '/' in macro.get('empresa_database','2025') else macro.get('empresa_database','2025')
    _ano_ant = str(int(_ano_ref) - 1) if _ano_ref.isdigit() else 'Anterior'
    lb = dfp.get('lucro_bruto') or 0
    mg_b = dfp.get('margem_bruta') or (round(lb/rl*100,1) if rl > 0 else 0)
    # Variação YoY — só mostra se tiver dado do ano anterior (campo prev_*)
    def var_yoy(atual, ant_key):
        ant = dfp.get(ant_key)
        if ant and ant != 0 and atual:
            pct = (atual - ant) / abs(ant) * 100
            return f'{pct:+.1f}%'
        return '—'
    dre_hdr = [P('(R$ milhões)', s['th']), P(f'{_ano_ant}A', s['th']),
               P(f'{_ano_ref}A', s['th']), P('Var. a/a', s['th']), P('Comentário', s['th'])]
    def fmt_val(v, neg=False):
        if not v: return '—'
        return f'({abs(v):,.0f})' if neg and v < 0 else f'{v:,.0f}'
    dre_rows = [
        ['Receita Líquida',         fmt_val(dfp.get('prev_receita')),     fmt_val(rl),          var_yoy(rl,'prev_receita'),    f'Receita consolidada — margem bruta {mg_b:.1f}%'],
        ['Lucro Bruto',             fmt_val(dfp.get('prev_lucro_bruto')), fmt_val(lb),          var_yoy(lb,'prev_lucro_bruto'),f'Margem bruta: {mg_b:.1f}%'],
        ['EBITDA Ajustado',         fmt_val(dfp.get('prev_ebitda')),      fmt_val(eb),          var_yoy(eb,'prev_ebitda'),     f'Margem EBITDA: {mg_eb:.1f}%'],
        ['Resultado Financeiro',    fmt_val(dfp.get('prev_rf'),True),     fmt_val(rf,True),     '—',                           'Inclui juros e variação cambial'],
        ['Lucro (Prejuízo) Líquido',fmt_val(dfp.get('prev_ll'),True),    fmt_val(ll,True),     var_yoy(ll,'prev_ll'),         'Resultado líquido do período'],
        ['FCO',                     fmt_val(dfp.get('prev_fco')),         fmt_val(fco),         var_yoy(fco,'prev_fco'),       'Fluxo de caixa operacional'],
        ['CAPEX',                   fmt_val(dfp.get('prev_capex'),True),  fmt_val(capex,True),  '—',                           'Investimentos em imobilizado'],
    ]
    bold_rows = {2, 3, 4}
    extra_dre = [('FONTNAME',(0,i+1),(-1,i+1),'Helvetica-Bold') for i in bold_rows]
    extra_dre += [('BACKGROUND',(0,3),(-1,3), HexColor('#FEF3C7')),
                  ('BACKGROUND',(0,4),(-1,4), HexColor('#FEE2E2')),
                  ('BACKGROUND',(0,2),(-1,2), HexColor('#DCFCE7'))]
    dd = [dre_hdr] + [[P(r[j], s['tl'] if j==0 else (s['tl'] if j==4 else s['tc'])) for j in range(5)] for r in dre_rows]
    story.append(tbl(dd, [W*0.22, W*0.13, W*0.13, W*0.10, W*0.42], extra_dre))
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
    cov_cp = caixa/venc_2026*100 if venc_2026 > 0 else 0
    ic_rows = [
        ['DL / EBITDA Ajustado (x)',    f'{alav:.2f}x',            '2,5x',          'abaixo 3,0x',   '⚠ Acima' if alav > 3.0 else '✓ OK'],
        ['ICJ — EBITDA / Juros (x)',    f'{icj:.2f}x',             'acima 2,5x',    'acima 2,0x',    '⚠ Abaixo' if icj < 2.0 else '✓ OK'],
        ['Dív. Bruta / EBITDA (x)',     f'{db/eb:.2f}x' if eb > 0 else '—', 'abaixo 5,0x', 'abaixo 5,5x', '⚠ Acima' if (eb > 0 and db/eb > 5.0) else '✓ OK'],
        ['FCO / Dívida Bruta (%)',      f'{fco/db*100:.1f}%' if db > 0 else '—', 'acima 10%', 'acima 8%', '⚠ Negativo' if fco < 0 else '✓ OK'],
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
        sh_rows.insert(4, ['Commodity principal', '+10%', f'+{imp_com:,.0f}', '—', alav_safe(dl, eb+imp_com)])
        sh_rows.insert(5, ['Commodity principal', '-10%', f'-{imp_com:,.0f}', '—', alav_safe(dl, eb-imp_com)])
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
        risk_rows.append(['Volatilidade do Brent', 'ALTO', 'MÉDIA',
            'Preço do petróleo impacta diretamente receita e EBITDA E&P.'])
    elif _setor_r in ('mineracao','siderurgia'):
        risk_rows.append(['Volatilidade de commodities', 'ALTO', 'MÉDIA',
            'Preços de minério/aço correlacionados à demanda China e ciclo global.'])
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

def _buscar_ticker_brapi(nome_pregao, nome_social, cod_cvm):
    """Tenta encontrar ticker via Brapi pelo nome de pregão ou CVM."""
    BRAPI_TOKEN = 'ucaHWHuWF7tLMv47tpzQB8'
    try:
        import requests as rq
        # Busca por nome no Brapi
        termos = [t for t in [nome_pregao, nome_social[:15] if nome_social else ''] if t]
        for termo in termos:
            r = rq.get(f'https://brapi.dev/api/quote/list',
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
    except:
        pass
    return ''

def _buscar_dados_brapi(ticker):
    """Busca cotação e múltiplos via Brapi para um ticker."""
    if not ticker:
        return {}
    BRAPI_TOKEN = 'ucaHWHuWF7tLMv47tpzQB8'
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
        cvm = _load_cvm_cache()
        if raw in cvm:
            info = cvm[raw]
            resultado['cod_cvm'] = info.get('cod_cvm','')
            resultado['segmento_b3'] = info.get('segmento','')
            resultado['categoria_cvm'] = info.get('categoria','')
            resultado['situacao_cvm'] = info.get('situacao_cvm','')
            nome_pregao = info.get('nome_pregao','') or resultado['nome_fantasia']
            # Tentar achar ticker via Brapi
            tk = _buscar_ticker_brapi(nome_pregao, resultado['razao_social'], info.get('cod_cvm',''))
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

    return jsonify(resultado)

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

# Cache em memória para textos extraídos dos PDFs (por sessão simples)
_pdf_cache = {'dfp_text': '', 'fre_text': ''}

@app.route('/api/upload', methods=['POST'])
def upload():
    result = {'dfp': {}, 'fre': {}, 'errors': [], 'success': False}
    if 'dfp' in request.files:
        f = request.files['dfp']
        try:
            text = extract_text_pdf(f)
            _pdf_cache['dfp_text'] = text
            result['dfp'] = parse_dfp(text)
            result['dfp']['_nome'] = f.filename
            result['dfp']['_chars'] = len(text)
            result['dfp']['_extraiu'] = sum(1 for v in result['dfp'].values() if v and str(v) not in ('', 'None', '0'))
        except Exception as e:
            result['errors'].append(f'DFP: {str(e)}')
    if 'fre' in request.files:
        f = request.files['fre']
        try:
            text = extract_text_pdf(f)
            _pdf_cache['fre_text'] = text
            result['fre'] = parse_fre(text)
            result['fre']['_nome'] = f.filename
        except Exception as e:
            result['errors'].append(f'FRE: {str(e)}')
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
# ROTA: /api/peers/<ticker>
# ══════════════════════════════════════════════════════════════════
@app.route('/api/peers/<ticker>')
def get_peers(ticker):
    """Busca peers do mesmo setor e retorna dados comparativos via Brapi."""
    import requests as rq
    BRAPI_TOKEN = 'ucaHWHuWF7tLMv47tpzQB8'
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

    sc = calcular_scorecard(dfp_data, setor)
    zs = calcular_zscore(dfp_data, market_cap_bi)

    return jsonify({'scorecard': sc, 'zscore': zs})

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
            BRAPI_TOKEN = 'ucaHWHuWF7tLMv47tpzQB8'
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
    """Busca notícias recentes via Google News RSS."""
    import urllib.request, urllib.parse
    from xml.etree import ElementTree as ET

    tk = ticker.upper()
    resultado = {'ticker': tk, 'noticias': []}
    queries = [tk, f'{tk} resultados', f'{tk} crédito']

    try:
        q = urllib.parse.quote(f'{tk} ações bolsa')
        url = f'https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419'
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            xml = r.read()
        root = ET.fromstring(xml)
        items = root.findall('.//item')[:10]
        for item in items:
            titulo = item.findtext('title','')
            link = item.findtext('link','')
            pub = item.findtext('pubDate','')
            fonte = item.findtext('source','')
            resultado['noticias'].append({
                'titulo': titulo,
                'link': link,
                'publicado': pub,
                'fonte': fonte,
            })
    except Exception as e:
        resultado['erro'] = str(e)

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
    except:
        pass

if __name__ == '__main__':
    print('\n🚀  Dashboard Análise de Crédito → http://localhost:5000\n')
    app.run(debug=True, port=5000)
