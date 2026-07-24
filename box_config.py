"""Load the commented multi-box YAML configuration."""

from __future__ import annotations

import socket
from pathlib import Path

from ruamel.yaml import YAML


def _yaml() -> YAML:
    """创建能够保留注释、引号和键顺序的 YAML 读写器。"""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def get_box_names(config_path: Path) -> list[str]:
    """按配置顺序返回所有有效盒子名称，供多盒子调度使用。"""
    data = _yaml().load(config_path.read_text(encoding="utf-8"))
    boxes = data.get("boxes") or []
    names = [str(box.get("name", "")).strip() for box in boxes]
    return [name for name in names if name]


def resolve_and_update(config_path: Path, box_name: str | None = None) -> dict:
    """解析指定盒子、刷新 IP，并返回检测脚本使用的统一配置。"""
    yaml = _yaml()
    data = yaml.load(config_path.read_text(encoding="utf-8"))
    boxes = data.get("boxes") or []
    if not boxes:
        raise RuntimeError("boxes.yaml 中没有配置任何盒子")

    # 未指定 --box 时使用第一台；入口脚本会在多盒子场景逐台调用。
    selected = None
    if box_name:
        selected = next(
            (box for box in boxes if str(box.get("name", "")).strip() == box_name),
            None,
        )
        if selected is None:
            raise RuntimeError(f"boxes.yaml 中不存在盒子: {box_name}")
    else:
        selected = boxes[0]

    host = str(selected.get("host", "")).strip()
    old_ip = str(selected.get("resolved_ip", "")).strip()
    try:
        resolved_ip = socket.gethostbyname(host or old_ip)
    except socket.gaierror:
        if not old_ip:
            raise RuntimeError(f"无法解析盒子 {selected.get('name')} 的地址: {host}")
        resolved_ip = old_ip

    if old_ip != resolved_ip:
        selected["resolved_ip"] = resolved_ip
        with config_path.open("w", encoding="utf-8") as output:
            yaml.dump(data, output)

    # 单盒子的 SSH 节点覆盖全局默认值。
    global_ssh = dict(data.get("ssh") or {})
    global_ssh.update(dict(selected.get("ssh") or {}))
    ssh = {
        "hostname": host,
        "ip": resolved_ip,
        "port": int(global_ssh.get("port", 22)),
        "username": str(global_ssh.get("user", "cat")),
        "password": str(global_ssh.get("password", "")),
        "connect_timeout": int(global_ssh.get("connect_timeout", 15)),
    }
    if not ssh["password"] or ssh["password"] == "PLEASE_CHANGE_ME":
        raise RuntimeError(f"请在 boxes.yaml 中填写盒子 {selected.get('name')} 的 SSH 密码")

    return {
        "box_name": str(selected.get("name") or host or resolved_ip),
        "ssh": ssh,
        "remote_data_directory": str(data.get("remote_dir", "/mnt/tf/bronze")),
        "remote_log_directory": str(data.get("remote_log_dir", "/rkbox/log")),
        "remote_log_margin_sec": int(data.get("remote_log_margin_sec", 60)),
        "sample_size": int(data.get("sample_count", 5)),
        "stable_seconds": int(data.get("stable_seconds", 180)),
        "download_directory": str(data.get("download_dir", "./downloads")),
        "report_directory": str(data.get("report_dir", "./reports")),
        "fg_report_directory": str(data.get("report_dir", "./reports")),
        "task_log_directory": str(data.get("task_log_dir", "./logs")),
        "anomaly_directory": str(data.get("anomaly_dir", "./anomalies")),
        "imu_hz": float(data.get("imu_hz", 100)),
        "emg_hz": float(data.get("emg_hz", 2000)),
        "rgb_hz": float(data.get("rgb_hz", 30)),
        "rate_tolerance": float(data.get("rate_tolerance", 0.05)),
        "max_time_delta_sec": float(data.get("max_time_delta_sec", 1.0)),
    }
