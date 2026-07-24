# Windows main entry: analyze on the box; download only abnormal MCAP files.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
# Always use the project virtual environment for reproducible dependencies.
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Local environment is missing. Run .\setup_windows.ps1 first."
}
& $Python (Join-Path $Root "mcap_check_for_FG.py")
exit $LASTEXITCODE
