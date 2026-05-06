param([switch]$Check)

if ($Check) {
    Write-Host "Verificando quota..."
    $pyExe = "$env:LocalAppData\Programs\Python\Python311\python.exe"
    if (-not (Test-Path $pyExe)) { $pyExe = "python" }
    $env:PYTHONIOENCODING = "utf-8"
    
    $code = @"
from dotenv import load_dotenv
import sys
load_dotenv()
try:
    from src.utils import get_llm
    llm = get_llm()
    resp = llm.invoke('OK')
    print('QUOTA_OK')
except Exception as e:
    print('QUOTA_FAIL: ' + str(e)[:100])
"@
    & $pyExe -c $code
    exit
}

Write-Host "=========================================="
Write-Host "Reexecutando Avaliação Completa"
Write-Host "=========================================="
Write-Host ""
Write-Host "Provider: Gemini 2.5-flash (free tier)"
Write-Host "Exemplos: 15 (4 já em cache, 11 novos)"
Write-Host "Modo: Final Run (todas métricas)"
Write-Host ""
Write-Host "Iniciando..."
Write-Host ""

$env:PYTHONIOENCODING = "utf-8"
python src/evaluate.py --final-run

Write-Host ""
Write-Host "=========================================="
Write-Host "Avaliação Concluída"
Write-Host "=========================================="
