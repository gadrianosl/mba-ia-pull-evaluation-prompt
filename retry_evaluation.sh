#!/bin/bash
# Script para reexecutar avaliação após reset de quota Gemini

echo "=========================================="
echo "Reexecutando Avaliação Completa"
echo "=========================================="
echo ""
echo "Provider: Gemini 2.5-flash (free tier)"
echo "Exemplos: 15 (4 já em cache, 11 novos)"
echo "Modo: Final Run (todas métricas)"
echo ""
echo "Iniciando..."
echo ""

python src/evaluate.py --final-run

echo ""
echo "=========================================="
echo "Avaliação Concluída"
echo "=========================================="
