#!/usr/bin/env python3
"""Check all zero-byte MCAP files and fully parse a reproducible random sample."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from mcap.reader import make_reader

from mcap_daily_check import load_config


def parse_entire_file(path: Path) -> dict[str, object]:
    started = time.time()
    result: dict[str, object] = {
        "file": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "status": "FAIL",
        "issue": "",
    }
    try:
        with path.open("rb") as source:
            reader = make_reader(source)
            summary = reader.get_summary()
            if summary is None or summary.statistics is None:
                raise ValueError("缺少MCAP Summary/Statistics，文件可能未正常关闭")
            parsed_count = sum(1 for _ in reader.iter_messages())
            expected_count = summary.statistics.message_count
            if parsed_count != expected_count:
                raise ValueError(f"消息数不一致: 解析={parsed_count}, Summary={expected_count}")
            result.update(
                status="PASS",
                parsed_messages=parsed_count,
                summary_messages=expected_count,
            )
    except Exception as exc:
        result["issue"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_s"] = round(time.time() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="MCAP零字节及随机抽样完整性检查")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("reports/integrity"))
    parser.add_argument("-c", "--config", type=Path)
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--random-seed", help="默认使用当天日期")
    parser.add_argument("--include-unstable", action="store_true")
    args = parser.parse_args()
    if args.sample_size <= 0:
        parser.error("--sample-size 必须大于0")

    cfg = load_config(args.config)
    candidates = [args.input] if args.input.is_file() else sorted(args.input.rglob("*.mcap"))
    zero_files = [path for path in candidates if path.stat().st_size == 0]
    now = time.time()
    eligible = [
        path for path in candidates
        if path.stat().st_size > 0
        and (args.include_unstable or now - path.stat().st_mtime >= cfg["stable_seconds"])
    ]
    seed = args.random_seed or datetime.now().strftime("%Y%m%d")
    sampled = sorted(random.Random(seed).sample(eligible, min(args.sample_size, len(eligible))))

    print(f"发现MCAP: {len(candidates)}，0KB文件: {len(zero_files)}")
    for path in zero_files:
        print(f"0KB文件: {path}")
    print(f"随机抽样: {len(sampled)}/{len(eligible)}，种子={seed}")
    results = []
    for index, path in enumerate(sampled, 1):
        print(f"[{index}/{len(sampled)}] 完整解析: {path}")
        item = parse_entire_file(path)
        results.append(item)
        print(f"结果: {item['status']}  问题: {item['issue'] or '-'}")

    failed_parse = [item for item in results if item["status"] != "PASS"]
    overall = "PASS" if not zero_files and not failed_parse and len(sampled) > 0 else "FAIL"
    issues = []
    if zero_files:
        issues.append(f"发现{len(zero_files)}个0KB文件")
    if failed_parse:
        issues.append(f"发现{len(failed_parse)}个损坏/无法解析文件")
    if not sampled:
        issues.append("没有可抽样的稳定非零MCAP文件")

    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "result": overall,
        "issues": issues,
        "files_found": len(candidates),
        "zero_byte_files": [str(path.resolve()) for path in zero_files],
        "eligible_files": len(eligible),
        "sample_size": len(sampled),
        "random_seed": seed,
        "sample_results": results,
    }
    json_path = args.output / f"mcap_integrity_{stamp}.json"
    csv_path = args.output / f"mcap_integrity_{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as output:
        fields = ["文件", "文件大小(B)", "解析消息数", "结果", "问题"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for path in zero_files:
            writer.writerow({"文件": str(path.resolve()), "文件大小(B)": 0,
                             "解析消息数": 0, "结果": "FAIL", "问题": "0KB文件"})
        for item in results:
            writer.writerow({
                "文件": item["file"], "文件大小(B)": item["size_bytes"],
                "解析消息数": item.get("parsed_messages", 0),
                "结果": item["status"], "问题": item["issue"],
            })
    print(f"完整性测试结果: {overall}  问题: {'; '.join(issues) or '-'}")
    print(f"JSON: {json_path.resolve()}\nCSV:  {csv_path.resolve()}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
