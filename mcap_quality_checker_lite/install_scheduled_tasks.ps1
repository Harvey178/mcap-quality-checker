# 创建每天 08:00 和 20:00 运行的 Windows 计划任务。
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $Root "run_FG_windows.ps1"
$PowerShell = (Get-Command powershell.exe).Source
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$TaskName = "MCAP_Box_Check_0800_2000"

if (-not (Test-Path -LiteralPath (Join-Path $Root ".venv\Scripts\python.exe"))) {
    throw "Local Python environment not found. Run .\setup_windows.ps1 first."
}

# 计划任务运行当前项目的远程分析入口。
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument $Arguments `
    -WorkingDirectory $Root
# 使用两个每日触发器，错过时间后由 StartWhenAvailable 尽快补跑。
$MorningTrigger = New-ScheduledTaskTrigger -Daily -At "08:00"
$EveningTrigger = New-ScheduledTaskTrigger -Daily -At "20:00"
$Triggers = @($MorningTrigger, $EveningTrigger)
# Interactive 表示仅在当前 Windows 用户已经登录时运行。
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Check box MCAP daily at 08:00 and 20:00; save reports on Windows." `
    -Force | Out-Null

Write-Host "Scheduled task created: $TaskName"
Get-ScheduledTask -TaskName $TaskName |
    Get-ScheduledTaskInfo |
    Select-Object LastRunTime, LastTaskResult, NextRunTime
