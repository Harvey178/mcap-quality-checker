#!/usr/bin/env python3
"""Daily integrity, frame-rate, and time-sync checks for device MCAP files."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcap.reader import make_reader
from mcap_protobuf.decoder import DecoderFactory


DEFAULT_CONFIG = {
    "stable_seconds": 180,
    "rate_tolerance_percent": 5.0,
    "gap_factor": 2.5,
    "sync_threshold_ms": 1000.0,
    "coverage_threshold_ms": 100.0,
    "streams": {
        "camera": {"topic": "/head/rgb/compressed", "expected_hz": 30.0,
                   "timestamp_path": "timing.event_time_ns", "timestamp_unit": "ns"},
        "head_imu": {"topic": "/head/imu", "expected_hz": 100.0,
                     "timestamp_path": "device_timestamp_us", "timestamp_unit": "us"},
        "left_imu": {"topic": "/forearm/left/imu", "expected_hz": 100.0,
                     "timestamp_path": "capture_time_ns", "timestamp_unit": "ns"},
        "right_imu": {"topic": "/forearm/right/imu", "expected_hz": 100.0,
                      "timestamp_path": "capture_time_ns", "timestamp_unit": "ns"},
        "left_emg": {"topic": "/forearm/left/emg_batch", "expected_hz": 2000.0,
                     "timestamp_path": "samples.0.capture_time_ns", "timestamp_unit": "ns"},
        "right_emg": {"topic": "/forearm/right/emg_batch", "expected_hz": 2000.0,
                      "timestamp_path": "samples.0.capture_time_ns", "timestamp_unit": "ns"},
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


@dataclass
class StreamResult:
    topic: str
    expected_hz: float
    count: int = 0
    duration_s: float = 0.0
    stream_duration_s: float = 0.0
    actual_hz: float = 0.0
    rate_error_percent: float = 0.0
    median_interval_ms: float | None = None
    p99_interval_ms: float | None = None
    max_interval_ms: float | None = None
    gap_count: int = 0
    duplicate_or_reverse_count: int = 0
    timestamp_source: str = ""
    status: str = "FAIL"
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def percentile(values: list[int], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return float(ordered[index])


def timestamp_to_ns(value: Any) -> int | None:
    if value is None:
        return None
    if hasattr(value, "seconds") and hasattr(value, "nanos"):
        return int(value.seconds) * 1_000_000_000 + int(value.nanos)
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def protobuf_timestamp(obj: Any, message: Any, is_emg: bool) -> tuple[int, str]:
    if is_emg:
        samples = getattr(obj, "samples", ())
        if samples:
            ts = timestamp_to_ns(getattr(samples[0], "capture_time_ns", None))
            if ts:
                return ts, "samples[0].capture_time_ns"
    for attr in ("capture_time_ns", "timestamp"):
        ts = timestamp_to_ns(getattr(obj, attr, None))
        if ts:
            return ts, attr
    timing = getattr(obj, "timing", None)
    ts = timestamp_to_ns(getattr(timing, "event_time_ns", None))
    if ts:
        return ts, "timing.event_time_ns"
    return int(message.publish_time or message.log_time), "mcap.publish_time"


def extract_path(obj: Any, path: str) -> Any:
    value = obj
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif part.isdigit():
            value = value[int(part)]
        else:
            value = getattr(value, part, None)
        if value is None:
            break
    return value


def decode_timestamp(
    schema: Any, channel: Any, message: Any, decoder: DecoderFactory, spec: dict[str, Any]
) -> tuple[int, str]:
    if channel.message_encoding == "json":
        # Decimal preserves the full textual precision of large JSON timestamps.
        obj = json.loads(message.data, parse_float=Decimal)
    elif channel.message_encoding == "protobuf":
        decode = decoder.decoder_for(channel.message_encoding, schema)
        if decode is None:
            raise ValueError(f"no protobuf decoder for {channel.topic}")
        obj = decode(message.data)
    else:
        raise ValueError(f"{channel.topic} 不支持的消息编码: {channel.message_encoding}")
    path = spec["timestamp_path"]
    raw = extract_path(obj, path)
    if raw is None:
        raise ValueError(f"{channel.topic} 缺少指定时间字段 {path}")
    unit = spec.get("timestamp_unit", "ns")
    factors = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}
    if unit not in factors:
        raise ValueError(f"{channel.topic} 不支持的时间单位 {unit}")
    ts = int((Decimal(str(raw)) * factors[unit]).to_integral_value())
    if ts <= 0:
        raise ValueError(f"{channel.topic} 的 {path} 不是有效正时间戳")
    return ts, f"{path} ({unit}->ns)"


def nearest_deltas(reference: list[int], target: list[int]) -> list[int]:
    result: list[int] = []
    for value in reference:
        pos = bisect.bisect_left(target, value)
        candidates = []
        if pos < len(target):
            candidates.append(abs(target[pos] - value))
        if pos:
            candidates.append(abs(target[pos - 1] - value))
        if candidates:
            result.append(min(candidates))
    return result


def max_nearest_pair(reference: list[int], target: list[int]) -> tuple[int, int, int]:
    """Return RGB time, nearest sensor time, and signed delta for the worst pair."""
    worst: tuple[int, int, int] | None = None
    for value in reference:
        pos = bisect.bisect_left(target, value)
        candidates: list[int] = []
        if pos < len(target):
            candidates.append(target[pos])
        if pos:
            candidates.append(target[pos - 1])
        if not candidates:
            continue
        nearest = min(candidates, key=lambda item: abs(item - value))
        delta = nearest - value
        if worst is None or abs(delta) > abs(worst[2]):
            worst = (value, nearest, delta)
    if worst is None:
        raise ValueError("无法找到最近时间配对")
    return worst


def ns_to_utc(value: int) -> str:
    seconds, nanos = divmod(value, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{base:%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z"


def analyze_stream(
    name: str,
    spec: dict[str, Any],
    timestamps: list[int],
    source: str,
    cfg: dict[str, Any],
    mcap_duration_s: float,
) -> StreamResult:
    expected = float(spec["expected_hz"])
    result = StreamResult(topic=spec["topic"], expected_hz=expected, count=len(timestamps), timestamp_source=source)
    if len(timestamps) < 2:
        result.issues.append("消息少于 2 帧")
        return result
    diffs = [b - a for a, b in zip(timestamps, timestamps[1:])]
    positive = [d for d in diffs if d > 0]
    result.duplicate_or_reverse_count = len(diffs) - len(positive)
    result.duration_s = mcap_duration_s
    result.stream_duration_s = (max(timestamps) - min(timestamps)) / 1e9
    # Match Foxglove's Topics-panel calculation: message count divided by
    # the complete MCAP recording duration, not this stream's own span.
    result.actual_hz = len(timestamps) / mcap_duration_s if mcap_duration_s > 0 else 0.0
    result.rate_error_percent = abs(result.actual_hz - expected) / expected * 100
    result.median_interval_ms = (percentile(positive, 0.5) or 0) / 1e6
    result.p99_interval_ms = (percentile(positive, 0.99) or 0) / 1e6
    result.max_interval_ms = (max(positive) if positive else 0) / 1e6
    gap_ns = cfg["gap_factor"] * 1e9 / expected
    result.gap_count = sum(d > gap_ns for d in positive)
    if result.rate_error_percent > cfg["rate_tolerance_percent"]:
        result.issues.append(f"平均帧率偏差 {result.rate_error_percent:.2f}%")
    if result.gap_count:
        result.warnings.append(f"发现 {result.gap_count} 个大于 {cfg['gap_factor']} 倍周期的间隔")
    if result.duplicate_or_reverse_count:
        result.issues.append(f"发现 {result.duplicate_or_reverse_count} 个重复/倒序时间戳")
    result.status = "PASS" if not result.issues else "FAIL"
    return result


def inspect_file(path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    topics = {v["topic"]: k for k, v in cfg["streams"].items()}
    timestamps: dict[str, list[int]] = {name: [] for name in cfg["streams"]}
    sources: dict[str, str] = {}
    decoder = DecoderFactory()
    report: dict[str, Any] = {
        "file": str(path.resolve()), "size_bytes": path.stat().st_size,
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "container_valid": False, "status": "FAIL", "issues": [],
    }
    try:
        with path.open("rb") as stream:
            reader = make_reader(stream)
            summary = reader.get_summary()
            if summary is None or summary.statistics is None:
                raise ValueError("MCAP 缺少可读取的 Summary/Statistics（文件可能未正常关闭）")
            report["mcap_message_count"] = summary.statistics.message_count
            mcap_duration_s = (
                summary.statistics.message_end_time
                - summary.statistics.message_start_time
            ) / 1e9
            report["mcap_duration_s"] = mcap_duration_s
            for schema, channel, message in reader.iter_messages(topics=list(topics)):
                name = topics[channel.topic]
                ts, source = decode_timestamp(schema, channel, message, decoder, cfg["streams"][name])
                timestamps[name].append(ts)
                sources.setdefault(name, source)
        report["container_valid"] = True
        stream_results = {
            name: analyze_stream(
                name, spec, timestamps[name], sources.get(name, ""), cfg, mcap_duration_s
            )
            for name, spec in cfg["streams"].items()
        }
        report["streams"] = {name: asdict(value) for name, value in stream_results.items()}
        camera = timestamps["camera"]
        first_times: dict[str, Any] = {}
        rgb_first = camera[0] if camera else None
        for name, spec in cfg["streams"].items():
            values = timestamps[name]
            if values:
                value = values[0]
                first_times[name] = {
                    "topic": spec["topic"],
                    "timestamp_field": spec["timestamp_path"],
                    "timestamp_ns": value,
                    "timestamp_utc": ns_to_utc(value),
                    "relative_to_rgb_s": (
                        (value - rgb_first) / 1e9 if rgb_first is not None else None
                    ),
                }
        report["first_timestamps"] = first_times
        sync: dict[str, Any] = {}
        sync_targets = [name for name in cfg["streams"] if name != "camera"]
        for name in sync_targets:
            target = timestamps[name]
            item: dict[str, Any] = {"status": "FAIL"}
            if camera and target:
                deltas = nearest_deltas(camera, target)
                rgb_time, sensor_time, signed_delta_ns = max_nearest_pair(camera, target)
                p99_ms = (percentile(deltas, 0.99) or 0) / 1e6
                max_ms = (max(deltas) if deltas else 0) / 1e6
                start_ms = (target[0] - camera[0]) / 1e6
                end_ms = (target[-1] - camera[-1]) / 1e6
                item.update(p99_nearest_ms=p99_ms, max_nearest_ms=max_ms,
                            start_offset_ms=start_ms, end_offset_ms=end_ms,
                            selected_rgb_time_ns=rgb_time,
                            selected_rgb_time_utc=ns_to_utc(rgb_time),
                            selected_sensor_time_ns=sensor_time,
                            selected_sensor_time_utc=ns_to_utc(sensor_time),
                            signed_delta_ns=signed_delta_ns,
                            signed_delta_ms=signed_delta_ns / 1e6)
                item["status"] = "PASS" if max_ms <= cfg["sync_threshold_ms"] else "FAIL"
                if item["status"] == "FAIL":
                    item["issue"] = "最大最近帧时间差超限"
            else:
                item["issue"] = "摄像头或目标数据流为空"
            sync[name] = item
        report["sync_to_camera"] = sync
        failed_streams = [name for name, item in stream_results.items() if item.status != "PASS"]
        failed_sync = [name for name, item in sync.items() if item["status"] != "PASS"]
        if failed_streams:
            report["issues"].append("帧率/连续性失败: " + ", ".join(failed_streams))
        if failed_sync:
            report["issues"].append("时间同步失败: " + ", ".join(failed_sync))
        report["status"] = "PASS" if not report["issues"] else "FAIL"
    except Exception as exc:
        report["issues"].append(f"{type(exc).__name__}: {exc}")
    report["elapsed_s"] = round(time.time() - started, 3)
    return report


def print_terminal_time_summary(report: dict[str, Any]) -> None:
    print(f"\n文件: {Path(report['file']).name}")
    if not report.get("container_valid"):
        print(f"测试结果: FAIL  问题: {'; '.join(report.get('issues', [])) or 'MCAP容器无效'}")
        print("无法输出时间摘要：MCAP容器无效")
        return
    detailed_issues: list[str] = []
    for name, stream in report.get("streams", {}).items():
        label = TERMINAL_NAMES.get(name, name)
        detailed_issues.extend(f"{label}: {text}" for text in stream.get("issues", []))
        detailed_issues.extend(f"{label}: {text}" for text in stream.get("warnings", []))
    for name, sync in report.get("sync_to_camera", {}).items():
        if sync.get("issue"):
            detailed_issues.append(f"{TERMINAL_NAMES.get(name, name)}: {sync['issue']}")
    print(
        f"测试结果: {report.get('status', 'FAIL')}  "
        f"问题: {'; '.join(detailed_issues) if detailed_issues else '-'}"
    )
    for name in DEFAULT_CONFIG["streams"]:
        item = report.get("first_timestamps", {}).get(name)
        stream = report.get("streams", {}).get(name, {})
        hz_text = f"{stream.get('actual_hz', 0):.3f}Hz"
        if not item:
            print(
                f"{TERMINAL_NAMES.get(name, name):<6}  {hz_text:<12}  "
                f"{DEFAULT_CONFIG['streams'][name]['topic']}  无数据"
            )
            continue
        relative = item.get("relative_to_rgb_s")
        relative_text = (
            f"{relative:+.6f}s" if relative is not None else "N/A"
        )
        print(
            f"{TERMINAL_NAMES.get(name, name):<6}  "
            f"{hz_text:<12}  "
            f"{item['topic']:<30}  "
            f"{item['timestamp_field']:<28}  "
            f"{item['timestamp_utc']}  "
            f"相对视频 {relative_text}"
        )


def write_csv(path: Path, reports: list[dict[str, Any]]) -> None:
    fields = ["文件", "结果", "问题"]
    for name in DEFAULT_CONFIG["streams"]:
        label = CSV_NAMES.get(name, name)
        fields += [f"{label}帧率(Hz)"]
        if name == "camera":
            fields += [f"{label}帧数"]
    for name in [name for name in DEFAULT_CONFIG["streams"] if name != "camera"]:
        label = CSV_NAMES.get(name, name)
        fields += [
            f"{label}RGB时间(ns)", f"{label}RGB时间(UTC)",
            f"{label}选择时间(ns)", f"{label}选择时间(UTC)",
            f"{label}时间差(ms)",
        ]
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for report in reports:
            row: dict[str, Any] = {
                "文件": report.get("file", ""),
                "结果": report.get("status", "FAIL"),
                "问题": "; ".join(report.get("issues", [])),
            }
            for name, item in report.get("streams", {}).items():
                label = CSV_NAMES.get(name, name)
                row[f"{label}帧率(Hz)"] = round(item["actual_hz"], 3)
                if name == "camera":
                    row[f"{label}帧数"] = item["count"]
            for name, item in report.get("sync_to_camera", {}).items():
                label = CSV_NAMES.get(name, name)
                row[f"{label}RGB时间(ns)"] = item.get("selected_rgb_time_ns", "")
                row[f"{label}RGB时间(UTC)"] = item.get("selected_rgb_time_utc", "")
                row[f"{label}选择时间(ns)"] = item.get("selected_sensor_time_ns", "")
                row[f"{label}选择时间(UTC)"] = item.get("selected_sensor_time_utc", "")
                row[f"{label}时间差(ms)"] = round(item.get("signed_delta_ms", 0), 6)
            writer.writerow(row)


def load_config(path: Path | None) -> dict[str, Any]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="批量检查 MCAP 有效性、帧率和摄像头/IMU/EMG 时间同步")
    parser.add_argument("input", type=Path, help="MCAP 文件或数据目录")
    parser.add_argument("-o", "--output", type=Path, default=Path("reports"), help="报告目录")
    parser.add_argument("-c", "--config", type=Path, help="JSON 配置文件")
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-unstable", action="store_true", help="也检查可能仍在写入的文件（不推荐）")
    parser.add_argument("--sample-size", type=int, help="从符合条件的文件中随机抽取指定数量")
    parser.add_argument("--random-seed", help="随机抽样种子；默认使用当天日期")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.input.is_file():
        candidates = [args.input]
    else:
        candidates = sorted(args.input.rglob("*.mcap") if args.recursive else args.input.glob("*.mcap"))
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
        print(f"随机抽样: {len(files)}/{eligible_count}，种子={seed}")
    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    reports = []
    for index, path in enumerate(files, 1):
        print(f"[{index}/{len(files)}] {path}", flush=True)
        report = inspect_file(path, cfg)
        reports.append(report)
        print_terminal_time_summary(report)
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "input": str(args.input.resolve()), "files_found": len(candidates),
        "files_checked": len(files), "files_skipped_as_writing": skipped,
        "eligible_files": eligible_count,
        "pass": sum(r["status"] == "PASS" for r in reports),
        "fail": sum(r["status"] != "PASS" for r in reports), "results": reports,
    }
    json_path = args.output / f"mcap_check_{stamp}.json"
    csv_path = args.output / f"mcap_check_{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, reports)
    print(f"完成: PASS={payload['pass']} FAIL={payload['fail']} 跳过写入中={skipped}")
    print(f"JSON: {json_path.resolve()}\nCSV:  {csv_path.resolve()}")
    return 1 if payload["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
