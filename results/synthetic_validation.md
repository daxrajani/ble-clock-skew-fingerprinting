# Synthetic validation results

Real output from `analysis/test_synthetic.py`, final version (after the
three real-data-driven bug fixes documented in
`real_capture_findings.md`), run 2026-08-11. Simulates three devices
with known ground-truth clock intervals and BLE-realistic random dither,
then runs the actual `estimate_skew.py` and `reid_matcher.py` code (not
a separate reimplementation) against the simulated capture.

Setup: Device A (true interval 152.500ms, 5000 packets), Device B (true
interval 152.100ms — 400us / ~2623ppm from A, a comfortably-detectable
mismatch chosen to demonstrate the matching mechanism clearly, not the
hardest possible case — see caveat below), Device C (true interval
100.000ms, 7500 packets, present the whole time — an unrelated
distractor that should never be matched to anything). Matching compares
1000-packet entry/exit windows, same as real captures.

```
=== Test 1: fitted intervals are NOT expected to equal true intervals ===
  Device A: true=152.5000ms fitted=157.4557ms (offset +4.9557ms) slope_se=41.20us
  Device B: true=152.1000ms fitted=157.0868ms (offset +4.9868ms) slope_se=41.25us
  Device C: true=100.0000ms fitted=105.0209ms (offset +5.0209ms) slope_se=33.71us

=== Test 1b: RELATIVE difference (A-B) recovers the TRUE relative difference within noise floor ===
  true diff=400.00us fitted diff=368.87us error=31.13us (tolerance 288.7us) [OK]

=== Test 2: A<->B interval difference is resolvable above noise floor ===
  |fitted_A - fitted_B| = 368.87us (true diff = 400.00us)
  |fitted_A - fitted_C| = 52.4348ms (should be huge - different nominal intervals)

=== Test 3: reid_matcher ranks (A,B) as best match, ignores C ===
  (exit(A)=157.3574ms entry(B)=156.9980ms diff=359.36us)
1 candidate re-identification pair(s), best matches first:
     vanished_mac      appeared_mac  gap_s  interval_diff_ms  sigma_distance  rssi_delta
AA:AA:AA:AA:AA:AA BB:BB:BB:BB:BB:BB  900.0          0.359359        2.785538       0.948

  Top candidate is (A, B) as expected - PASS
  Device C correctly excluded from candidates - PASS

ALL TESTS PASSED
```

## What this actually shows

- **The ~5ms offset in every device's fitted value is expected, not an
  error.** `advDelay`'s mean (5ms) gets absorbed into the fitted slope
  identically for every device, so it cancels out in any device-to-device
  comparison — which is the only thing `reid_matcher` ever does. Absolute
  fitted values are not meaningful on their own.
- **`slope_se_ms` is now correctly calibrated, validated against theory.**
  At n=5000 packets with a known 2.887ms dither standard deviation, the
  computed standard error (41.20-41.25us) matches the theoretical
  prediction (`2887us / sqrt(5000) = 40.8us`) almost exactly. This
  required two failed attempts first (see `real_capture_findings.md`) -
  the first underestimated uncertainty (using cumulative-sum residuals,
  which grow like a random walk), the second overestimated precision
  wildly (applying textbook OLS slope-SE to those same serially-correlated
  residuals). The fix computes standard error from per-step residuals
  instead, which are much closer to independent.
- **`sigma_distance = 2.79` for the true match** - close to but under the
  `SIGMA_TOLERANCE = 3` cutoff. This is intentional: the 400us/2623ppm
  gap used here is a comfortably-detectable case, not an easy one -
  demonstrating the matcher correctly identifies a real match without
  requiring an unrealistically large signal.
- **Device C, at a completely different nominal interval, separated by
  over 52 milliseconds** - trivially distinguishable regardless of noise.

## Honest caveat carried over from the real capture

This test's 2623ppm gap was deliberately chosen to be comfortably above
the ~129us combined noise floor at a 1000-packet window. Smaller, equally
realistic crystal-to-crystal differences (commonly tens to low hundreds
of ppm) can sit at or below that floor and may not reliably separate at
this window size - which is exactly what showed up when matching real
ambient devices that happen to share popular standard advertising
intervals (see `real_capture_findings.md`'s discussion of the 130
candidates among 60 real devices). This test validates that the
mechanism is *correct*, not that every real-world rotation is easy to
catch.
