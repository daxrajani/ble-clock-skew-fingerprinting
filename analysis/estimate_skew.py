#!/usr/bin/env python3
"""
Estimates each BLE device's clock-skew fingerprint from its
inter-packet-interval sequence in a capture CSV.

Method (the RF analog of classic TCP/WiFi clock-skew fingerprinting -
see e.g. Kohno et al. 2005 "Remote physical device fingerprinting",
Jana & Kasera 2008 for 802.11 clock skew):

1. For each MAC, take its sorted advertisement timestamps.
2. BLE's mandatory advDelay (Core Spec Vol 6 Part B 4.4.1) adds a random
   0-10ms dither to every advertising interval, swamping any single
   inter-packet gap - so we don't trust individual deltas. Instead we
   estimate the device's *nominal* interval as min(deltas) (dither is
   non-negative, so the minimum observed gap is close to the true base
   interval - the more packets, the closer), then reconstruct how many
   nominal intervals elapsed between each pair of consecutive packets
   (accounting for occasionally-missed packets), and fit a straight line
   of cumulative-elapsed-time vs cumulative-interval-count across the
   *entire* capture span.
3. The fitted slope is the device's true mean advertising interval as
   measured by our receiver's clock, to sub-millisecond precision if
   enough packets were captured - this is the fingerprint. Two physical
   radios both "nominally" configured to the same interval (e.g. iOS's
   common ~152.5ms) will still show slightly different fitted intervals
   because no two crystals run at exactly the same real frequency.

Needs a few hundred packets per device for a fingerprint stable enough
to be useful - ambient devices only briefly in range won't have enough
data and are dropped (see MIN_PACKETS).

Usage:
    python estimate_skew.py ../captures/session1.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MIN_PACKETS = 200  # below this, the fit is too noisy to trust


def estimate_device_skew(timestamps_ms):
    """timestamps_ms: sorted 1D array of int64 ms. Returns dict or None."""
    if len(timestamps_ms) < MIN_PACKETS:
        return None

    deltas = np.diff(timestamps_ms).astype(np.float64)
    deltas = deltas[deltas > 0]  # duplicate/out-of-order guard
    if len(deltas) < MIN_PACKETS - 1:
        return None

    nominal_guess = float(np.min(deltas))
    if nominal_guess <= 0:
        return None

    n_intervals = np.round(deltas / nominal_guess).astype(np.int64)
    n_intervals[n_intervals < 1] = 1  # guard against rounding to 0

    cum_intervals = np.cumsum(n_intervals).astype(np.float64)
    cum_time = np.cumsum(deltas)

    # Fit cum_time = slope * cum_intervals + intercept (least squares,
    # forced through reasonable range - no intercept-at-origin assumption).
    A = np.vstack([cum_intervals, np.ones_like(cum_intervals)]).T
    slope, intercept = np.linalg.lstsq(A, cum_time, rcond=None)[0]

    predicted = slope * cum_intervals + intercept
    residuals = cum_time - predicted
    fit_rmse_ms = float(np.sqrt(np.mean(residuals ** 2)))

    return {
        "n_packets": len(timestamps_ms),
        "nominal_interval_guess_ms": nominal_guess,
        "fitted_interval_ms": float(slope),
        "fit_rmse_ms": fit_rmse_ms,
        "first_seen_ms": int(timestamps_ms[0]),
        "last_seen_ms": int(timestamps_ms[-1]),
        "span_s": (timestamps_ms[-1] - timestamps_ms[0]) / 1000.0,
    }


def main():
    if len(sys.argv) < 2:
        print("usage: python estimate_skew.py <capture.csv> [more captures...]")
        sys.exit(1)

    dfs = [pd.read_csv(p) for p in sys.argv[1:]]
    df = pd.concat(dfs, ignore_index=True)
    df = df.sort_values(["mac", "uptime_ms"])

    results = []
    for mac, group in df.groupby("mac"):
        ts = group["uptime_ms"].values
        est = estimate_device_skew(ts)
        if est is None:
            continue
        est["mac"] = mac
        est["mean_rssi"] = float(group["rssi"].mean())
        results.append(est)

    if not results:
        print(f"No device had >= {MIN_PACKETS} packets - capture longer, or "
              "lower MIN_PACKETS if you're just testing the pipeline.")
        return

    out = pd.DataFrame(results).sort_values("fitted_interval_ms")
    out = out[["mac", "n_packets", "span_s", "nominal_interval_guess_ms",
               "fitted_interval_ms", "fit_rmse_ms", "mean_rssi",
               "first_seen_ms", "last_seen_ms"]]

    out_path = Path(sys.argv[1]).parent.parent / "results" / "skew_fingerprints.csv"
    out_path.parent.mkdir(exist_ok=True)
    out.to_csv(out_path, index=False)

    print(f"{len(out)} devices with >= {MIN_PACKETS} packets fingerprinted.")
    print(out.to_string(index=False))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
