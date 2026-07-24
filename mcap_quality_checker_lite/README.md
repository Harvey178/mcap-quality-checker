# MCAP 采集盒检测工具（轻量版）

这是从完整项目中提取的独立运行版本，只保留当前实际使用的“Windows 控制 +
盒子端分析”链路。正常 MCAP 在盒子端直接分析，不下载到 Windows；只有异常文件
才会下载。

## 已整合和移除的内容

- 远程 MCAP 枚举与异常下载已整合进 `mcap_check_for_FG.py`；
- 文件完整性检查已由盒子端分析器统一完成；
- 不再包含独立的 `mcap_integrity_check.py`；
- 不再包含下载正常 MCAP 后在 Windows 分析的旧模式；
- 不再包含 `run_windows_local.py` 和 `run_windows.ps1`；
- 不生成 CSV 或 JSON 最终报告，只在 Windows 保存 TXT 报告。

完整性检查仍然包括：

- 全目录 0KB 文件；
- 随机抽取5个稳定 MCAP；
- MCAP 容器能否解析；
- Summary/Statistics 是否存在；
- 实际解析消息数与 Summary 消息数是否一致。

## 文件说明

| 文件 | 用途 |
|---|---|
| `mcap_check_for_FG.py` | Windows 唯一主入口 |
| `data_analyze.py` | 上传到盒子执行的完整性、帧率和时间同步分析器 |
| `box_config.py` | 多盒子 YAML 配置读取和 IP 更新 |
| `extract_remote_logs.py` | 提取异常时刻附近的盒子日志 |
| `mcap_check_config.json` | 六路 Topic 和时间字段内部定义 |
| `boxes.example.yaml` | 多盒子配置模板 |
| `run_FG_windows.ps1` | 手动运行入口 |
| `setup_windows.ps1` | 安装 Python 环境和依赖 |
| `install_scheduled_tasks.ps1` | 安装每天08:00、20:00计划任务 |

轻量版的盒子端分析已经全部整合到 `data_analyze.py`，不需要
`mcap_daily_check.py`。

## 首次安装

```powershell
Copy-Item .\boxes.example.yaml .\boxes.yaml
notepad .\boxes.yaml
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

填写 `boxes.yaml` 中的 SSH 密码和盒子地址。真实 `boxes.yaml` 已被
`.gitignore` 排除。

## 手动运行

检测全部盒子：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_FG_windows.ps1
```

只检测指定盒子：

```powershell
.\.venv\Scripts\python.exe .\mcap_check_for_FG.py --box box-01
```

## 定时运行

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_scheduled_tasks.ps1
```

计划任务每天08:00和20:00运行，名称为：

```text
MCAP_Box_Check_0800_2000
```

## 本地输出

```text
reports/<时间_盒子名称>/测试报告_*.txt
logs/<时间_盒子名称>/脚本运行.log
logs/<时间_盒子名称>/异常处理.log
logs/<时间_盒子名称>/盒子检测明细.log
anomalies/<时间_盒子名称>/<问题文件名>/
```

异常目录包含问题 MCAP、检查说明和对应时间段的 `/rkbox/log` 日志。
