#!/usr/bin/env python3
"""
The actual demo: given skew_fingerprints.csv spanning a long enough
capture to include real MAC-rotation events, look for a MAC that
vanished and a MAC that appeared shortly after with a closely-matching
clock-skew fingerprint - a probable re-identification of the same
physical device across its privacy-motivated MAC randomization.

This is exactly the failure mode BLE MAC randomization is supposed to
prevent (Bluetooth Core Spec Vol 6 Part B 4.4.2's whole point is
untraceability) - if it works reliably, it's evidence that timing-based
fingerprinting is a real side channel privacy engineers should account
for, not just theoretical.

Scoring, not a hard yes/no: candidates are ranked by how many standard
errors apart their fitted intervals are (accounts for fit uncertainty -
a device with only 200 packets has a noisier estimate than one with
5000), combined with a time-gap plausibility factor and RSSI similarity
(same physical device shouldn't jump RSSI much across a rotation, unless
it moved).

Usage:
    python reid_matcher.py ../results/skew_fingerprints.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MAX_GAP_S = 40 * 60      # generous upper bound on MAC rotation interval
MIN_GAP_S = 5             # must have actually vanished first
MAX_RSSI_DELTA = 15       # dB - same physical device shouldn't jump much
INTERVAL_TOLERANCE_MS = 0.15  # absolute fallback tolerance


def main():
    if len(sys.argv) < 2:
        print("usage: python reid_matcher.py <skew_fingerprints.csv>")
        sys.exit(1)

    df = pd.read_csv(sys.argv[1]).sort_values("first_seen_ms").reset_index(drop=True)

    candidates = []
    for i, vanished in df.iterrows():
        for j, appeared in df.iterrows():
            if i == j:
                continue
            gap_s = (appeared["first_seen_ms"] - vanished["last_seen_ms"]) / 1000.0
            if not (MIN_GAP_S <= gap_s <= MAX_GAP_S):
                continue

            interval_diff = abs(vanished["fitted_interval_ms"] - appeared["fitted_interval_ms"])
            # Combine both devices' fit uncertainty into an expected noise floor.
            combined_rmse = np.sqrt(vanished["fit_rmse_ms"] ** 2 + appeared["fit_rmse_ms"] ** 2)
            noise_floor = max(combined_rmse / np.sqrt(min(vanished["n_packets"], appeared["n_packets"])),
                               0.01)
            sigma_distance = interval_diff / noise_floor

            if interval_diff > INTERVAL_TOLERANCE_MS and sigma_distance > 3:
                continue  # not a plausible match by either criterion

            rssi_delta = abs(vanished["mean_rssi"] - appeared["mean_rssi"])
            if rssi_delta > MAX_RSSI_DELTA:
                continue

            candidates.append({
                "vanished_mac": vanished["mac"],
                "appeared_mac": appeared["mac"],
                "gap_s": gap_s,
                "interval_diff_ms": interval_diff,
                "sigma_distance": sigma_distance,
                "rssi_delta": rssi_delta,
                "vanished_n_packets": int(vanished["n_packets"]),
                "appeared_n_packets": int(appeared["n_packets"]),
            })

    if not candidates:
        print("No candidate re-identification pairs found. Either no MAC "
              "rotation happened during this capture window, or the "
              "capture is too short for confident fingerprints on both "
              "sides of a rotation - this needs a capture spanning at "
              "least one real rotation event (~15-20 min of continuous "
              "presence before and after).")
        return

    out = pd.DataFrame(candidates).sort_values("sigma_distance")
    out_path = Path(sys.argv[1]).parent / "reid_candidates.csv"
    out.to_csv(out_path, index=False)

    print(f"{len(out)} candidate re-identification pair(s), best matches first:\n")
    print(out.to_string(index=False))
    print(f"\nWrote {out_path}")
    print("\nInterpretation: low sigma_distance (<3ish) + small interval_diff_ms "
          "+ small rssi_delta + a gap_s consistent with a real MAC rotation "
          "(iOS/Android typically ~15min) is the strongest evidence. Manually "
          "cross-check against dataset/ context (e.g. do you know a phone was "
          "present continuously across that gap?) before treating any single "
          "candidate as confirmed - this is a statistical signal, not a proof.")


if __name__ == "__main__":
    main()
