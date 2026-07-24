#!/usr/bin/env python3
"""Extract timestamped rkbox log lines for a UTC problem window."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", type=Path, default=Path("/rkbox/log"))
    parser.add_argument("--utc-time", required=True)
    parser.add_argument("--window-seconds", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    utc = datetime.fromisoformat(args.utc_time.replace("Z", "+00:00"))
    local = utc.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    start = local - timedelta(seconds=args.window_seconds)
    end = local + timedelta(seconds=args.window_seconds)
    matched = 0
    with args.output.open("w", encoding="utf-8", errors="replace") as output:
        output.write(f"问题时间UTC: {utc.isoformat()}\n")
        output.write(f"盒子时间UTC+8: {local.isoformat(sep=' ')}\n")
        output.write(f"提取范围: {start} ~ {end}\n\n")
        for path in sorted(args.log_dir.glob("main*.log")):
            wrote_header = False
            with path.open("r", encoding="utf-8", errors="replace") as source:
                for line in source:
                    if len(line) < 21 or line[0] != "[":
                        continue
                    try:
                        value = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    if start <= value <= end:
                        if not wrote_header:
                            output.write(f"===== {path} =====\n")
                            wrote_header = True
                        output.write(line)
                        matched += 1
        if matched == 0:
            output.write("指定时间范围内未找到日志。\n")
    print(f"提取日志行数: {matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
