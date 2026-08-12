#!/usr/bin/env python3
"""
Passive capture logger for skew_logger firmware. No labeling, no
filtering - appends every SKEW,... line seen to a CSV. Meant to be left
running unattended for hours; skew estimation needs hundreds of packets
per device for a reliable fit, so the longer the better.

Usage:
    python log_skew_capture.py --port COM3 --out ../captures/session1.csv
    # Ctrl+C to stop, or --duration <seconds> to auto-stop
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path

import serial

LINE_RE = re.compile(r"^SKEW,(-?\d+),([0-9A-Fa-f:]{17}(?: \([a-z]+\))?),(-?\d+)\s*$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", default=str(Path(__file__).parent.parent / "captures" / "capture.csv"))
    ap.add_argument("--duration", type=float, default=None, help="Auto-stop after N seconds (default: run until Ctrl+C)")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not out_path.exists() or out_path.stat().st_size == 0

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"Could not open {args.port}: {e}", file=sys.stderr)
        sys.exit(1)

    time.sleep(0.2)
    ser.reset_input_buffer()

    with out_path.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["uptime_ms", "mac", "rssi"])

        print(f"Logging to {out_path}. Press Ctrl+C to stop." if args.duration is None
              else f"Logging to {out_path} for {args.duration}s.")

        deadline = None if args.duration is None else time.time() + args.duration
        n = 0
        try:
            while deadline is None or time.time() < deadline:
                raw = ser.readline().decode(errors="replace")
                if not raw:
                    continue
                m = LINE_RE.match(raw.strip())
                if not m:
                    continue
                uptime_ms, mac_raw, rssi = m.groups()
                mac = mac_raw.split(" ")[0].upper()
                writer.writerow([uptime_ms, mac, rssi])
                n += 1
                if n % 500 == 0:
                    f.flush()
                    print(f"  {n} packets logged")
        except KeyboardInterrupt:
            pass
        f.flush()
        print(f"Done. {n} packets logged this session.")

    ser.close()


if __name__ == "__main__":
    main()
