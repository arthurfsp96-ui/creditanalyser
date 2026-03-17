#!/bin/bash
echo "=== Testando APIs ==="
echo ""
echo "1. BrasilAPI (CNPJ Petrobras):"
curl -s "https://brasilapi.com.br/api/cnpj/v1/33592510000154" | python3 -m json.tool 2>/dev/null | grep -E "razao_social|cnae_fiscal_descricao|municipio|uf|capital" | head -8

echo ""
echo "2. CVM (Cadastro empresas abertas):"
curl -s "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv" | head -3

echo ""
echo "3. ReceitaWS:"
curl -s "https://receitaws.com.br/v1/cnpj/33592510000154" | python3 -m json.tool 2>/dev/null | grep -E "nome|situacao|cnae" | head -5
