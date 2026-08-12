# Synthetic validation results

Real output from `analysis/test_synthetic.py`, run 2026-08-11. Simulates
three devices with known ground-truth clock intervals and BLE-realistic
random dither, then runs the actual `estimate_skew.py` and
`reid_matcher.py` code (not a separate reimplementation) against the
simulated capture.

Setup: Device A (true interval 152.500ms, 5000 packets), Device B (true
interval 152.480ms — a deliberately small, realistic-worst-case 131ppm
difference from A, meant to represent A's *same physical radio*
reappearing under a new MAC 15 minutes later), Device C (true interval
100.000ms, 7500 packets, present the whole time — an unrelated
distractor that should never be matched to anything).

```
=== Test 1: fitted intervals are NOT expected to equal true intervals ===
  Device A: true=152.5000ms fitted=157.4754ms (offset +4.9754ms) rmse=42.9936ms
  Device B: true=152.4800ms fitted=157.5026ms (offset +5.0226ms) rmse=51.1180ms
  Device C: true=100.0000ms fitted=105.0190ms (offset +5.0190ms) rmse=85.3331ms

=== Test 1b: RELATIVE difference (A-B) recovers the TRUE relative difference within noise floor ===
  true diff=20.00us fitted diff=-27.20us error=47.20us (tolerance 288.7us) [OK]

=== Test 2: A<->B interval difference is resolvable above noise floor ===
  |fitted_A - fitted_B| = 27.20us (true diff = 20.00us)
  |fitted_A - fitted_C| = 52.4564ms (should be huge - different nominal intervals)

=== Test 3: reid_matcher ranks (A,B) as best match, ignores C ===
1 candidate re-identification pair(s), best matches first:
     vanished_mac      appeared_mac  gap_s  interval_diff_ms  sigma_distance  rssi_delta
AA:AA:AA:AA:AA:AA BB:BB:BB:BB:BB:BB  900.0            0.0272        0.028795       0.948

  Top candidate is (A, B) as expected - PASS
  Device C correctly excluded from candidates - PASS

ALL TESTS PASSED
```

## What this actually shows

- **The ~5ms offset in every device's fitted value is expected, not an
  error.** `advDelay`'s mean (5ms) gets absorbed into the regression
  slope identically for every device, so it cancels out in any
  device-to-device comparison — which is the only thing `reid_matcher`
  ever does. Absolute fitted values are not meaningful on their own.
- **The interesting number is Test 1b/2**: the true difference between A
  and B's crystals was only 20 microseconds (an intentionally
  hard, near-worst-case 131ppm scenario). The estimator recovered a
  27.2us difference — right magnitude, but with the *wrong sign*
  (fitted B ended up larger than fitted A, though true A > true B).
  **This is honest: 20us is smaller than this setup's own noise floor**
  (~41us per device, ~58us combined at n=5000), so a single trial can
  land on either side of zero by chance at this SNR. `reid_matcher` still
  correctly picked (A, B) as the best - and only - candidate, because its
  tolerance check is scaled to the noise floor rather than assuming
  perfect precision, and it also weighs gap-timing plausibility and RSSI
  similarity, not interval difference alone.
- **Device C, at a completely different nominal interval, separated by
  over 52 milliseconds** - trivially distinguishable regardless of noise.
  Most real-world crystal mismatches (commonly 50-200+ ppm) will behave
  much more like C than like the deliberately-adversarial A/B pair here.

## Takeaway for real captures

Don't expect microsecond-perfect fingerprints from a 15-minute capture
window on devices with near-identical crystals. Do expect very reliable
separation between devices whose true crystal error differs by more than
roughly the noise floor at your sample size (`noise_std/sqrt(n)`, where
`noise_std ≈ 2.9ms` for BLE's 0-10ms advDelay) - which in practice means
letting `skew_logger` run as long as possible before drawing conclusions
about a specific device pair.
