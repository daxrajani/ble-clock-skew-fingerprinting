# Real capture findings

From a 1-hour passive capture on the nRF52840 DK (2026-08-11), 133,210
packets, 60 distinct ambient BLE devices fingerprinted (`>= 200` packets
each). This file documents both the interesting real result and three
real bugs the process of testing against real hardware data found and
fixed - each is as much the point of this project as the headline result.

## The clean example: a real MAC rotation, caught in raw data

One pair stood out immediately by inspection, before any statistics:

```
121013,22:92:47:37:94:E7,-64
121123,22:92:47:37:94:E7,-65
...
121865,22:92:47:37:94:E7,-67
121886,15:6F:0A:A8:3C:88,-64   <- new MAC, same ~100-110ms cadence, same RSSI band
121994,15:6F:0A:A8:3C:88,-64
...
```

`22:92:47:37:94:E7`'s last packet is at `121865`ms; `15:6F:0A:A8:3C:88`'s
first packet is at `121886`ms - 21 milliseconds later, exactly one more
advertising interval at this device's own cadence. No gap, no overlap,
same RSSI band straight through the "seam." This reads as a single
physical radio's next transmission switching MAC address on schedule -
not two different devices that happen to be similar.

## Three real bugs, found by testing on real data (not just synthetic)

`analysis/test_synthetic.py` passed cleanly on synthetic ground-truth
data from the start. Running the same pipeline on this real capture
exposed three separate, real problems synthetic data hadn't (and
structurally couldn't) surface:

**1. `MIN_GAP_S` was 1000x too conservative.** Initially set to 5 seconds
on the (untested) assumption a device would be visibly absent before
reappearing under a new MAC. The real transition above had a gap of 21
*milliseconds* - a MAC switch happens instantly, mid-schedule. Fixed by
lowering `MIN_GAP_S` to a near-zero sanity floor.

**2. Whole-lifetime fitting lets errors accumulate over long spans.**
The initial design fit each device's fingerprint across its *entire*
observed presence. Over a short (~4 minute) window, `15:6F`'s fitted
interval was 104.649882ms; extended to its full ~10-minute presence in
the 1-hour capture, the same device's fit drifted to 104.757782ms - a
~108us shift from more data, not less, of the *same physical device*.
Missed-packet reconstruction error compounds over long spans. Fixed by
computing separate **entry** (first 1000 packets) and **exit** (last
1000 packets) fingerprints per device, and matching a vanished device's
*exit* fingerprint against an appeared device's *entry* fingerprint -
both close in time to the actual transition.

**3. The uncertainty formula was wrong twice, in opposite directions.**
- First version used `fit_rmse / sqrt(n)`, where `fit_rmse` came from
  residuals of a regression fit through a *cumulative sum*. Cumulative
  sums of noisy increments are random walks; their residuals grow with
  the span in a way unrelated to per-packet measurement noise. This
  **underestimated** uncertainty for long/noisy fits, letting nearly any
  pair "pass" - 249 candidates out of ~1,770 device pairs tested, an
  obviously broken result.
- Second attempt used the textbook OLS slope standard-error formula
  (`sqrt(SSE/(n-2))/sqrt(Sxx)`) applied to that same cumulative-sum fit.
  This is textbook-correct for i.i.d. residuals, but cumulative-sum
  residuals are *strongly serially correlated* (a high point at step i
  mechanically stays high at i+1, i+2, ...), violating that assumption.
  Result: slope SE came out as ~0.5 microseconds on synthetic data with
  known ~2.9ms per-packet dither - obviously **too small**, caught
  because `test_synthetic.py` failed after this change (it had been
  passing) rather than by inspection.
- Correct fix: compute the standard error from **per-step residuals**
  (`delta - fitted_interval * n_intervals_for_that_step`), which are much
  closer to independent, using standard sample-mean SE
  (`std(per-step residuals) / sqrt(n)`). Validated: on synthetic data
  with a known 2.887ms dither standard deviation, the resulting SE at
  n=5000 packets came out to 41.20-41.25us, matching the theoretical
  prediction (`2887us/sqrt(5000) = 40.8us`) almost exactly.

## The honest result after all three fixes

With the corrected pipeline, **130 candidate pairs** remain among the 60
fingerprinted devices (1,770 unordered pairs tested - about 7.3%
flagged). This is not a residual bug - it reflects a genuine, honest
limitation: many ambient BLE devices use a small set of popular standard
advertising intervals (this capture shows clusters around ~95ms, ~100ms,
and others), so with a 1000-packet window's noise floor (tens to a few
hundred microseconds), *statistics alone* often can't uniquely
distinguish which of several similar nearby devices is a genuine
re-identification versus coincidental similarity.

Notably, the confirmed clean example above (`22:92` -> `15:6F`) now
ranks **~80th of 130** by pure statistical confidence
(`sigma_distance = 0.60`), not first - even though the raw-data evidence
(21ms gap, matching cadence, matching RSSI) is unambiguous. **This is the
real lesson**: `reid_matcher.py` is deliberately built to rank candidates
for manual review, not assert a single automated verdict, and this
result is exactly why - in a dense RF environment, statistical ranking
alone is a triage tool, not a decision. Corroborating signals (timing
plausibility, RSSI stability, and here, direct inspection of the raw
packet sequence) still matter.

## Honest caveats

- This is one capture session in one location - not a broad claim about
  false-positive rates across environments. A quieter environment (fewer
  ambient devices, less popular-interval clustering) would likely show
  far fewer spurious candidates.
- No controlled ground-truth experiment yet (e.g., deliberately toggling
  a *known* phone's Bluetooth off/on, as done in the companion
  `ble-rssi-ml-classifier` project, to get a labeled before/after pair).
  That would be the natural next step to measure true precision/recall
  rather than relying on one clean example found by inspection.
- Fingerprint precision is fundamentally window-size-limited
  (`noise_std/sqrt(n)`, same law as anywhere else in this project) -
  longer entry/exit windows would sharpen resolution at the cost of
  needing devices present longer before/after a rotation to fingerprint
  them at all.
