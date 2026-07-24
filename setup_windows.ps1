$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
python -m venv (Join-Path $Root ".venv")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
Write-Host "安装完成。运行："
Write-Host "& `"$Python`" `"$Root\run_windows_local.py`""
