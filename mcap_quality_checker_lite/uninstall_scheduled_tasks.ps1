# 删除本项目创建的 Windows 计划任务，不删除报告、配置或 MCAP。
$ErrorActionPreference = "Stop"
$TaskName = "MCAP_Box_Check_0800_2000"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "计划任务已删除: $TaskName"
