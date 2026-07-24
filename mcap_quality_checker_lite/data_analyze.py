#!/usr/bin/env python3
"""Analyze MCAP integrity, frame rates, and synchronization on the data box."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcap.reader import make_reader


# 内置默认值保证远程脚本可以单文件运行；Windows 主控会上传本次动态配置覆盖它。
DEFAULT_CONFIG = {
    "stable_seconds": 180,
    "rate_tolerance_percent": 5.0,
    "gap_factor": 2.5,
    "sync_threshold_ms": 1000.0,
    "coverage_threshold_ms": 100.0,
    "streams": {
        "camera": {"topic": "/head/rgb/compressed", "expected_hz": 30.0},
        "head_imu": {"topic": "/head/imu", "expected_hz": 100.0},
        "left_imu": {"topic": "/forearm/left/imu", "expected_hz": 100.0},
        "right_imu": {"topic": "/forearm/right/imu", "expected_hz": 100.0},
        "left_emg": {"topic": "/forearm/left/emg_batch", "expected_hz": 2000.0},
        "right_emg": {"topic": "/forearm/right/emg_batch", "expected_hz": 2000.0},
    },
}

CSV_NAMES = {
    "camera": "RGB",
    "head_imu": "头IMU",
    "left_imu": "左IMU",
    "right_imu": "右IMU",
    "left_emg": "左EMG",
    "right_emg": "右EMG",
}

TERMINAL_NAMES = {
    "camera": "视频",
    "head_imu": "头IMU",
    "left_imu": "左IMU",
    "right_imu": "右IMU",
    "left_emg": "左EMG",
    "right_emg": "右EMG",
}


def load_config(path: Path | None) -> dict[str, Any]:
    """加载 JSON 检测参数，并与轻量版内置默认值合并。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path:
        custom = json.loads(path.read_text(encoding="utf-8"))
        for key, value in custom.items():
            if key == "streams":
                for name, spec in value.items():
                    cfg["streams"].setdefault(name, {}).update(spec)
            else:
                cfg[key] = value
    return cfg


def ns_to_utc(value: int) -> str:
    """将纳秒时间戳转换成保留九位小数的 UTC 字符串。"""
    seconds, nanos = divmod(value, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{base:%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z"


def percentile(values: list[int], q: float) -> float | None:
    """对整数序列计算线性插值百分位数。"""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return float(ordered[index])


def inspect_file(path: Path, cfg: dict[str, Any], write_details: bool, output: Path) -> dict[str, Any]:
    """按 Foxglove 播放时间检查单个 MCAP 的帧率、完整性和时间同步。"""
    started = time.time()
    topic_to_name = {spec["topic"]: name for name, spec in cfg["streams"].items()}
    log_times: dict[str, list[int]] = {name: [] for name in cfg["streams"]}
    report: dict[str, Any] = {
        "file": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "time_basis": "MCAP log_time (playback time)",
        "selection_rule": "latest topic message with log_time <= RGB log_time",
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "container_valid": False,
        "status": "FAIL",
        "issues": [],
    }
    try:
        with path.open("rb") as source:
            reader = make_reader(source)
            summary = reader.get_summary()
            if summary is None or summary.statistics is None:
                raise ValueError("MCAP 缺少可读取的 Summary/Statistics")
            stats = summary.statistics
            duration_s = (stats.message_end_time - stats.message_start_time) / 1e9
            if duration_s <= 0:
                raise ValueError("MCAP 全文件时间跨度无效")
            report.update(
                container_valid=True,
                mcap_message_count=stats.message_count,
                mcap_start_log_time_ns=stats.message_start_time,
                mcap_end_log_time_ns=stats.message_end_time,
                mcap_duration_s=duration_s,
            )
            for _, channel, message in reader.iter_messages(topics=list(topic_to_name)):
                log_times[topic_to_name[channel.topic]].append(int(message.log_time))

        for values in log_times.values():
            values.sort()

        streams: dict[str, Any] = {}
        for name, spec in cfg["streams"].items():
            count = len(log_times[name])
            actual_hz = count / duration_s
            expected_hz = float(spec["expected_hz"])
            error_pct = abs(actual_hz - expected_hz) / expected_hz * 100
            status = "PASS" if count and error_pct <= cfg["rate_tolerance_percent"] else "FAIL"
            streams[name] = {
                "topic": spec["topic"],
                "count": count,
                "expected_hz": expected_hz,
                "actual_hz": actual_hz,
                "rate_error_percent": error_pct,
                "status": status,
            }
        report["streams"] = streams

        rgb_times = log_times["camera"]
        target_names = [name for name in cfg["streams"] if name != "camera"]
        sync: dict[str, Any] = {}
        details: list[dict[str, Any]] = []
        threshold_ns = int(float(cfg["sync_threshold_ms"]) * 1_000_000)
        if not rgb_times:
            raise ValueError("RGB数据流为空，无法选择中间帧")
        middle_rgb_pos = len(rgb_times) // 2
        middle_rgb_index = middle_rgb_pos + 1
        middle_rgb_time = rgb_times[middle_rgb_pos]
        report["selected_rgb_middle_frame"] = {
            "frame_index": middle_rgb_index,
            "log_time_ns": middle_rgb_time,
            "log_time_utc": ns_to_utc(middle_rgb_time),
        }
        selected_times: dict[str, Any] = {
            "camera": {
                "topic": cfg["streams"]["camera"]["topic"],
                "frame_index": middle_rgb_index,
                "log_time_ns": middle_rgb_time,
                "log_time_utc": ns_to_utc(middle_rgb_time),
                "relative_to_rgb_s": 0.0,
            }
        }
        for name in target_names:
            values = log_times[name]
            pos = bisect.bisect_right(values, middle_rgb_time) - 1
            unavailable = int(pos < 0)
            sensor_time = values[pos] if pos >= 0 else None
            lag = middle_rgb_time - sensor_time if sensor_time is not None else None
            status = "PASS" if lag is not None and lag <= threshold_ns else "FAIL"
            sync[name] = {
                "topic": cfg["streams"][name]["topic"],
                "compared_rgb_frames": int(lag is not None),
                "unavailable_at_rgb_frames": unavailable,
                "p99_playback_lag_ms": (lag or 0) / 1e6,
                "max_playback_lag_ms": (lag or 0) / 1e6,
                "threshold_ms": cfg["sync_threshold_ms"],
                "status": status,
            }
            if sensor_time is not None:
                signed_delta_ns = sensor_time - middle_rgb_time
                sync[name].update(
                    selected_rgb_log_time_ns=middle_rgb_time,
                    selected_rgb_log_time_utc=ns_to_utc(middle_rgb_time),
                    selected_sensor_log_time_ns=sensor_time,
                    selected_sensor_log_time_utc=ns_to_utc(sensor_time),
                    signed_delta_ns=signed_delta_ns,
                    signed_delta_ms=signed_delta_ns / 1e6,
                )
                selected_times[name] = {
                    "topic": cfg["streams"][name]["topic"],
                    "frame_index": pos + 1,
                    "log_time_ns": sensor_time,
                    "log_time_utc": ns_to_utc(sensor_time),
                    "relative_to_rgb_s": signed_delta_ns / 1e9,
                }
            if write_details:
                details.append({
                    "RGB帧号": middle_rgb_index,
                    "RGB播放时间(ns)": middle_rgb_time,
                    "数据流": CSV_NAMES.get(name, name),
                    "传感器帧号": pos + 1 if pos >= 0 else "",
                    "传感器播放时间(ns)": sensor_time or "",
                    "播放差(ns)": lag if lag is not None else "",
                    "播放差(ms)": f"{lag / 1e6:.6f}" if lag is not None else "",
                    "结果": status,
                })
        report["selected_playback_times"] = selected_times
        report["sync_to_rgb_playback"] = sync

        failed_rates = [name for name, item in streams.items() if item["status"] != "PASS"]
        failed_sync = [name for name, item in sync.items() if item["status"] != "PASS"]
        if failed_rates:
            report["issues"].append("帧率失败: " + ", ".join(failed_rates))
        if failed_sync:
            report["issues"].append("播放时间同步失败: " + ", ".join(failed_sync))
        report["status"] = "PASS" if not report["issues"] else "FAIL"

        if write_details:
            detail_path = output / f"{path.stem}_FG_frame_details.csv"
            with detail_path.open("w", newline="", encoding="utf-8-sig") as detail_file:
                fields = [
                    "RGB帧号", "RGB播放时间(ns)", "数据流", "传感器帧号",
                    "传感器播放时间(ns)", "播放差(ns)", "播放差(ms)", "结果",
                ]
                writer = csv.DictWriter(detail_file, fieldnames=fields)
                writer.writeheader()
                writer.writerows(details)
            report["frame_details_csv"] = str(detail_path.resolve())
    except Exception as exc:
        report["issues"].append(f"{type(exc).__name__}: {exc}")
    report["elapsed_s"] = round(time.time() - started, 3)
    return report


def print_terminal_summary(report: dict[str, Any], cfg: dict[str, Any]) -> None:
    """输出一个文件的中文终端摘要。"""
    print(f"\n文件: {Path(report['file']).name}")
    print(
        f"测试结果: {report.get('status', 'FAIL')}  "
        f"问题: {'; '.join(report.get('issues', [])) or '-'}"
    )
    if not report.get("container_valid"):
        print("无法输出播放时间摘要：MCAP容器无效")
        return
    for name in cfg["streams"]:
        item = report.get("selected_playback_times", {}).get(name)
        stream = report.get("streams", {}).get(name, {})
        hz_text = f"{stream.get('actual_hz', 0):.3f}Hz"
        if not item:
            print(
                f"{TERMINAL_NAMES.get(name, name):<6}  {hz_text:<12}  "
                f"{cfg['streams'][name]['topic']}  无数据"
            )
            continue
        relative = item.get("relative_to_rgb_s")
        relative_text = f"{relative:+.6f}s" if relative is not None else "N/A"
        print(
            f"{TERMINAL_NAMES.get(name, name):<6}  "
            f"{hz_text:<12}  "
            f"{item['topic']:<30}  "
            f"{item['log_time_utc']}  "
            f"差值={relative_text}"
        )


def write_summary_csv(path: Path, reports: list[dict[str, Any]], stream_names: list[str]) -> None:
    """写入兼容旧流程的汇总 CSV；Windows 主流程最终只保留 TXT。"""
    fields = ["文件", "结果", "MCAP时长(s)", "问题"]
    for name in stream_names:
        label = CSV_NAMES.get(name, name)
        fields += [f"{label}帧率(Hz)"]
    for name in [name for name in stream_names if name != "camera"]:
        label = CSV_NAMES.get(name, name)
        fields += [
            f"{label}RGB播放时间(ns)", f"{label}RGB播放时间(UTC)",
            f"{label}选择播放时间(ns)", f"{label}选择播放时间(UTC)",
            f"{label}时间差(ms)",
        ]
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for report in reports:
            row: dict[str, Any] = {
                "文件": report["file"],
                "结果": "PASS" if report["status"] == "PASS" else "FAIL",
                "MCAP时长(s)": report.get("mcap_duration_s", ""),
                "问题": "; ".join(report.get("issues", [])),
            }
            for name, item in report.get("streams", {}).items():
                label = CSV_NAMES.get(name, name)
                row[f"{label}帧率(Hz)"] = round(item["actual_hz"], 3)
            for name, item in report.get("sync_to_rgb_playback", {}).items():
                label = CSV_NAMES.get(name, name)
                row[f"{label}RGB播放时间(ns)"] = item.get("selected_rgb_log_time_ns", "")
                row[f"{label}RGB播放时间(UTC)"] = item.get("selected_rgb_log_time_utc", "")
                row[f"{label}选择播放时间(ns)"] = item.get("selected_sensor_log_time_ns", "")
                row[f"{label}选择播放时间(UTC)"] = item.get("selected_sensor_log_time_utc", "")
                row[f"{label}时间差(ms)"] = round(item.get("signed_delta_ms", 0), 6)
            writer.writerow(row)


def main() -> int:
    """解析参数、抽样 MCAP 并生成远程分析结果。"""
    parser = argparse.ArgumentParser(description="按 MCAP 播放时间检查 MCAP")
    parser.add_argument("input", type=Path, help="MCAP 文件或目录")
    parser.add_argument("-o", "--output", type=Path, default=Path("reports_FG"))
    parser.add_argument("-c", "--config", type=Path)
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-unstable", action="store_true")
    parser.add_argument("--write-frame-details", action="store_true", help="导出每个RGB时刻的播放时间配对")
    parser.add_argument("--sample-size", type=int, help="从符合条件的文件中随机抽取指定数量")
    parser.add_argument("--random-seed", help="抽样日期；默认使用当天日期")
    args = parser.parse_args()
    cfg = load_config(args.config)
    candidates = (
        [args.input]
        if args.input.is_file()
        else sorted(args.input.rglob("*.mcap") if args.recursive else args.input.glob("*.mcap"))
    )
    now = time.time()
    files = [
        path for path in candidates
        if path.stat().st_size > 0
        and (args.include_unstable or now - path.stat().st_mtime >= cfg["stable_seconds"])
    ]
    skipped = len(candidates) - len(files)
    eligible_count = len(files)
    if args.sample_size is not None:
        if args.sample_size <= 0:
            parser.error("--sample-size 必须大于0")
        seed = args.random_seed or datetime.now().strftime("%Y%m%d")
        files = sorted(random.Random(seed).sample(files, min(args.sample_size, len(files))))
        print(f"随机抽样: {len(files)}/{eligible_count}，日期={seed}")
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, path in enumerate(files, 1):
        print(f"[{index}/{len(files)}] {path}", flush=True)
        report = inspect_file(path, cfg, args.write_frame_details, args.output)
        reports.append(report)
        print_terminal_summary(report, cfg)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "method": "playback time / MCAP log_time",
        "files_found": len(candidates),
        "files_checked": len(files),
        "files_skipped_as_writing": skipped,
        "eligible_files": eligible_count,
        "pass": sum(item["status"] == "PASS" for item in reports),
        "fail": sum(item["status"] != "PASS" for item in reports),
        "results": reports,
    }
    json_path = args.output / f"mcap_check_FG_{stamp}.json"
    csv_path = args.output / f"mcap_check_FG_{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary_csv(csv_path, reports, list(cfg["streams"]))
    print(f"完成: PASS={payload['pass']} FAIL={payload['fail']} 跳过写入中={payload['files_skipped_as_writing']}")
    print(f"JSON: {json_path.resolve()}\nCSV:  {csv_path.resolve()}")
    return 1 if payload["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
