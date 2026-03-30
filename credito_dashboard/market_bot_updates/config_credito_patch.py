#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config_credito_patch.py
────────────────────────
Adicione estas configurações ao seu config.py existente.
"""

# ══════════════════════════════════════════════════════════════
# Adicione ao config.py existente:
# ══════════════════════════════════════════════════════════════

# ── WATCHLIST DE CRÉDITO ──────────────────────────────────────────────────────
# Empresas monitoradas pelo módulo credito.py
# Adicione/remova conforme necessário
# CNPJ no formato com máscara (xx.xxx.xxx/xxxx-xx)
WATCHLIST_CREDITO_CONFIG = [
    # Grandes empresas já monitoradas pelo market_bot
    {"ticker": "PETR4",  "cnpj": "00.362.305/0001-04", "nome": "Petrobras",       "setor": "petroleo"},
    {"ticker": "VALE3",  "cnpj": "33.592.510/0001-54", "nome": "Vale",            "setor": "mineracao"},
    {"ticker": "ITUB4",  "cnpj": "60.701.190/0001-04", "nome": "Itaú Unibanco",   "setor": "bancos"},
    {"ticker": "BBDC4",  "cnpj": "60.746.948/0001-12", "nome": "Bradesco",        "setor": "bancos"},
    {"ticker": "BBAS3",  "cnpj": "00.000.000/0001-91", "nome": "Banco do Brasil", "setor": "bancos"},
    {"ticker": "WEGE3",  "cnpj": "84.429.695/0001-11", "nome": "WEG",             "setor": "industrial"},
    {"ticker": "SUZB3",  "cnpj": "16.404.287/0001-55", "nome": "Suzano",          "setor": "papel"},
    {"ticker": "GGBR4",  "cnpj": "33.611.500/0001-19", "nome": "Gerdau",          "setor": "siderurgia"},
    {"ticker": "ABEV3",  "cnpj": "00.395.288/0003-78", "nome": "Ambev",           "setor": "alimentos"},
    {"ticker": "RAIL3",  "cnpj": "02.387.241/0001-60", "nome": "Rumo Logística",  "setor": "logistica"},
    # Adicione aqui empresas de clientes que você assessora:
    # {"ticker": "XXXX3", "cnpj": "xx.xxx.xxx/xxxx-xx", "nome": "Empresa X", "setor": "setor"},
]

# ── PARÂMETROS DE ALERTA DE CRÉDITO ──────────────────────────────────────────
CREDITO_LIMIAR_SCORE_QUEDA  = 1.5   # Queda de 1.5+ pts no score → alerta
CREDITO_LIMIAR_ZSCORE_QUEDA = 0.5   # Queda de 0.5+ no Altman Z'' → alerta
CREDITO_LIMIAR_FSCORE_QUEDA = 2     # Queda de 2+ pts no Piotroski → alerta
CREDITO_LIMIAR_ALAV_SUBIDA  = 0.5   # Alta de 0.5x na alavancagem → alerta

# ── FONTES DE DADOS (futura expansão) ────────────────────────────────────────
# Para protestos reais, cadastre em: https://valida.api.br/
VALIDA_API_TOKEN = ""  # Deixar vazio para usar apenas fontes gratuitas

# ── AGENDA DE CRÉDITO ─────────────────────────────────────────────────────────
CREDITO_DIA_SEMANA = "monday"   # Dia do ciclo semanal de crédito
CREDITO_HORARIO    = "09:30"    # Horário (após ciclo_semanal normal)
