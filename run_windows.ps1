# Compatibility entry: download sampled MCAP files and analyze on Windows.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
# The local-analysis mode also uses the project virtual environment.
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Local environment is missing. Run .\setup_windows.ps1 first."
}
& $Python (Join-Path $Root "run_windows_local.py")
exit $LASTEXITCODE
