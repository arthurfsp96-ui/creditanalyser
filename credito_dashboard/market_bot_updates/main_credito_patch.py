#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main_credito_patch.py
─────────────────────
INSTRUÇÕES: Este arquivo contém os trechos que você deve adicionar
ao seu main.py existente para integrar o módulo de crédito.

NÃO substitua o main.py inteiro — apenas adicione os trechos abaixo
nos locais indicados pelos comentários.
"""

# ══════════════════════════════════════════════════════════════
# PASSO 1 — No topo do main.py, adicione este import:
# ══════════════════════════════════════════════════════════════

from credito import ciclo_credito_semanal, relatorio_empresa_on_demand, alertar_deterioracao_ativa


# ══════════════════════════════════════════════════════════════
# PASSO 2 — Adicione esta função no main.py (antes do if __name__):
# ══════════════════════════════════════════════════════════════

def ciclo_semanal():
    """
    Ciclo de segunda-feira 09:00.
    SUBSTITUA a função ciclo_semanal() existente por esta versão expandida,
    ou simplesmente adicione a chamada ao credito no final da existente.
    """
    print("\n" + "="*50)
    print("CICLO SEMANAL — SEGUNDA-FEIRA 09:00")
    print("="*50)

    # >>> Suas chamadas originais aqui (não remover) <<<
    # alerta_abertura()  # já existia
    # resumo_semanal_ia()  # já existia
    # (etc.)

    # >>> NOVO: Monitoramento de crédito <<<
    print("\n📋 Iniciando monitoramento de crédito semanal...")
    try:
        ciclo_credito_semanal()
    except Exception as e:
        print(f"[ERRO] ciclo_credito_semanal: {e}")
        import traceback
        traceback.print_exc()


# ══════════════════════════════════════════════════════════════
# PASSO 3 — No ciclo_monitoramento() (roda a cada 2h), adicione:
# ══════════════════════════════════════════════════════════════

def ciclo_monitoramento():
    """
    Modifique seu ciclo_monitoramento() existente adicionando a chamada abaixo
    no final da função:
    """
    # >>> Suas chamadas originais aqui (não remover) <<<
    # checar_e_alertar()   # já existia
    # checar_correlacao()  # já existia

    # >>> NOVO: Checar deteriorações de crédito no histórico <<<
    try:
        alertar_deterioracao_ativa()
    except Exception as e:
        print(f"[AVISO] alertar_deterioracao_ativa: {e}")


# ══════════════════════════════════════════════════════════════
# PASSO 4 — Handler de comandos Telegram (/credito TICKER)
# Adicione ao processador de mensagens Telegram existente:
# ══════════════════════════════════════════════════════════════

def processar_comando_credito(texto: str) -> bool:
    """
    Processa o comando /credito TICKER enviado no Telegram.
    Chame esta função no seu handler de updates/polling.

    Exemplo de uso no handler:
        if texto.startswith("/credito"):
            processar_comando_credito(texto)
            return
    """
    partes = texto.strip().split()
    if len(partes) < 2:
        from credito import _enviar_telegram
        _enviar_telegram(
            "💳 <b>Uso:</b> /credito TICKER\n"
            "Exemplo: /credito VALE3\n\n"
            "<i>Gera relatório completo de crédito com Altman Z'', "
            "Piotroski F-Score, DSCR e score composto.</i>"
        )
        return True
    ticker = partes[1].upper()
    relatorio_empresa_on_demand(ticker)
    return True


# ══════════════════════════════════════════════════════════════
# PASSO 5 — Adicione a agenda semanal (schedule)
# No bloco where you configure o schedule (abaixo do if __name__):
# ══════════════════════════════════════════════════════════════

"""
Adicione estas linhas ao bloco de schedule do main.py:

    import schedule

    # Crédito semanal — segunda às 09:30 (após o ciclo_semanal normal)
    schedule.every().monday.at("09:30").do(ciclo_credito_semanal)

    # Verificação de deteriorações a cada 2h (junto ao monitoramento)
    # Já está integrado no ciclo_monitoramento() — não precisa de linha separada
"""
