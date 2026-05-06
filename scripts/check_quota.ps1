$pyExe = "$env:LocalAppData\Programs\Python\Python311\python.exe"
if (-not (Test-Path $pyExe)) {
  $pyExe = "python"
}

$env:PYTHONIOENCODING = "utf-8"

$code = @"
from dotenv import load_dotenv
import sys

load_dotenv()

try:
    from src.utils import get_llm
    llm = get_llm(temperature=0)
    resp = llm.invoke("Responda apenas OK")
    content = getattr(resp, "content", str(resp))
    print("QUOTA_OK")
    print(str(content)[:200])
    sys.exit(0)
except Exception as e:
    print("QUOTA_FAIL")
    print(str(e))
    sys.exit(2)
"@

& $pyExe -c $code
exit $LASTEXITCODE
