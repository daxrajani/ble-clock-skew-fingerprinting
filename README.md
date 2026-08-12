# ble-clock-skew-fingerprinting

Can you re-identify a BLE device after its MAC address rotates, using
nothing but the timing statistics of its advertisements? This project
tests that, on real hardware, with an nRF52840 DK as a passive listener
— no other hardware, no physical interaction required once it's running.

## The problem

iOS and Android randomize a device's BLE MAC address every ~15 minutes
specifically so it can't be tracked long-term by passive observers
(Bluetooth Core Spec Vol 6 Part B, 4.4.2 — the "resolvable/non-resolvable
private address" mechanism). This project asks whether that protection
actually holds up against a side channel the spec doesn't address at all:
**every radio's crystal oscillator runs at a very slightly different real
frequency than its nominal spec** (a few to a few hundred ppm, varying
chip-to-chip), so a device's advertisements drift from their nominal
interval in a way that's a near-constant fingerprint of that physical
radio — independent of its MAC address. This is the RF analog of a
well-established technique (Kohno et al. 2005, "Remote Physical Device
Fingerprinting"; also applied to 802.11 by Jana & Kasera 2008) applied
here to BLE specifically.

If a MAC vanishes and a new MAC appears nearby shortly after with a
matching clock-skew fingerprint, that's evidence they're the same
physical device — the privacy protection MAC randomization is supposed
to provide didn't survive contact with a timing side channel.

## How it works

1. **`firmware/skew_logger`** — Zephyr BLE observer firmware on the
   nRF52840 DK. Logs every advertisement heard, from every device, with
   millisecond timestamps: `SKEW,<uptime_ms>,<mac>,<rssi>`. No filtering,
   no target MAC — passive and unattended, meant to run for hours.
2. **`analysis/log_skew_capture.py`** — captures that UART stream to a
   CSV on the host.
3. **`analysis/estimate_skew.py`** — for each device with enough packets,
   fits its true mean advertising interval from the inter-packet-arrival
   sequence (see Methodology below) — this fitted value is the
   fingerprint.
4. **`analysis/reid_matcher.py`** — the actual demo: scans for a MAC that
   vanished and a MAC that appeared shortly after with a closely-matching
   fingerprint, ranks candidates by statistical confidence, and flags
   probable re-identifications.

## Methodology

BLE's mandatory `advDelay` (Core Spec Vol 6 Part B, 4.4.1) adds a random
0–10ms dither to *every* advertising interval specifically to reduce
collisions — which also means no single inter-packet gap tells you
anything about clock skew. The fingerprint only emerges from many packets:

- Estimate the device's nominal interval as `min(deltas)` (dither is
  non-negative, so the smallest observed gap approaches the true base
  interval as sample count grows).
- Reconstruct how many nominal intervals elapsed between each pair of
  consecutive packets (usually 1; more if packets were missed).
- Fit a straight line of cumulative elapsed time vs. cumulative interval
  count across the whole capture. The slope is the fingerprint.

**Important, tested honestly**: because `advDelay`'s mean (~5ms) is
non-zero, the fitted slope carries a constant systematic offset from the
device's true nominal interval — this doesn't matter, since matching only
ever compares two fitted values *against each other*, and that offset is
shared. What does matter is random noise: since dither has standard
deviation ~2.9ms, any single device's fingerprint precision improves as
`noise_std / sqrt(n)` — i.e. you need real sample counts (hundreds to
low-thousands of packets) for a usable fingerprint, and a small
crystal-to-crystal difference can be genuinely hard to resolve at low n.
`analysis/test_synthetic.py` validates the whole pipeline against
synthetic data with known ground truth (not just "it ran without
crashing") and documents exactly where this precision limit bites — see
that file and `results/synthetic_validation.md`.

## Status

- [x] Firmware written, builds clean (83KB flash / 22KB RAM on
      nrf52840dk/nrf52840), flashed to a real DK.
- [x] Analysis pipeline (capture → skew estimation → re-ID matching)
      implemented and validated against synthetic ground-truth data —
      see `results/synthetic_validation.md`.
- [x] A full 1-hour passive capture completed: 133,210 packets, 60
      ambient devices fingerprinted. One MAC rotation caught cleanly in
      raw data (21ms gap, matching cadence, matching RSSI straight
      through the transition) — the clearest real evidence the method
      works on real hardware.
- [x] Testing on this real capture (not just synthetic data) found and
      fixed three real bugs in the matching pipeline — an unrealistic
      minimum-gap assumption, error accumulation from whole-lifetime
      fitting, and a statistical uncertainty formula that was wrong in
      two different ways before landing on one validated against
      synthetic ground truth. Full writeup, including the honest result
      after all three fixes (130 candidates among 60 devices — a real
      limitation of pairwise statistical matching in a dense RF
      environment, not a residual bug): `results/real_capture_findings.md`.
- [ ] A controlled experiment with a *known* device (deliberately
      toggling a specific phone's Bluetooth off/on, as done in the
      companion `ble-rssi-ml-classifier` project) would give a labeled
      ground-truth pair to measure true precision/recall, rather than
      relying on one clean example found by inspection.

## Running it yourself

```bash
cd firmware/skew_logger
west build -b nrf52840dk/nrf52840 . -d build
west flash -d build

cd ../../analysis
pip install -r requirements.txt
python log_skew_capture.py --port COM3 --out ../captures/session1.csv
# let it run for at least 30-60 minutes to span a real MAC rotation, Ctrl+C to stop

python estimate_skew.py ../captures/session1.csv
python reid_matcher.py ../results/skew_fingerprints.csv
```

## Validate the method first

```bash
cd analysis
python test_synthetic.py
```

## Limitations / honest caveats

- This only works against devices actively advertising (broadcast beacon
  mode, or connectable devices between connections) — it says nothing
  about connection-mode BLE traffic.
- Ambient temperature affects crystal frequency, so a fingerprint
  captured in one thermal environment may drift somewhat if conditions
  change substantially between the "vanished" and "appeared" windows.
- This is a statistical signal, not cryptographic proof — `reid_matcher`
  ranks candidates by confidence, it doesn't assert certainty.
- Devices with only a few dozen packets (most ambient devices briefly in
  range) don't have enough data for a usable fingerprint and are
  correctly dropped (`MIN_PACKETS` in `estimate_skew.py`).

## Why this project

Built as a companion to [`ble-rssi-ml-classifier`](https://github.com/daxrajani/ble-rssi-ml-classifier)
— same nRF52840 DK, same Zephyr scanning infrastructure, but a different
question: instead of "how far is this device," this asks "is BLE's own
privacy mechanism actually sufficient," which is a security/RF research
angle rather than a straightforward classification task.

## License

MIT
