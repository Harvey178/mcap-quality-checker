$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "未安装本地环境，请先执行 .\setup_windows.ps1"
}
& $Python (Join-Path $Root "mcap_check_for_FG.py")
exit $LASTEXITCODE
