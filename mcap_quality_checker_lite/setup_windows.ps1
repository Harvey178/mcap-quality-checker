# 首次安装或依赖更新后运行：创建项目独立虚拟环境并安装 requirements.txt。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
# 虚拟环境固定放在项目目录，确保手动运行与计划任务使用相同依赖。
python -m venv (Join-Path $Root ".venv")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
Write-Host "安装完成。运行："
Write-Host "& `"$Python`" `"$Root\mcap_check_for_FG.py`""
