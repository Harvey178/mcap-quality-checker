# MCAP采集盒自动检测工具

Windows通过SSH连接采集盒，自动更新盒子IP，在盒子上随机检查
`/mnt/tf/bronze` 中5个稳定MCAP。正常MCAP不下载；检测报告和任务日志保存到Windows，
仅异常时下载问题MCAP并提取 `/rkbox/log` 对应时间的日志。

## 检查内容

- 全目录0KB文件；
- 随机5个MCAP是否损坏或无法解析；
- 各topic帧率及±5%误差；
- 选择RGB中间帧，比较头IMU、左右IMU和左右EMG的播放时间；
- 时间同步阈值为1秒；
- 异常MCAP、检查说明和问题时间前后60秒的盒子日志。

## 环境与依赖

- Windows 10/11
- Python 3.10或更高版本
- Windows可通过SSH访问采集盒
- Visual Studio Code（可选）

Python依赖：

- `mcap==1.4.0`
- `mcap-protobuf-support==0.5.4`
- `paramiko==5.0.0`

## 首次安装

```powershell
Copy-Item .\client_config.example.json .\client_config.json
notepad .\client_config.json
.\setup_windows.ps1
```

在 `client_config.json` 中填写盒子账号和密码。该文件含明文密码，已被
`.gitignore` 排除，禁止提交到GitHub。

## 手动运行

```powershell
.\run_FG_windows.ps1
```

输出目录：

```text
reports/       TXT测试报告
logs/          脚本运行、异常处理和盒子检测明细日志
anomalies/     异常MCAP、检查说明和rkbox问题时间日志
```

不保留CSV或JSON报告。正常MCAP不会下载。

## Visual Studio Code

1. 用VS Code打开本项目目录；
2. `Ctrl+Shift+P` → `Tasks: Run Task` → `首次安装Python环境`；
3. 打开“运行和调试”，选择“运行MCAP盒子检查”；
4. 按 `F5`。

## 每日08:00和20:00自动运行

```powershell
.\install_scheduled_tasks.ps1
```

查看状态：

```powershell
Get-ScheduledTask -TaskName MCAP_Box_Check_0800_2000
Get-ScheduledTaskInfo -TaskName MCAP_Box_Check_0800_2000
```

删除任务：

```powershell
.\uninstall_scheduled_tasks.ps1
```

计划任务使用当前Windows用户身份，在用户登录状态下运行。错过触发时间时会尽快补跑。
任务不会删除盒子内的MCAP文件。

## GitHub安全说明

以下内容不会提交：

- `client_config.json`及SSH密码；
- 下载的MCAP；
- reports、logs、anomalies运行产物；
- Python虚拟环境。

提交前请确认 `git status` 中不存在密码或MCAP。
