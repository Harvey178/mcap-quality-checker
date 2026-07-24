#!/usr/bin/env python3
"""Windows controller: run the check on the box, fetch failures and log excerpts."""

from __future__ import annotations

import argparse
import json
import posixpath
import random
import shlex
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paramiko

from box_config import get_box_names, resolve_and_update
from run_windows_local import download, walk_mcap


ROOT = Path(__file__).resolve().parent
RUN_LOG_PATH: Path | None = None
ERROR_LOG_PATH: Path | None = None
DISPLAY_NAMES = {
    "camera": "视频",
    "head_imu": "头IMU",
    "left_imu": "左IMU",
    "right_imu": "右IMU",
    "left_emg": "左EMG",
    "right_emg": "右EMG",
}


def ns_to_beijing(value: int) -> str:
    seconds, nanos = divmod(int(value), 1_000_000_000)
    beijing = timezone(timedelta(hours=8))
    base = datetime.fromtimestamp(seconds, tz=beijing)
    return f"{base:%Y-%m-%d %H:%M:%S}.{nanos:09d} +0800"


def append_log(path: Path | None, message: str) -> None:
    if path is None:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8-sig") as output:
        output.write(f"[{timestamp}] {message}\n")


def run_log(message: str) -> None:
    append_log(RUN_LOG_PATH, message)


def error_log(message: str) -> None:
    append_log(ERROR_LOG_PATH, message)


def remote_run(client: paramiko.SSHClient, command: str, timeout: int = 900) -> int:
    print("盒子执行检测...", flush=True)
    _, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=True)
    for line in iter(stdout.readline, ""):
        if line:
            print(line, end="", flush=True)
        if stdout.channel.exit_status_ready() and not line:
            break
    for line in stderr.readlines():
        print(line, end="", file=sys.stderr)
    return stdout.channel.recv_exit_status()


def remote_run_background(
    client: paramiko.SSHClient,
    sftp: paramiko.SFTPClient,
    command: str,
    remote_root: str,
    run_id: str,
    timeout: int = 900,
) -> int:
    script_path = f"{remote_root}/run_{run_id}.sh"
    log_path = f"{remote_root}/run_{run_id}.log"
    exit_path = f"{remote_root}/run_{run_id}.exit"
    with sftp.open(script_path, "w") as script:
        script.write("#!/usr/bin/env bash\n")
        script.write(command + "\n")
        script.write(f"echo $? > {shlex.quote(exit_path)}\n")
    sftp.chmod(script_path, 0o700)
    start = (
        f"rm -f {shlex.quote(log_path)} {shlex.quote(exit_path)}; "
        f"nohup bash {shlex.quote(script_path)} > {shlex.quote(log_path)} "
        "2>&1 < /dev/null &"
    )
    client.exec_command(start)[1].channel.recv_exit_status()
    deadline = time.time() + timeout
    print("盒子后台检测已启动。", flush=True)
    while time.time() < deadline:
        _, stdout, _ = client.exec_command(
            f"if [ -f {shlex.quote(exit_path)} ]; then "
            f"echo DONE:$(cat {shlex.quote(exit_path)}); else echo RUNNING; fi",
            timeout=15,
        )
        state = stdout.read().decode("utf-8", errors="replace").strip()
        if state.startswith("DONE:"):
            code = int(state.split(":", 1)[1])
            return code
        time.sleep(5)
    raise TimeoutError("盒子检测超过等待上限")


def main() -> int:
    global RUN_LOG_PATH, ERROR_LOG_PATH
    parser = argparse.ArgumentParser(description="Windows启动盒子检测并按需下载问题文件")
    parser.add_argument("--config", type=Path, default=ROOT / "boxes.yaml")
    parser.add_argument("--box", help="只检测指定名称的盒子；默认依次检测全部盒子")
    parser.add_argument("--seed", help="默认使用当天日期")
    args = parser.parse_args()
    config_path = args.config.resolve()
    if not args.box:
        box_names = get_box_names(config_path)
        if len(box_names) > 1:
            exit_codes = []
            for box_name in box_names:
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--config",
                    str(config_path),
                    "--box",
                    box_name,
                ]
                if args.seed:
                    command.extend(["--seed", args.seed])
                print(f"\n========== 开始检测盒子: {box_name} ==========", flush=True)
                exit_codes.append(subprocess.run(command, cwd=ROOT).returncode)
            return 1 if any(exit_codes) else 0
    config = resolve_and_update(config_path, args.box)
    ssh = config["ssh"]
    seed = args.seed or datetime.now().strftime("%Y%m%d")
    sample_size = int(config.get("sample_size", 5))
    stable_seconds = int(config.get("stable_seconds", 180))
    safe_box_name = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in config["box_name"]
    )
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}_{safe_box_name}"
    local_report = (
        ROOT / config.get("fg_report_directory", "./reports") / stamp
    ).resolve()
    task_log_dir = (
        ROOT / config.get("task_log_directory", "./logs") / stamp
    ).resolve()
    anomaly_run_dir = (
        ROOT / config.get("anomaly_directory", "./anomalies") / stamp
    ).resolve()
    local_report.mkdir(parents=True, exist_ok=True)
    task_log_dir.mkdir(parents=True, exist_ok=True)
    RUN_LOG_PATH = task_log_dir / "脚本运行.log"
    ERROR_LOG_PATH = task_log_dir / "异常处理.log"
    runtime_check_config = json.loads(
        (ROOT / "mcap_check_config.json").read_text(encoding="utf-8")
    )
    runtime_check_config["stable_seconds"] = stable_seconds
    runtime_check_config["rate_tolerance_percent"] = (
        float(config["rate_tolerance"]) * 100
    )
    runtime_check_config["sync_threshold_ms"] = (
        float(config["max_time_delta_sec"]) * 1000
    )
    for stream_name in ("head_imu", "left_imu", "right_imu"):
        runtime_check_config["streams"][stream_name]["expected_hz"] = config["imu_hz"]
    for stream_name in ("left_emg", "right_emg"):
        runtime_check_config["streams"][stream_name]["expected_hz"] = config["emg_hz"]
    runtime_check_config["streams"]["camera"]["expected_hz"] = config["rgb_hz"]
    runtime_check_config_path = task_log_dir / "检测参数.json"
    runtime_check_config_path.write_text(
        json.dumps(runtime_check_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    run_log(f"脚本启动，抽样日期={seed}")
    run_log(f"配置文件={args.config.resolve()}")
    error_log("异常处理日志初始化")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    run_log(f"开始连接盒子 {ssh['username']}@{ssh['ip']}")
    client.connect(
        ssh["ip"], port=int(ssh.get("port", 22)),
        username=ssh["username"], password=ssh["password"],
        timeout=10, auth_timeout=10,
    )
    run_log("SSH连接成功")
    sftp = client.open_sftp()
    _, box_stdout, _ = client.exec_command(
        "printf '%s\\n' \"$(hostname)\" \"$(date '+%Y-%m-%d %H:%M:%S %z')\"",
        timeout=15,
    )
    box_lines = box_stdout.read().decode("utf-8", errors="replace").splitlines()
    remote_hostname = box_lines[0] if box_lines else "unknown"
    box_time = box_lines[1] if len(box_lines) > 1 else "unknown"
    entries = list(walk_mcap(sftp, config["remote_data_directory"]))
    now = time.time()
    zero_files = sorted(path for path, item in entries if item.st_size == 0)
    eligible = sorted(
        [(path, item) for path, item in entries
         if item.st_size > 0 and now - item.st_mtime >= stable_seconds],
        key=lambda pair: pair[0],
    )
    selected = sorted(
        random.Random(seed).sample(eligible, min(sample_size, len(eligible))),
        key=lambda pair: pair[0],
    )
    run_log(
        f"远程扫描完成：MCAP={len(entries)}，0KB={len(zero_files)}，"
        f"可抽样={len(eligible)}，选中={len(selected)}"
    )
    print(f"远程MCAP文件总数量: {len(entries)}，完整性异常文件数量: {len(zero_files)}，随机抽样数量: {len(selected)}")

    remote_root = f"/home/{ssh['username']}/.mcap_windows_check"
    remote_report = f"{remote_root}/reports/{stamp}"
    client.exec_command(f"mkdir -p {shlex.quote(remote_report)}")[1].channel.recv_exit_status()
    uploads = {
        ROOT / "mcap_check_for_FG_local.py": f"{remote_root}/mcap_check_for_FG.py",
        ROOT / "mcap_daily_check.py": f"{remote_root}/mcap_daily_check.py",
        runtime_check_config_path: f"{remote_root}/mcap_check_config.json",
        ROOT / "extract_remote_logs.py": f"{remote_root}/extract_remote_logs.py",
    }
    for local, remote in uploads.items():
        sftp.put(str(local), remote)
    run_log("检测脚本与配置上传完成")

    python_path = (
        "USER_SITE=$(python3 -m site --user-site); "
        "export PYTHONPATH=\"$USER_SITE${PYTHONPATH:+:$PYTHONPATH}\"; "
    )
    command = (
        f"cd {shlex.quote(remote_root)} && {python_path}"
        f"python3 mcap_check_for_FG.py {shlex.quote(config['remote_data_directory'])} "
        f"--output {shlex.quote(remote_report)} "
        "--config mcap_check_config.json "
        f"--sample-size {sample_size} --random-seed {shlex.quote(seed)}"
    )
    check_exit = remote_run_background(
        client, sftp, command, remote_root, stamp, timeout=900
    )
    run_log(f"盒子检测结束，返回码={check_exit}")
    if check_exit:
        error_log(f"盒子检测返回非零状态：{check_exit}")
    remote_detail_log = f"{remote_root}/run_{stamp}.log"
    try:
        sftp.get(remote_detail_log, str(task_log_dir / "盒子检测明细.log"))
    except OSError:
        pass

    report_names = sftp.listdir(remote_report)
    json_names = sorted(name for name in report_names if name.lower().endswith(".json"))
    if not json_names:
        raise RuntimeError("盒子未生成JSON检测报告")
    with sftp.open(posixpath.join(remote_report, json_names[-1]), "r") as remote_json:
        report = json.loads(remote_json.read().decode("utf-8"))
    run_log(f"远程检测报告读取完成：{json_names[-1]}")
    failed_results = [item for item in report["results"] if item["status"] != "PASS"]
    damaged_results = [
        item for item in report["results"] if not item.get("container_valid", False)
    ]
    integrity_problem_files = zero_files + [item["file"] for item in damaged_results]
    abnormal_files = sorted(set(zero_files + [item["file"] for item in failed_results]))
    entry_by_path = {path: item for path, item in entries}
    for path in zero_files:
        item_dir = anomaly_run_dir / Path(path).stem
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / Path(path).name).write_bytes(b"")
        (item_dir / "检查说明.log").write_text(
            f"文件: {path}\n结果: FAIL\n问题: 0KB文件\n",
            encoding="utf-8-sig",
        )
        error_log(f"发现0KB异常文件并生成检查说明：{path}")

    for item in failed_results:
        remote_mcap = item["file"]
        item_dir = anomaly_run_dir / Path(remote_mcap).stem
        item_dir.mkdir(parents=True, exist_ok=True)
        remote_attr = entry_by_path.get(remote_mcap) or sftp.stat(remote_mcap)
        print(f"下载问题MCAP: {remote_mcap}", flush=True)
        error_log(f"开始下载异常MCAP：{remote_mcap}")
        download(sftp, remote_mcap, item_dir / Path(remote_mcap).name, remote_attr.st_size)
        error_log(f"异常MCAP下载完成：{item_dir / Path(remote_mcap).name}")
        explanation = [
            f"文件: {remote_mcap}",
            f"结果: {item.get('status', 'FAIL')}",
            f"容器有效: {item.get('container_valid', False)}",
            "问题: " + ("; ".join(item.get("issues", [])) or "-"),
        ]
        for name, stream in item.get("streams", {}).items():
            stream_issues = stream.get("issues", []) + stream.get("warnings", [])
            if stream.get("status") != "PASS" or stream_issues:
                explanation.append(
                    f"{name}: 帧率={stream.get('actual_hz', 0):.3f}Hz, "
                    f"结果={stream.get('status')}, "
                    f"问题={'; '.join(stream_issues) or '-'}"
                )
        for name, sync in item.get("sync_to_rgb_playback", {}).items():
            if sync.get("status") != "PASS":
                explanation.append(
                    f"{name}: 时间同步={sync.get('status')}, "
                    f"时间差={sync.get('max_playback_lag_ms', 0):.6f}ms"
                )
        (item_dir / "检查说明.log").write_text(
            "\n".join(explanation) + "\n", encoding="utf-8-sig"
        )
        error_log(f"检查说明生成完成：{item_dir / '检查说明.log'}")
        middle = item.get("selected_rgb_middle_frame", {})
        utc_time = middle.get("log_time_utc")
        if utc_time:
            remote_excerpt = f"{remote_root}/{Path(remote_mcap).stem}_problem.log"
            extract_command = (
                f"python3 {shlex.quote(remote_root + '/extract_remote_logs.py')} "
                f"--utc-time {shlex.quote(utc_time)} "
                f"--window-seconds {int(config['remote_log_margin_sec'])} "
                f"--log-dir {shlex.quote(config['remote_log_directory'])} "
                f"--output {shlex.quote(remote_excerpt)}"
            )
            remote_run(client, extract_command, timeout=120)
            sftp.get(remote_excerpt, str(item_dir / "rkbox问题时间日志.log"))
            error_log(
                f"问题时间日志提取完成：UTC={utc_time}，"
                f"文件={item_dir / 'rkbox问题时间日志.log'}"
            )

    for name in report_names:
        try:
            sftp.remove(posixpath.join(remote_report, name))
        except OSError:
            pass
    try:
        sftp.rmdir(remote_report)
    except OSError:
        pass
    sftp.close()
    client.close()
    failed = bool(check_exit or zero_files)
    integrity_abnormal_count = len(integrity_problem_files)
    report_lines: list[str] = []

    def emit(text: str = "") -> None:
        report_lines.append(text)
        print(text)

    emit()
    emit(f"[盒子] {config.get('box_name', remote_hostname)}  host={ssh['ip']}")
    emit("  连接: OK")
    emit(f"  盒子时间(北京): {box_time}")
    emit(f"  抽样日期: {seed}")
    emit(f"  本盒结果: {'FAIL' if failed else 'PASS'}")
    emit(
        f"  MCAP文件: {len(entries)}, 随机抽检: {len(selected)}, "
        f"完整性异常: {integrity_abnormal_count}"
    )
    if integrity_problem_files:
        emit(
            f"  文件完整性结果: FAIL  问题: "
            f"0KB={len(zero_files)}，损坏/无法解析={len(damaged_results)}"
        )
    else:
        emit("  文件完整性结果: PASS  问题: -")
    if abnormal_files:
        emit(f"  异常文件结果: 有异常文件，共{len(abnormal_files)}个")
        for path in abnormal_files:
            emit(f"    异常文件: {path}")
    else:
        emit("  异常文件结果: 无异常文件")
    result_by_file = {item["file"]: item["status"] for item in report["results"]}
    detail_by_file = {item["file"]: item for item in report["results"]}
    for remote_path, _ in selected:
        emit(f"  - [{result_by_file.get(remote_path, 'UNKNOWN')}] {Path(remote_path).name}")
        detail = detail_by_file.get(remote_path, {})
        selected_times = detail.get("selected_playback_times", {})
        streams = detail.get("streams", {})
        sync_results = detail.get("sync_to_rgb_playback", {})
        emit(
            "      名称    帧率         帧率结果  选项(topic)"
            "                       时间ns               时间UTC+8"
            "                                  差值          时间同步结果"
        )
        for stream_name in (
            "camera", "head_imu", "left_imu", "right_imu", "left_emg", "right_emg"
        ):
            timing = selected_times.get(stream_name)
            stream = streams.get(stream_name)
            if not timing or not stream:
                emit(f"      {DISPLAY_NAMES[stream_name]:<6}  无数据")
                continue
            difference = timing.get("relative_to_rgb_s")
            difference_text = (
                f"{difference:+.6f}s" if difference is not None else "N/A"
            )
            rate_result = stream.get("status", "FAIL")
            time_result = (
                "PASS"
                if stream_name == "camera"
                else sync_results.get(stream_name, {}).get("status", "FAIL")
            )
            emit(
                f"      {DISPLAY_NAMES[stream_name]:<6}  "
                f"{stream.get('actual_hz', 0):.3f}Hz  "
                f"{rate_result:<8}  "
                f"{stream.get('topic', ''):<30}  "
                f"{timing.get('log_time_ns', ''):<20}  "
                f"{ns_to_beijing(timing.get('log_time_ns', 0)):<42}  "
                f"{difference_text:<12}  "
                f"{time_result}"
            )
    for remote_path in zero_files:
        emit(f"  - [FAIL] {Path(remote_path).name} (0KB)")
    anomaly_mcap_count = len(failed_results) + len(zero_files)
    if anomaly_mcap_count:
        emit(f"  问题MCAP: 已保存{anomaly_mcap_count}个")
        emit(f"  异常资料: {anomaly_run_dir}")
    else:
        emit("  问题MCAP: 0个，无需下载")
    txt_report = local_report / f"测试报告_{stamp}.txt"
    emit(f"  TXT测试报告: {txt_report}")
    emit(f"  任务日志: {task_log_dir}")
    txt_report.write_text("\n".join(report_lines) + "\n", encoding="utf-8-sig")
    run_log(f"TXT测试报告生成完成：{txt_report}")
    run_log(
        f"脚本结束，结果={'FAIL' if failed else 'PASS'}，"
        f"异常文件={len(abnormal_files)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        error_log(
            f"未捕获异常：{type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}"
        )
        run_log(f"脚本异常终止：{type(exc).__name__}: {exc}")
        print(f"脚本运行异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
