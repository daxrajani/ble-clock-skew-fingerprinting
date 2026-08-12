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
   of cumulative-elapsed-time vs cumulative-interval-count.
3. The fitted slope is the device's true mean advertising interval as
   measured by our receiver's clock - this is the fingerprint. Two
   physical radios both "nominally" configured to the same interval
   (e.g. iOS's common ~152.5ms) will still show slightly different
   fitted intervals because no two crystals run at exactly the same real
   frequency.

**Windowed, not whole-lifetime** (found necessary by testing on a real
1-hour capture, not just synthetic data - see
results/real_capture_findings.md): fitting across a device's *entire*
observed lifetime lets missed-packet reconstruction errors accumulate
over long spans and drift the fitted value by tens to hundreds of
microseconds - enough to break matching. Instead each device gets an
"entry" fingerprint (its first WINDOW_PACKETS packets) and an "exit"
fingerprint (its last WINDOW_PACKETS packets), computed independently.
A MAC-rotation match should compare a vanished device's *exit*
fingerprint against a newly-appeared device's *entry* fingerprint -
both close in time to the actual transition, where drift hasn't had a
chance to accumulate.

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

MIN_PACKETS = 200   # below this, the fit is too noisy to trust
WINDOW_PACKETS = 1000  # entry/exit fingerprint window size


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
    n = len(cum_intervals)

    # Fitted interval = total elapsed time / total intervals elapsed - a
    # simple ratio estimator, equivalent to (and more transparent than) the
    # OLS slope of cum_time vs cum_intervals forced through the origin.
    total_intervals = float(cum_intervals[-1])
    slope = float(cum_time[-1]) / total_intervals

    # Standard error of that ratio, via the delta method on PER-STEP
    # residuals (deltas - slope*n_intervals), NOT on the cumulative sums'
    # residuals. This distinction matters: cum_time is a running total, so
    # residuals from a fit through the cumulative series are strongly
    # serially correlated (a high point at step i mechanically stays high
    # at i+1, i+2, ...), which violates the i.i.d. assumption standard OLS
    # slope-SE formulas depend on and made an earlier attempt at this
    # (textbook "sqrt(SSE/(n-2))/sqrt(Sxx)" on the cumulative fit) produce
    # an absurdly tiny, wrong answer (~0.5us) - caught by
    # analysis/test_synthetic.py failing after that change, see
    # results/real_capture_findings.md. Per-step residuals are much closer
    # to independent, so a standard sample-mean standard error applies:
    # SE = std(per-step residuals) / sqrt(n).
    step_residuals = deltas - slope * n_intervals
    if n > 1:
        step_std = float(np.std(step_residuals, ddof=1))
        slope_se_ms = step_std / np.sqrt(total_intervals)
    else:
        slope_se_ms = float("inf")

    fit_rmse_ms = float(np.sqrt(np.mean(step_residuals ** 2)))  # kept for reference/debugging

    return {
        "n_packets": n,
        "fitted_interval_ms": float(slope),
        "fit_rmse_ms": fit_rmse_ms,
        "slope_se_ms": float(slope_se_ms),
    }


def estimate_device_fingerprints(timestamps_ms):
    """Returns dict with overall/entry/exit fits, or None if too few packets."""
    overall = estimate_device_skew(timestamps_ms)
    if overall is None:
        return None

    n = len(timestamps_ms)
    entry_ts = timestamps_ms[:min(n, WINDOW_PACKETS)]
    exit_ts = timestamps_ms[-min(n, WINDOW_PACKETS):]

    entry = estimate_device_skew(entry_ts) or {}
    exit_ = estimate_device_skew(exit_ts) or {}

    result = {
        "n_packets": n,
        "overall_fitted_interval_ms": overall["fitted_interval_ms"],
        "overall_slope_se_ms": overall["slope_se_ms"],
        "entry_n_packets": entry.get("n_packets"),
        "entry_fitted_interval_ms": entry.get("fitted_interval_ms"),
        "entry_slope_se_ms": entry.get("slope_se_ms"),
        "exit_n_packets": exit_.get("n_packets"),
        "exit_fitted_interval_ms": exit_.get("fitted_interval_ms"),
        "exit_slope_se_ms": exit_.get("slope_se_ms"),
        "first_seen_ms": int(timestamps_ms[0]),
        "last_seen_ms": int(timestamps_ms[-1]),
        "span_s": (timestamps_ms[-1] - timestamps_ms[0]) / 1000.0,
    }
    return result


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
        est = estimate_device_fingerprints(ts)
        if est is None:
            continue
        est["mac"] = mac
        est["mean_rssi"] = float(group["rssi"].mean())
        results.append(est)

    if not results:
        print(f"No device had >= {MIN_PACKETS} packets - capture longer, or "
              "lower MIN_PACKETS if you're just testing the pipeline.")
        return

    out = pd.DataFrame(results).sort_values("overall_fitted_interval_ms")
    out = out[["mac", "n_packets", "span_s", "overall_fitted_interval_ms", "overall_slope_se_ms",
               "entry_n_packets", "entry_fitted_interval_ms", "entry_slope_se_ms",
               "exit_n_packets", "exit_fitted_interval_ms", "exit_slope_se_ms",
               "mean_rssi", "first_seen_ms", "last_seen_ms"]]

    out_path = Path(sys.argv[1]).parent.parent / "results" / "skew_fingerprints.csv"
    out_path.parent.mkdir(exist_ok=True)
    out.to_csv(out_path, index=False)

    print(f"{len(out)} devices with >= {MIN_PACKETS} packets fingerprinted "
          f"(entry/exit windows of up to {WINDOW_PACKETS} packets each).")
    print(out.to_string(index=False))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
