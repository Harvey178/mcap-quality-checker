# MCAP 采集盒自动检测工具

本项目运行在 Windows，通过 SSH 连接肌电采集盒，在盒子端检查
`/mnt/tf/bronze` 中的 MCAP 文件，并将测试报告、运行日志和异常材料保存到
Windows 本地。正常 MCAP 不会下载，只有发现异常时才下载问题文件及
`/rkbox/log` 对应时间段的日志。

## 检查内容

- 检查目录中是否存在大小为 0 KB 的 MCAP；
- 按当天日期作为随机种子，随机抽取 5 个稳定文件；
- 检查文件是否损坏或无法解析；
- 检查视频、头 IMU、左右 IMU、左右 EMG 帧率，允许误差为 5%；
- 选择 RGB 中间帧作为基准时间；
- 将六路设备时间统一换算后，与 RGB 时间进行比较；
- 时间同步阈值为 1 秒；
- 异常时下载问题 MCAP、检查说明和问题时间前后 60 秒的盒子日志。

## 运行环境

- Windows 10 或 Windows 11；
- Python 3.10 或更高版本；
- Windows 能通过 SSH 访问采集盒；
- PowerShell 5.1 或更高版本；
- Visual Studio Code（可选）。

Python 包统一写在 `requirements.txt`：

```text
mcap==1.4.0
mcap-protobuf-support==0.5.4
paramiko==5.0.0
```

## 一、下载项目

```powershell
git clone https://github.com/Harvey178/mcap-quality-checker.git
cd mcap-quality-checker
```

也可以在 GitHub 页面选择 `Code` → `Download ZIP`，解压后用 PowerShell 或
Visual Studio Code 打开项目目录。

## 二、首次安装

如果系统禁止直接执行 PowerShell 脚本，先在项目目录执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

创建本地配置文件：

```powershell
Copy-Item .\client_config.example.json .\client_config.json
notepad .\client_config.json
```

至少填写以下内容：

```json
{
  "box_name": "box-01",
  "hostname": "lubancat.local",
  "port": 22,
  "username": "cat",
  "password": "请填写盒子密码",
  "remote_mcap_dir": "/mnt/tf/bronze",
  "remote_log_dir": "/rkbox/log",
  "sample_size": 5
}
```

`hostname` 可以填写盒子域名或当前 IP。程序连接成功后会自动获取盒子 IP，并
更新本地 `client_config.json`。

安装独立 Python 环境和依赖：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

## 三、手动运行

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_FG_windows.ps1
```

运行过程会在终端显示：

- 盒子名称、IP、连接状态和盒子时间；
- 文件完整性结果及有无异常文件；
- 每个抽检文件的名称；
- 六路数据的名称、帧率、帧率结果、Topic、时间 ns、UTC+8 时间、与视频差值和
  时间同步结果；
- 每个文件及本次盒子检测的最终 `PASS` 或 `FAIL`。

## 四、报告和异常文件

每次运行使用独立的时间戳目录：

```text
reports/<运行时间>/测试报告_*.txt
logs/<运行时间>/脚本运行.log
logs/<运行时间>/异常处理.log
logs/<运行时间>/盒子检测明细.log
anomalies/<运行时间>/<问题文件名>/
```

异常目录可能包含：

```text
问题文件.mcap
检查说明.log
rkbox问题时间日志.log
```

正常 MCAP 不下载到 Windows。本工具不会删除盒子中的 MCAP。

## 五、每天 08:00 和 20:00 自动运行

安装 Windows 计划任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_scheduled_tasks.ps1
```

任务名称为：

```text
MCAP_Box_Check_0800_2000
```

查看计划任务：

```powershell
Get-ScheduledTask -TaskName MCAP_Box_Check_0800_2000
Get-ScheduledTaskInfo -TaskName MCAP_Box_Check_0800_2000
```

立即试运行一次：

```powershell
Start-ScheduledTask -TaskName MCAP_Box_Check_0800_2000
```

删除计划任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall_scheduled_tasks.ps1
```

计划任务使用当前 Windows 用户身份，仅在用户登录状态下运行。电脑在触发时间
关机或休眠时，会在恢复后尽快补跑。

## 六、在 Visual Studio Code 中运行

1. 使用 VS Code 打开项目目录；
2. 按 `Ctrl+Shift+P`；
3. 选择 `Tasks: Run Task`；
4. 首次运行选择“首次安装 Python 环境”；
5. 打开“运行和调试”，选择“运行 MCAP 盒子检查”；
6. 按 `F5`。

也可以直接在 VS Code 终端执行：

```powershell
.\run_FG_windows.ps1
```

## 七、常见问题

### PowerShell 提示禁止运行脚本

使用带 `-ExecutionPolicy Bypass` 的完整命令运行，或者为当前用户设置
`RemoteSigned`。

### 连接盒子失败

依次检查：

```powershell
ping lubancat.local
ssh cat@lubancat.local
```

确认 Windows 和盒子位于同一网络，并检查配置中的主机名、端口、账号和密码。

### 抽检数量不足 5 个

程序只抽取已完成写入并达到稳定等待时间的 MCAP。目录中稳定文件不足时，实际
抽检数量会少于 5 个。

### 为什么只有异常文件被下载

分析在盒子端完成，避免每两分钟产生的 MCAP 大量占用 Windows 磁盘和网络。
只有异常文件需要留存复现，因此才会下载。

## 安全说明

以下内容已被 `.gitignore` 排除，不应提交到 GitHub：

- `client_config.json` 及 SSH 密码；
- 下载的 `*.mcap`；
- `reports/`、`logs/`、`anomalies/`；
- Python 虚拟环境 `.venv/`；
- PID 文件和临时运行日志。

公开提交前建议执行：

```powershell
git status
git check-ignore client_config.json
```

发现密码或 MCAP 被暂存时，不要执行 `git commit` 或 `git push`。
