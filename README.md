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
ruamel.yaml==0.18.10
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
Copy-Item .\boxes.example.yaml .\boxes.yaml
notepad .\boxes.yaml
```

至少填写以下内容：

```yaml
ssh:
  user: cat
  password: 请填写盒子密码
  port: 22

boxes:
  - name: box-01
    host: lubancat.local
    resolved_ip: ""
```

`host` 可以填写盒子域名或当前 IP。程序连接成功后会自动解析盒子 IP，并更新
对应盒子的 `resolved_ip`。如需检测多个盒子，在 `boxes:` 下继续添加即可；
脚本默认依次检测全部盒子。单个盒子需要不同的 SSH 账号或密码时，可以在该盒子
下增加 `ssh:` 覆盖全局设置。

### 完整配置示例

```yaml
remote_dir: /mnt/tf/bronze
remote_log_dir: /rkbox/log
remote_log_margin_sec: 60

report_dir: reports
task_log_dir: logs
anomaly_dir: anomalies
download_dir: downloads

sample_count: 5
stable_seconds: 180

imu_hz: 100
emg_hz: 2000
rgb_hz: 30
rate_tolerance: 0.05
max_time_delta_sec: 1.0

ssh:
  user: cat
  password: 请填写默认密码
  port: 22
  connect_timeout: 15

boxes:
  - name: box-01
    host: lubancat.local
    resolved_ip: ""

  - name: box-02
    host: 192.168.137.190
    resolved_ip: ""
    ssh:
      user: cat
      password: 请填写box-02密码
      port: 22
```

### 配置项说明

| 配置项 | 默认值 | 用途 |
|---|---:|---|
| `remote_dir` | `/mnt/tf/bronze` | 盒子上的 MCAP 目录 |
| `remote_log_dir` | `/rkbox/log` | 盒子运行日志目录 |
| `remote_log_margin_sec` | `60` | 异常时间前后提取日志的秒数 |
| `report_dir` | `reports` | Windows TXT 报告目录 |
| `task_log_dir` | `logs` | Windows 任务日志目录 |
| `anomaly_dir` | `anomalies` | 异常文件及日志目录 |
| `sample_count` | `5` | 每个盒子随机抽检的 MCAP 数量 |
| `stable_seconds` | `180` | 文件多久未修改才认为写入完成 |
| `imu_hz` | `100` | 头部及左右 IMU 目标帧率 |
| `emg_hz` | `2000` | 左右 EMG 目标帧率 |
| `rgb_hz` | `30` | RGB 视频目标帧率 |
| `rate_tolerance` | `0.05` | 帧率允许误差，`0.05` 表示 5% |
| `max_time_delta_sec` | `1.0` | 六路时间与 RGB 的最大允许差值 |
| `ssh.connect_timeout` | `15` | SSH 连接超时秒数 |
| `boxes[].name` | 必填 | 盒子名称，也是报告目录名称的一部分 |
| `boxes[].host` | 必填 | 盒子域名或 IP |
| `boxes[].resolved_ip` | 自动填写 | 最近一次成功解析出的 IP |

不要手动依赖 `resolved_ip` 作为主地址。脚本优先解析 `host`，解析失败时才使用
上次保存的 `resolved_ip`。

安装独立 Python 环境和依赖：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

## 三、手动运行

检测 `boxes.yaml` 中的全部盒子：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_FG_windows.ps1
```

只检测某一个盒子：

```powershell
.\.venv\Scripts\python.exe .\mcap_check_for_FG.py --box box-01
```

临时指定另一份配置：

```powershell
.\.venv\Scripts\python.exe .\mcap_check_for_FG.py --config D:\config\boxes.yaml
```

多盒子按 `boxes:` 中的顺序依次执行。如果任意一个盒子检测失败，脚本最终返回
失败状态。

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
reports/<运行时间_盒子名称>/测试报告_*.txt
logs/<运行时间_盒子名称>/脚本运行.log
logs/<运行时间_盒子名称>/异常处理.log
logs/<运行时间_盒子名称>/盒子检测明细.log
anomalies/<运行时间_盒子名称>/<问题文件名>/
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
关机或休眠时，会在恢复后尽快补跑。计划任务每次都会读取最新的 `boxes.yaml`，
所以后续新增盒子不需要重新创建计划任务。

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

- `boxes.yaml` 及 SSH 密码；
- 下载的 `*.mcap`；
- `reports/`、`logs/`、`anomalies/`；
- Python 虚拟环境 `.venv/`；
- PID 文件和临时运行日志。

公开提交前建议执行：

```powershell
git status
git check-ignore boxes.yaml
```

发现密码或 MCAP 被暂存时，不要执行 `git commit` 或 `git push`。

## 项目文件说明

| 文件 | 用途 |
|---|---|
| `mcap_check_for_FG.py` | Windows 主控制器，负责 SSH、抽样、远程分析、报告和异常下载 |
| `box_config.py` | 读取多盒子 YAML、合并 SSH 配置并更新解析后的 IP |
| `boxes.example.yaml` | 可公开提交的带注释配置模板 |
| `boxes.yaml` | 包含真实账号密码的本地配置，不提交 Git |
| `mcap_check_for_FG_local.py` | 上传到盒子执行的 Foxglove 口径分析入口 |
| `mcap_daily_check.py` | MCAP Topic、帧率、时间字段和同步计算核心 |
| `mcap_integrity_check.py` | 0KB 和随机样本完整解析检查 |
| `extract_remote_logs.py` | 提取问题时刻附近的 `/rkbox/log` 日志 |
| `mcap_check_config.json` | 远程分析的内部默认参数和六路 Topic 定义 |
| `run_FG_windows.ps1` | 推荐的 Windows 手动/计划任务运行入口 |
| `run_windows.ps1` | 下载抽样 MCAP 后在 Windows 分析的兼容入口 |
| `setup_windows.ps1` | 创建虚拟环境并安装依赖 |
| `install_scheduled_tasks.ps1` | 安装每天 08:00、20:00 的计划任务 |
| `uninstall_scheduled_tasks.ps1` | 删除计划任务 |

`mcap_check_config.json` 使用严格 JSON 格式，JSON 标准不允许写注释。字段说明如下：

- `stable_seconds`：内部默认的文件稳定等待秒数；
- `rate_tolerance_percent`：帧率允许误差百分比；
- `gap_factor`：判定异常长间隔的倍数；
- `sync_threshold_ms`：与 RGB 时间同步的允许差值；
- `coverage_threshold_ms`：流时间覆盖范围的允许差值；
- `streams`：六路 Topic、目标帧率、时间字段路径及时间单位。

运行时会用 `boxes.yaml` 中的参数覆盖对应内部默认值，因此日常调整应优先修改
`boxes.yaml`，不需要直接修改 `mcap_check_config.json`。

## 更新现有安装

代码更新或 `requirements.txt` 发生变化后，在项目目录执行：

```powershell
git pull
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

`git pull` 不会覆盖本地 `boxes.yaml`，因为该文件不进入 Git 版本管理。
