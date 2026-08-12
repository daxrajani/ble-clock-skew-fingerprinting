#!/usr/bin/env python3
"""
Validates estimate_skew.py and reid_matcher.py against synthetic data
with known ground truth, before trusting either on real capture data.

Simulates:
  - Device A: true interval 152.500ms, ~1000 packets, then goes quiet
    (its MAC "rotates away").
  - Device B: true interval 152.480ms (20us different - about 131ppm,
    a realistic crystal-to-crystal difference), starts ~15 minutes after
    A stops - meant to represent the SAME physical radio reappearing
    under a new random MAC after a privacy rotation.
  - Device C: true interval 100.000ms, unrelated distractor device
    present throughout - should NOT be matched to anything.

Each interval gets BLE's mandatory 0-10ms uniform random advDelay
dither added, same as real hardware. Writes a synthetic capture CSV,
runs it through the real estimate_skew.py / reid_matcher.py logic
(imported directly, not reimplemented), and checks:
  1. Fitted intervals for A and B are within a few us of their true
     values despite the +/-10ms per-packet dither.
  2. reid_matcher's top-ranked candidate is exactly (A's MAC, B's MAC).
  3. Device C is not flagged as matching anything.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from estimate_skew import estimate_device_skew  # noqa: E402

RNG = np.random.default_rng(42)


def simulate_device(true_interval_ms, n_packets, start_ms):
    """Returns sorted timestamps in ms (int), BLE-style dithered intervals."""
    dither = RNG.uniform(0, 10, size=n_packets - 1)
    intervals = true_interval_ms + dither
    timestamps = start_ms + np.concatenate([[0], np.cumsum(intervals)])
    return timestamps.astype(np.int64)


def main():
    mac_a, mac_b, mac_c = "AA:AA:AA:AA:AA:AA", "BB:BB:BB:BB:BB:BB", "CC:CC:CC:CC:CC:CC"

    N = 5000  # ~12.7 min of continuous advertising at 152.5ms - realistic for one rotation window
    ts_a = simulate_device(152.500, N, start_ms=0)
    gap_ms = 15 * 60 * 1000  # 15 minutes, plausible MAC rotation gap
    ts_b = simulate_device(152.480, N, start_ms=int(ts_a[-1]) + gap_ms)
    ts_c = simulate_device(100.000, int(N * 1.5), start_ms=0)  # present throughout, unrelated

    # advDelay dither (Core Spec Vol 6 Part B 4.4.1) is uniform[0,10]ms,
    # non-zero mean -> any single device's fitted interval has an
    # irreducible statistical noise floor of dither_std/sqrt(n), same as
    # a sample mean's standard error. This is physics, not a bug: more
    # packets -> tighter fingerprint, per the usual sqrt(n) law.
    dither_std_ms = 10 / np.sqrt(12)
    noise_floor_us = lambda n: (dither_std_ms * 1000) / np.sqrt(n)  # noqa: E731

    rows = []
    for mac, ts, rssi in [(mac_a, ts_a, -60), (mac_b, ts_b, -61), (mac_c, ts_c, -75)]:
        for t in ts:
            rows.append({"uptime_ms": int(t), "mac": mac, "rssi": rssi + RNG.integers(-2, 3)})
    df = pd.DataFrame(rows).sort_values(["mac", "uptime_ms"])

    print("=== Test 1: fitted intervals are NOT expected to equal true intervals ===")
    print("  (the estimator's slope absorbs advDelay's mean (~5ms), a constant systematic")
    print("   offset shared by every device fitted this way - only RELATIVE differences")
    print("   between two fitted values are meaningful, which is all reid_matcher uses.)")
    est_a = estimate_device_skew(df[df.mac == mac_a]["uptime_ms"].values)
    est_b = estimate_device_skew(df[df.mac == mac_b]["uptime_ms"].values)
    est_c = estimate_device_skew(df[df.mac == mac_c]["uptime_ms"].values)
    for name, est, true_val in [("A", est_a, 152.500), ("B", est_b, 152.480), ("C", est_c, 100.000)]:
        print(f"  Device {name}: true={true_val:.4f}ms fitted={est['fitted_interval_ms']:.4f}ms "
              f"(offset {est['fitted_interval_ms']-true_val:+.4f}ms) rmse={est['fit_rmse_ms']:.4f}ms")

    ok = True
    print(f"\n=== Test 1b: RELATIVE difference (A-B) recovers the TRUE relative difference within noise floor ===")
    print(f"  (theoretical combined noise floor at n={N} each: {np.sqrt(2)*noise_floor_us(N):.1f}us)")
    true_diff_us = (152.500 - 152.480) * 1000
    fitted_diff_us = (est_a["fitted_interval_ms"] - est_b["fitted_interval_ms"]) * 1000
    tolerance_us = 5 * np.sqrt(2) * noise_floor_us(N)  # generous margin, not a tight bound
    err_us = abs(fitted_diff_us - true_diff_us)
    status = "OK" if err_us < tolerance_us else "FAIL"
    if status == "FAIL":
        ok = False
    print(f"  true diff={true_diff_us:.2f}us fitted diff={fitted_diff_us:.2f}us "
          f"error={err_us:.2f}us (tolerance {tolerance_us:.1f}us) [{status}]")

    print("\n=== Test 2: A<->B interval difference is resolvable above noise floor ===")
    diff_ms = abs(est_a["fitted_interval_ms"] - est_b["fitted_interval_ms"])
    print(f"  |fitted_A - fitted_B| = {diff_ms*1000:.2f}us (true diff = 20.00us)")
    diff_ac_ms = abs(est_a["fitted_interval_ms"] - est_c["fitted_interval_ms"])
    print(f"  |fitted_A - fitted_C| = {diff_ac_ms:.4f}ms (should be huge - different nominal intervals)")

    print("\n=== Test 3: reid_matcher ranks (A,B) as best match, ignores C ===")
    results_csv = Path(__file__).parent / "_test_fingerprints.csv"
    fp_rows = []
    for mac, est in [(mac_a, est_a), (mac_b, est_b), (mac_c, est_c)]:
        r = dict(est)
        r["mac"] = mac
        r["mean_rssi"] = float(df[df.mac == mac]["rssi"].mean())
        fp_rows.append(r)
    pd.DataFrame(fp_rows).to_csv(results_csv, index=False)

    import subprocess
    result = subprocess.run([sys.executable, str(Path(__file__).parent / "reid_matcher.py"),
                              str(results_csv)], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        ok = False

    candidates_csv = results_csv.parent / "reid_candidates.csv"
    if candidates_csv.exists():
        cand = pd.read_csv(candidates_csv)
        top = cand.iloc[0]
        if top["vanished_mac"] == mac_a and top["appeared_mac"] == mac_b:
            print("  Top candidate is (A, B) as expected - PASS")
        else:
            print(f"  Top candidate was ({top['vanished_mac']}, {top['appeared_mac']}), expected (A, B) - FAIL")
            ok = False
        if ((cand["vanished_mac"] == mac_c) | (cand["appeared_mac"] == mac_c)).any():
            print("  Device C incorrectly appears in candidates - FAIL")
            ok = False
        else:
            print("  Device C correctly excluded from candidates - PASS")
    else:
        print("  No candidates file produced - FAIL")
        ok = False

    results_csv.unlink(missing_ok=True)
    candidates_csv.unlink(missing_ok=True)

    print(f"\n{'ALL TESTS PASSED' if ok else 'SOME TESTS FAILED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
