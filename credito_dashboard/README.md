# Dashboard — Análise de Crédito Corporativo
## CSN · CSNA3 · DFP 31/12/2025

Dashboard web para geração automatizada de relatórios de análise de crédito
a partir de documentos oficiais (DFP/FRE) com premissas macroeconômicas editáveis.

---

## Instalação

```bash
pip install flask flask-cors pdfplumber reportlab
```

## Execução

```bash
cd credito_dashboard
python app.py
```

Acesse: **http://localhost:5000**

---

## Como usar

1. **Upload de Documentos** — Arraste a DFP (Demonstrações Financeiras) e o FRE (Formulário de Referência) em PDF
2. **Processar** — Clique em "Processar documentos" para extrair os dados automaticamente
3. **Premissas Macro** — Ajuste os sliders ou insira valores manualmente:
   - USD/BRL
   - Selic (% a.a.)
   - IPCA (% a.a.)
   - CDI (% a.a.)
   - Minério Fe 62% (US$/t)
   - HRC China Export (US$/t)
   - Treasury 10Y EUA (%)
4. **Gerar PDF** — Clique em "Gerar Análise de Crédito em PDF"

O relatório inclui: Sumário Executivo, DRE Consolidada, Análise por Segmento,
Estrutura de Capital, Cronograma de Vencimentos, Análise de Sensibilidade,
Cenários, Matriz de Riscos e Recomendação de Crédito.

---

## Estrutura

```
credito_dashboard/
├── app.py              # Backend Flask (extração PDF + geração relatório)
├── templates/
│   └── index.html      # Frontend dashboard
└── README.md
```

## Dependências

```
flask>=2.0
flask-cors>=3.0
pdfplumber>=0.9
reportlab>=4.0
```
