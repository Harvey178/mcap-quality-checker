#!/usr/bin/env python3
"""Windows-local controller: sample remote MCAPs over SFTP and check locally."""

from __future__ import annotations

import argparse
import csv
import json
import random
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

import paramiko


ROOT = Path(__file__).resolve().parent


def resolve_and_update(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    ssh = config["ssh"]
    try:
        ssh["ip"] = socket.gethostbyname(ssh.get("hostname") or ssh["ip"])
    except socket.gaierror:
        if not ssh.get("ip"):
            raise RuntimeError("无法解析盒子主机名，且配置中没有可用IP")
    temporary = config_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(config_path)
    return config


def walk_mcap(sftp: paramiko.SFTPClient, root: str):
    for entry in sftp.listdir_attr(root):
        path = str(PurePosixPath(root) / entry.filename)
        if stat.S_ISDIR(entry.st_mode):
            yield from walk_mcap(sftp, path)
        elif entry.filename.lower().endswith(".mcap"):
            yield path, entry


def download(sftp: paramiko.SFTPClient, remote: str, local: Path, size: int) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    last_percent = -1

    def progress(done: int, total: int) -> None:
        nonlocal last_percent
        percent = int(done * 100 / total) if total else 100
        if percent // 10 != last_percent // 10:
            print(f"  下载进度: {percent}% ({done / 1024**2:.1f}/{total / 1024**2:.1f} MB)", flush=True)
            last_percent = percent

    sftp.get(remote, str(local), callback=progress)
    if local.stat().st_size != size:
        raise IOError(f"下载大小不一致: 远程={size}, 本地={local.stat().st_size}")


def run_check(arguments: list[str]) -> int:
    print("本地执行: " + " ".join(arguments), flush=True)
    return subprocess.run(arguments, cwd=ROOT).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows本地通过SSH抽样检查盒子MCAP")
    parser.add_argument("--config", type=Path, default=ROOT / "client_config.json")
    parser.add_argument("--seed", help="默认使用当天日期")
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = resolve_and_update(config_path)
    ssh = config["ssh"]
    seed = args.seed or datetime.now().strftime("%Y%m%d")
    sample_size = int(config.get("sample_size", 5))
    stable_seconds = int(config.get("stable_seconds", 180))
    print(f"盒子IP已更新: {ssh['ip']}")
    print(f"连接账号: {ssh['username']}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        ssh["ip"], port=int(ssh.get("port", 22)),
        username=ssh["username"], password=ssh["password"],
        timeout=10, auth_timeout=10,
    )
    sftp = client.open_sftp()
    entries = list(walk_mcap(sftp, config["remote_data_directory"]))
    zero_files = [path for path, item in entries if item.st_size == 0]
    now = time.time()
    eligible = sorted([
        (path, item) for path, item in entries
        if item.st_size > 0 and now - item.st_mtime >= stable_seconds
    ], key=lambda pair: pair[0])
    selected = sorted(
        random.Random(seed).sample(eligible, min(sample_size, len(eligible))),
        key=lambda pair: pair[0],
    )
    print(f"远程MCAP: {len(entries)}，0KB: {len(zero_files)}，可抽样: {len(eligible)}")
    print(f"随机抽样: {len(selected)}，种子={seed}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    download_root = (ROOT / config["download_directory"] / stamp).resolve()
    report_root = (ROOT / config["report_directory"] / stamp).resolve()
    download_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    zero_csv = report_root / "远程0KB文件.csv"
    with zero_csv.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(["文件", "文件大小(B)", "结果", "问题"])
        for path in zero_files:
            writer.writerow([path, 0, "FAIL", "0KB文件"])

    for index, (remote, item) in enumerate(selected, 1):
        local = download_root / Path(remote).name
        print(f"[{index}/{len(selected)}] 下载: {remote}", flush=True)
        download(sftp, remote, local, item.st_size)
    sftp.close()
    client.close()

    if not selected:
        print("测试结果: FAIL  问题: 没有可抽样的稳定非零MCAP文件")
        return 1
    common = ["--include-unstable", "--sample-size", str(sample_size), "--random-seed", seed]
    integrity_exit = run_check([
        sys.executable, str(ROOT / "mcap_integrity_check.py"), str(download_root),
        "--output", str(report_root / "integrity"), "--config", str(ROOT / "mcap_check_config.json"),
        *common,
    ])
    internal_exit = run_check([
        sys.executable, str(ROOT / "mcap_daily_check.py"), str(download_root),
        "--output", str(report_root / "internal_time"), "--config", str(ROOT / "mcap_check_config.json"),
        *common,
    ])
    failed = bool(zero_files or integrity_exit or internal_exit)
    issues = []
    if zero_files:
        issues.append(f"远程发现{len(zero_files)}个0KB文件")
    if integrity_exit:
        issues.append("完整性检查失败")
    if internal_exit:
        issues.append("内部时间检查失败")
    print(f"测试结果: {'FAIL' if failed else 'PASS'}  问题: {'; '.join(issues) or '-'}")
    print(f"本地报告目录: {report_root}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
