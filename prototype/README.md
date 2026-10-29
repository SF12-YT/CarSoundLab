# CarSoundLab Sample-Driven Prototype v4

This is an **offline preparation tool**, not an Android app update. It creates a reviewable sample bank from the five RB21 onboard recordings already in the CarSoundLab repository.

## What changed in v4

- **No SciPy dependency.** Uses NumPy, SoundFile and FFmpeg. FFmpeg resamples non-48 kHz sources to 48 kHz without intentional pitch shifting or time stretching.
- **Audio candidates are detected once over the recording.** Spectral-flux peaks are then matched to the uncertainty intervals between OpenF1 gear telemetry samples, instead of treating each coarse telemetry timestamp as an exact shift time.
- **Multi-gear telemetry jumps expand into candidate steps.** For example, 8→6 creates two *inferred* shift slots. Any intermediate gear labels are marked inferred, never represented as directly observed telemetry.
- **No fabricated clip for a missing candidate.** The review CSV records slots where a strong candidate could not be matched.
- **Neighbour-aware clip boundaries.** Shift one-shots use approximately 35 ms before and 105 ms after a detected transient, capped by adjacent selected transients so the next shift is less likely to leak into the clip. Short samples are allowed; the old 150 ms minimum no longer silently discards them.
- Emits an audio-transient candidate CSV and a detailed per-step review CSV for listening checks.

## Termux setup

From Termux:

```bash
pkg update
pkg install python python-numpy libsndfile ffmpeg
python -m pip install soundfile
```

Check that the imports and FFmpeg work:

```bash
python -c "import numpy, soundfile; print('NumPy and SoundFile OK')"
ffmpeg -version
```

## Run it

Extract the ZIP at the **root of your existing CarSoundLab repository**, so the files land under `prototype/`. Keep your Android source and original recordings unchanged.

From the repository root:

```bash
python prototype/prepare_sample_bank.py --repo-root . --out prototype/sample-bank-output
```

The script fetches historical OpenF1 car telemetry, so an internet connection is needed. To generate only the user-supplied first-downshift anchor clips without telemetry, add `--no-fetch`; that mode does not run the multi-shift detector.

The output directory is cleared and recreated by default each run. Do not use `--out` to point at your Android assets folder.

## Main outputs

- `sample-bank-output/sample_bank_audition_montage.wav` — all generated samples separated by short silences.
- `sample-bank-output/sample_index.csv` — clip paths, source time ranges and metadata.
- `sample-bank-output/sample_bank.json` — provenance, settings and limitations.
- `<circuit>/<circuit>_shift_event_review.csv` — one row per expected gear step, its matched candidate (if any), inferred gear labels, timing and clip bounds.
- `<circuit>/<circuit>_audio_transient_candidates.csv` — spectral-flux candidates the detector found in the complete recording.
- `<circuit>/<circuit>_car_data.csv` and `<circuit>/<circuit>_gear_changes.csv` — fetched telemetry and the coarse gear changes.

## Important limits

The recordings are full onboard mixes, not isolated engine recordings. Spectral-flux peaks can also be caused by tyres, road noise, wind, radio or other scene sounds. The algorithm is deliberately a **candidate detector**, not a claim that every matched peak is a genuine shift. In particular, every generated clip and inferred intermediate gear should be auditioned against the source recording before use in the app. The 48 kHz conversion, script compilation and synthetic audio tests can be tested here; the actual five RB21 files and their shift timing could not be independently auditioned in this package build.

The sample bank is not integrated into the Android engine and does not build an APK.
