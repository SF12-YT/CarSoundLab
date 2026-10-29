#!/usr/bin/env python3
"""Prepare telemetry-aligned, source-preserving RB21 onboard audio clips.

Run from an extracted CarSoundLab repo overlay:
  python prototype/prepare_sample_bank.py --repo-root .

Needs Python 3.10+, NumPy, SoundFile, and internet access for OpenF1 telemetry.
No synthetic engine oscillators are generated; source clips stay unpitched.
"""
from __future__ import annotations
import argparse, csv, json, shutil, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import numpy as np
    import soundfile as sf
except ImportError as e:
    raise SystemExit(
        "Missing Python audio dependency. On Termux run:\n"
        "  pkg install python-numpy libsndfile ffmpeg\n"
        "  python -m pip install soundfile"
    ) from e

API = "https://api.openf1.org/v1/car_data"
FIELDS = ["date", "driver_number", "session_key", "n_gear", "rpm", "speed", "throttle", "brake", "drs"]
TARGET_SR = 48000
SHIFT_PRE_MS = 35
SHIFT_POST_MS = 105
MIN_SHIFT_SEPARATION_S = 0.060
MIN_TRANSIENT_SCORE = 1.12


def parse_utc(v: str) -> datetime:
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def fetch_json(url: str, timeout: float = 35.0) -> Any:
    req = Request(url, headers={"User-Agent": "CarSoundLab-SampleBank/0.4"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_lap_data(item: dict[str, Any], pad_before: float = 2.0, pad_after: float = 1.0) -> list[dict[str, Any]]:
    start = parse_utc(item["pole_lap_start_utc"])
    end = start + timedelta(seconds=float(item["pole_lap_duration_s"]))
    params = {
        "session_key": str(item["session_key"]),
        "driver_number": "1",
        "date>": (start - timedelta(seconds=pad_before)).isoformat(timespec="milliseconds"),
        "date<": (end + timedelta(seconds=pad_after)).isoformat(timespec="milliseconds"),
    }
    rows = fetch_json(API + "?" + urlencode(params))
    if not isinstance(rows, list): raise ValueError(f"Unexpected OpenF1 payload for {item['circuit']}: {rows!r}")
    rows.sort(key=lambda r: parse_utc(r["date"]))
    return rows


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return data, int(sr)
    except Exception as e:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(f"Could not decode {path.name} with SoundFile ({e}); install ffmpeg and retry") from e
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "decoded.wav"
            p = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(path), "-acodec", "pcm_f32le", str(tmp)], capture_output=True, text=True)
            if p.returncode: raise RuntimeError(f"ffmpeg could not decode {path}: {p.stderr[-500:]}")
            data, sr = sf.read(str(tmp), dtype="float32", always_2d=True)
            return data, int(sr)


def resample_audio(data: np.ndarray, source_sr: int, target_sr: int = TARGET_SR) -> np.ndarray:
    """Use FFmpeg's audio resampler; no SciPy build or time/pitch shifting is needed."""
    audio = np.asarray(data, dtype=np.float32)
    if int(source_sr) == int(target_sr):
        return audio
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            f"Cannot convert {source_sr} Hz to {target_sr} Hz because FFmpeg was not found. "
            "On Termux run: pkg install ffmpeg"
        )
    # FLOAT WAV intermediates preserve precision; FFmpeg changes only the sample rate.
    with tempfile.TemporaryDirectory(prefix="carsoundlab_resample_") as td:
        input_wav = Path(td) / "input_float.wav"
        output_wav = Path(td) / "output_float.wav"
        sf.write(str(input_wav), audio, int(source_sr), subtype="FLOAT", format="WAV")
        command = [
            ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(input_wav),
            "-ar", str(int(target_sr)), "-c:a", "pcm_f32le", str(output_wav),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 or not output_wav.exists():
            detail = (result.stderr or result.stdout or "unknown FFmpeg error")[-1000:]
            raise RuntimeError(f"FFmpeg resampling failed ({source_sr} -> {target_sr} Hz): {detail}")
        converted, actual_sr = sf.read(str(output_wav), dtype="float32", always_2d=True)
        if int(actual_sr) != int(target_sr):
            raise RuntimeError(f"FFmpeg output was {actual_sr} Hz; expected {target_sr} Hz")
        # FFmpeg may differ by a sample due to container rounding. Do not time-stretch to fix it.
        return np.asarray(converted, dtype=np.float32)


def refine_shift_time(data: np.ndarray, sr: int, predicted_s: float,
                      radius_s: float = 0.25) -> tuple[float, float]:
    """Refine a coarse telemetry timestamp using local spectral-flux peaks.

    Returns (candidate_time_s, relative_score). This is a heuristic, not ground truth;
    the predicted timestamp and adjustment are saved for review.
    """
    if len(data) < int(0.04 * sr):
        return float(predicted_s), 0.0
    mono = data.mean(axis=1) if data.ndim == 2 else data
    half = 0.010
    hop = max(1, round(0.005 * sr))
    radius = round(radius_s * sr)
    center = round(predicted_s * sr)
    lo = max(0, center - radius - round(half * sr))
    hi = min(len(mono), center + radius + round(half * sr))
    segment = mono[lo:hi]
    frame_n = max(32, round(0.020 * sr))
    if len(segment) < frame_n * 2:
        return float(predicted_s), 0.0
    starts = np.arange(0, len(segment) - frame_n + 1, hop, dtype=np.int64)
    frames = np.stack([segment[i:i + frame_n] for i in starts])
    window = np.hanning(frame_n).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frames * window[None, :], axis=1)).astype(np.float32)
    # Normalize each frame so broadband level changes don't dominate solely by loudness.
    spectrum /= (np.linalg.norm(spectrum, axis=1, keepdims=True) + 1e-8)
    positive_diff = np.maximum(0.0, spectrum[1:] - spectrum[:-1])
    flux = np.sqrt(np.mean(positive_diff * positive_diff, axis=1) + 1e-12)
    frame_times = (lo + starts[1:] + frame_n / 2) / sr
    in_range = np.abs(frame_times - predicted_s) <= radius_s
    if not np.any(in_range):
        return float(predicted_s), 0.0
    times = frame_times[in_range]
    candidates = flux[in_range]
    # Mildly prefer candidates nearer the telemetry time without ruling out the observed ~200 ms offset.
    weighted = candidates / (1.0 + (np.abs(times - predicted_s) / 0.16) ** 2)
    idx = int(np.argmax(weighted))
    peak = float(candidates[idx])
    reference = float(np.percentile(candidates, 75)) + 1e-8
    relative_score = float(np.clip(peak / reference, 0.0, 10.0))
    return float(times[idx]), round(relative_score, 3)


def fade_edges(y: np.ndarray, sr: int, ms: float = 8.0) -> np.ndarray:
    y = y.copy()
    n = min(max(0, round(sr * ms / 1000)), len(y) // 2)
    if n:
        f = np.linspace(0.0, 1.0, n, endpoint=False, dtype=np.float32)[:, None]
        y[:n] *= f
        y[-n:] *= f[::-1]
    return y


def slice_audio(data: np.ndarray, sr: int, start_s: float, end_s: float) -> np.ndarray:
    start = max(0, min(len(data), round(start_s * sr)))
    end = max(start, min(len(data), round(end_s * sr)))
    return data[start:end].copy()


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep headroom; do not normalize samples independently, as that changes relative dynamics.
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 0.999: data = data * (0.999 / peak)
    sf.write(str(path), data, sr, subtype="PCM_16")


def detect_audio_transients(data: np.ndarray, sr: int) -> list[dict[str, Any]]:
    """Find local broadband spectral onsets in the original recording.

    This produces *candidates*, not ground-truth shifts. Telemetry later constrains
    which candidates are eligible. Chunked FFT processing keeps RAM use reasonable
    on Android/Termux and needs only NumPy.
    """
    if data.ndim == 1:
        audio = data[:, None]
    else:
        audio = data
    frame_n = max(256, round(0.020 * sr))
    hop = max(1, round(0.005 * sr))
    if len(audio) < frame_n * 2:
        return []
    starts = np.arange(0, len(audio) - frame_n + 1, hop, dtype=np.int64)
    window = np.hanning(frame_n).astype(np.float32)
    flux = np.zeros(len(starts), dtype=np.float32)
    block_size = 384
    previous_spectrum: np.ndarray | None = None
    for base in range(0, len(starts), block_size):
        block_starts = starts[base:base + block_size]
        frames = np.stack([audio[int(st):int(st) + frame_n].T for st in block_starts])
        spectra = np.abs(np.fft.rfft(frames * window[None, None, :], axis=2)).astype(np.float32)
        spectra = spectra.mean(axis=1)
        spectra /= (np.linalg.norm(spectra, axis=1, keepdims=True) + 1e-8)
        if previous_spectrum is not None:
            first_diff = np.maximum(0.0, spectra[0] - previous_spectrum)
            flux[base] = float(np.sqrt(np.mean(first_diff * first_diff) + 1e-12))
        diffs = np.maximum(0.0, spectra[1:] - spectra[:-1])
        if len(diffs):
            flux[base + 1:base + 1 + len(diffs)] = np.sqrt(np.mean(diffs * diffs, axis=1) + 1e-12)
        previous_spectrum = spectra[-1]

    times = (starts + frame_n / 2) / float(sr)
    global_floor = float(np.percentile(flux, 55)) if len(flux) else 0.0
    local_peaks: list[dict[str, Any]] = []
    neighbor = max(2, round(0.025 * sr / hop))
    baseline_radius = max(neighbor * 3, round(0.25 * sr / hop))
    for i in range(1, len(flux) - 1):
        if flux[i] < global_floor or flux[i] < flux[i - 1] or flux[i] <= flux[i + 1]:
            continue
        if flux[i] < float(np.max(flux[max(0, i - neighbor):i + 1])):
            continue
        if flux[i] < float(np.max(flux[i:min(len(flux), i + neighbor + 1)])):
            continue
        lo, hi = max(0, i - baseline_radius), min(len(flux), i + baseline_radius + 1)
        local = flux[lo:hi]
        if len(local) > 5:
            local = np.concatenate((local[:max(0, i-lo-neighbor)], local[min(len(local), i-lo+neighbor+1):]))
        baseline = float(np.median(local)) + 1e-9
        score = float(flux[i]) / baseline
        if score >= MIN_TRANSIENT_SCORE:
            local_peaks.append({"time_s": float(times[i]), "score": round(min(score, 20.0), 3),
                                "flux": float(flux[i]), "local_baseline": baseline})

    # Non-maximum suppression so the several FFT frames around one transient do not
    # become multiple clips. Close real gear changes remain eligible (60 ms minimum).
    accepted: list[dict[str, Any]] = []
    for candidate in sorted(local_peaks, key=lambda c: c["score"], reverse=True):
        if all(abs(candidate["time_s"] - existing["time_s"]) >= MIN_SHIFT_SEPARATION_S for existing in accepted):
            accepted.append(candidate)
    return sorted(accepted, key=lambda c: c["time_s"])


def gear_events(rows: list[dict[str, Any]], lap_start: datetime, audio_lap_start_s: float) -> list[dict[str, Any]]:
    """Gear transitions with the telemetry sampling interval, not a fake exact time.

    For each changed gear, the audio shift lies somewhere between the last sample
    that reported the old gear and the first sample that reported the new gear.
    Large gear jumps may contain several real shifts missed by telemetry.
    """
    out: list[dict[str, Any]] = []
    prev_gear: int | None = None
    prev_stamp: datetime | None = None
    for row in rows:
        stamp = parse_utc(row["date"])
        gear = row.get("n_gear")
        if isinstance(gear, int) and isinstance(prev_gear, int) and gear != prev_gear:
            rel = (stamp - lap_start).total_seconds()
            prev_rel = ((prev_stamp or stamp) - lap_start).total_seconds()
            out.append({
                "timestamp_utc": row["date"],
                "relative_to_lap_start_s": round(rel, 3),
                "audio_time_s": round(audio_lap_start_s + rel, 3),
                "telemetry_interval_start_s": round(audio_lap_start_s + prev_rel, 4),
                "telemetry_interval_end_s": round(audio_lap_start_s + rel, 4),
                "previous_gear": prev_gear,
                "new_gear": gear,
                "direction": "downshift" if gear < prev_gear else "upshift",
                "rpm": row.get("rpm"), "speed_kmh": row.get("speed"),
                "throttle_pct": row.get("throttle"), "brake": row.get("brake"),
            })
        if isinstance(gear, int):
            prev_gear = gear
            prev_stamp = stamp
    return out


def match_shift_slots(events: list[dict[str, Any]], candidates: list[dict[str, Any]], audio_duration_s: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Match unique audio-transient candidates to telemetry-constrained shift slots.

    A gear jump such as 8->6 creates two *inferred* step slots. A candidate can only
    be assigned once, and we don't fabricate a clip when no candidate passes review
    thresholds. The CSV keeps the missing slots visible for manual follow-up.
    """
    slots: list[dict[str, Any]] = []
    for event_i, ev in enumerate(events):
        old, new = int(ev["previous_gear"]), int(ev["new_gear"])
        count = max(1, abs(new - old))
        start = float(ev.get("telemetry_interval_start_s", float(ev["audio_time_s"]) - 0.13))
        end = float(ev.get("telemetry_interval_end_s", float(ev["audio_time_s"])))
        if end < start:
            start, end = end, start
        span = max(0.04, end - start)
        radius = min(0.28, max(0.18, span / (2.0 * count) + 0.12))
        for step in range(count):
            target = start + span * (step + 0.5) / count
            slots.append({"slot_id": len(slots), "event_index": event_i, "step_index": step,
                          "expected_steps": count, "target_s": target, "interval_start_s": start,
                          "interval_end_s": end, "radius_s": radius, "event": ev,
                          "estimated_previous_gear": old - step if new < old else old + step,
                          "estimated_new_gear": old - step - 1 if new < old else old + step + 1,
                          "gear_jump": count > 1})

    # Greedy global matching by peak strength penalized by distance to the expected
    # portion of the telemetry interval. This avoids reusing one peak for two shifts.
    pairs: list[tuple[float, int, int, float]] = []
    for slot in slots:
        for ci, candidate in enumerate(candidates):
            dist = abs(float(candidate["time_s"]) - slot["target_s"])
            if dist > slot["radius_s"]:
                continue
            score = np.log1p(min(20.0, max(0.0, float(candidate["score"]))))
            normalized_distance = dist / max(0.025, slot["radius_s"])
            utility = score - 1.5 * normalized_distance * normalized_distance
            pairs.append((float(utility), int(slot["slot_id"]), ci, dist))
    pairs.sort(reverse=True)
    used_slots: set[int] = set()
    used_candidates: set[int] = set()
    chosen_candidate_times: list[float] = []
    for utility, slot_id, ci, dist in pairs:
        if slot_id in used_slots or ci in used_candidates:
            continue
        time_s = float(candidates[ci]["time_s"])
        if any(abs(time_s - t) < MIN_SHIFT_SEPARATION_S for t in chosen_candidate_times):
            continue
        if float(candidates[ci]["score"]) < MIN_TRANSIENT_SCORE:
            continue
        slots[slot_id]["candidate"] = candidates[ci]
        slots[slot_id]["distance_ms"] = round(dist * 1000.0, 1)
        slots[slot_id]["match_utility"] = round(utility, 3)
        used_slots.add(slot_id)
        used_candidates.add(ci)
        chosen_candidate_times.append(time_s)

    matched = sorted((s for s in slots if "candidate" in s), key=lambda s: s["candidate"]["time_s"])
    # Clip bounds are fenced by neighbouring selected shifts so the previous/next
    # gear's transient is less likely to leak into the current one-shot.
    for i, slot in enumerate(matched):
        t = float(slot["candidate"]["time_s"])
        prev_gap = t - float(matched[i - 1]["candidate"]["time_s"]) if i else None
        next_gap = float(matched[i + 1]["candidate"]["time_s"]) - t if i + 1 < len(matched) else None
        pre = SHIFT_PRE_MS / 1000.0 if prev_gap is None else min(SHIFT_PRE_MS / 1000.0, max(0.008, prev_gap * 0.40))
        post = SHIFT_POST_MS / 1000.0 if next_gap is None else min(SHIFT_POST_MS / 1000.0, max(0.018, next_gap * 0.42))
        slot["clip_start_s"] = max(0.0, t - pre)
        slot["clip_end_s"] = min(audio_duration_s, t + post)
    return slots, matched


def choose_state_window(rows: list[dict[str, Any]], lap_start: datetime, audio_lap_start_s: float,
                        state: str, window_s: float) -> dict[str, Any] | None:
    """Find a short stable telemetry window without relying on audio pitch alteration."""
    valid = []
    for r in rows:
        try:
            rel = (parse_utc(r["date"]) - lap_start).total_seconds()
            speed = float(r.get("speed") or 0); rpm = float(r.get("rpm") or 0)
            gear = int(r.get("n_gear") or 0); throttle = float(r.get("throttle") or 0)
            brake = float(r.get("brake") or 0)
        except (KeyError, ValueError, TypeError): continue
        if rel < 0: continue
        if state == "high_speed_full_throttle": ok = speed >= 280 and throttle >= 90 and brake < 10
        elif state == "mid_gear_acceleration": ok = 105 <= speed <= 255 and 3 <= gear <= 7 and throttle >= 82 and brake < 10
        elif state == "corner_exit": ok = 70 <= speed <= 185 and 2 <= gear <= 5 and 18 <= throttle <= 85 and brake < 20
        elif state == "braking": ok = brake >= 70 and gear <= 8
        else: ok = False
        valid.append({"rel": rel, "ok": bool(ok), "speed": speed, "rpm": rpm, "gear": gear,
                      "throttle": throttle, "brake": brake, "date": r["date"]})
    if len(valid) < 4: return None
    best = None
    # Test windows starting at telemetry timestamps; telemetry is ~3.7Hz, so score by sample coverage.
    for i, first in enumerate(valid):
        if first["rel"] + window_s > max(v["rel"] for v in valid): continue
        sample = [v for v in valid[i:] if first["rel"] <= v["rel"] <= first["rel"] + window_s]
        if len(sample) < 4: continue
        coverage = sum(1 for v in sample if v["ok"]) / len(sample)
        if coverage < 0.72: continue
        mean_rpm = sum(v["rpm"] for v in sample) / len(sample)
        mean_speed = sum(v["speed"] for v in sample) / len(sample)
        score = coverage * 1000 + mean_rpm / 1000 + mean_speed / 10000
        if best is None or score > best["score"]:
            best = {"score": score, "coverage": coverage, "rows": sample, "start_rel": first["rel"],
                    "end_rel": first["rel"] + window_s, "mean_rpm": mean_rpm, "mean_speed": mean_speed}
    if not best: return None
    start_audio = audio_lap_start_s + best["start_rel"]
    return {"start_s": round(start_audio, 3), "end_s": round(start_audio + window_s, 3),
            "lap_relative_start_s": round(best["start_rel"], 3), "coverage": round(best["coverage"], 3),
            "mean_rpm": round(best["mean_rpm"]), "mean_speed_kmh": round(best["mean_speed"], 1),
            "gear_range": [min(v["gear"] for v in best["rows"]), max(v["gear"] for v in best["rows"])],
            "mean_throttle_pct": round(sum(v["throttle"] for v in best["rows"]) / len(best["rows"]), 1)}


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Path to cloned CarSoundLab repository")
    ap.add_argument("--manifest", type=Path, default=Path(__file__).with_name("pole_lap_alignment.json"))
    ap.add_argument("--out", type=Path, default=Path("sample-bank-output"), help="Output directory (relative to current folder by default)")
    ap.add_argument("--no-fetch", action="store_true", help="Skip OpenF1 fetch (only creates the manifest and anchor clips)")
    ap.add_argument("--keep-existing-output", action="store_true", help="Do not clear prior output")
    args = ap.parse_args()
    root = args.repo_root.resolve()
    out = args.out.resolve()
    if out.exists() and not args.keep_existing_output:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    try: manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Cannot read manifest {args.manifest}: {e}", file=sys.stderr); return 2
    all_samples, telemetry_summary = [], []
    montage_parts = []
    montage_sr = TARGET_SR
    for item in manifest.get("recordings", []):
        circuit = item["circuit"]
        audio_path = root / item["asset_path"]
        if not audio_path.exists():
            print(f"[{circuit}] MISSING audio: {audio_path}", file=sys.stderr)
            continue
        try: audio, source_sr = read_audio(audio_path)
        except Exception as e:
            print(f"[{circuit}] cannot read audio: {e}", file=sys.stderr); continue
        audio = resample_audio(audio, source_sr, TARGET_SR)
        sr = TARGET_SR
        duration = len(audio) / sr
        if source_sr != TARGET_SR:
            print(f"[{circuit}] converted {source_sr} Hz -> {TARGET_SR} Hz for a common output rate (duration/pitch preserved)")
        lap_start = parse_utc(item["pole_lap_start_utc"])
        audio_lap_start_s = float(item["recording_lap_start_s"])
        raw_rows: list[dict[str, Any]] = []
        if not args.no_fetch:
            try:
                raw_rows = fetch_lap_data(item)
                print(f"[{circuit}] fetched {len(raw_rows)} car-data samples")
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as e:
                print(f"[{circuit}] telemetry fetch unavailable ({e}); using supplied first-downshift anchor only", file=sys.stderr)
        else:
            print(f"[{circuit}] --no-fetch: using supplied first-downshift anchor only")
        track_out = out / circuit.lower().replace(" ", "_")
        track_out.mkdir(parents=True, exist_ok=True)
        track_samples: list[dict[str, Any]] = []

        def add_sample(label: str, start_s: float, end_s: float, metadata: dict[str, Any]) -> None:
            start_s = max(0.0, float(start_s)); end_s = min(duration, float(end_s))
            if end_s - start_s < 0.050: return
            snippet = fade_edges(slice_audio(audio, sr, start_s, end_s), sr)
            filename = f"{circuit.lower()}_{len(track_samples)+1:02d}_{label}.wav"
            write_wav(track_out / filename, snippet, sr)
            entry = {"circuit": circuit, "asset": str(audio_path), "sample_file": str(track_out / filename),
                     "label": label, "source_start_s": round(start_s, 3), "source_end_s": round(end_s, 3),
                     "duration_s": round(len(snippet)/sr, 3), "sample_rate_hz": sr, "source_sample_rate_hz": source_sr, "channels": int(audio.shape[1]),
                     "pitch_shift": 0, "processing": "FFmpeg resample to 48 kHz if needed; source-preserving trim and 8ms edge fade", **metadata}
            track_samples.append(entry); all_samples.append(entry)
            montage_parts.extend([snippet, np.zeros((round(0.32 * sr), audio.shape[1]), dtype=np.float32)])

        # Anchor clip around the user's first observed downshift. This remains available even if API fetch fails.
        down = float(item["audio_first_downshift_s"])
        anchor_gear = item.get("telemetry_first_downshift_gear", "unknown")
        down_refined, down_score = refine_shift_time(audio, sr, down)
        down_start = down_refined - SHIFT_PRE_MS / 1000.0
        down_end = down_refined + SHIFT_POST_MS / 1000.0
        add_sample("first_downshift_anchor", down_start, down_end,
                   {"state": "downshift_event", "anchor": True, "gear_change": anchor_gear,
                    "telemetry_relative_s": item["telemetry_first_downshift_relative_s"],
                    "audio_time_predicted_s": round(down, 4),
                    "audio_time_refined_s": round(down_refined, 4),
                    "audio_refinement_delta_ms": round((down_refined - down) * 1000, 1),
                    "spectral_flux_relative_score": down_score,
                    "rpm": item.get("telemetry_rpm"), "speed_kmh": item.get("telemetry_speed_kmh"),
                    "note": "Short clip around an audio-refined candidate. Listen-check recommended; spectral-flux refinement is heuristic."})

        events = []
        event_review_rows = []
        if raw_rows:
            events = gear_events(raw_rows, lap_start, audio_lap_start_s)
            # Detect the whole recording once. Telemetry constrains candidate matching,
            # but does not dictate an exact sample timestamp or one clip per row.
            transients = detect_audio_transients(audio, sr)
            write_csv(track_out / f"{circuit.lower()}_audio_transient_candidates.csv", transients,
                      ["time_s", "score", "flux", "local_baseline"])
            slots, matched_slots = match_shift_slots(events, transients, duration)
            matched_by_slot = {int(s["slot_id"]): s for s in matched_slots}
            matched_times = [float(s["candidate"]["time_s"]) for s in matched_slots]
            for slot in slots:
                ev = slot["event"]
                old_g = int(ev["previous_gear"])
                new_g = int(ev["new_gear"])
                est_old = int(slot["estimated_previous_gear"])
                est_new = int(slot["estimated_new_gear"])
                candidate = slot.get("candidate")
                if candidate:
                    refined = float(candidate["time_s"])
                    clip_start = float(slot["clip_start_s"])
                    clip_end = float(slot["clip_end_s"])
                    label = "downshift" if new_g < old_g else "upshift"
                    sample_label = f"{label}_{slot['slot_id']+1:03d}_step{slot['step_index']+1}"
                    inferred = bool(slot["gear_jump"])
                    note = ("Inferred intermediate gear step; audio candidate matched inside telemetry interval; listen-check required."
                            if inferred else "Audio transient matched inside telemetry sampling interval; listen-check required.")
                    add_sample(sample_label, clip_start, clip_end,
                               {"state": f"{label}_event", "anchor": False,
                                "gear_change": f"{est_old}->{est_new}",
                                "gear_change_observed": f"{old_g}->{new_g}",
                                "gear_label_inferred": inferred,
                                "audio_time_predicted_s": round(float(slot["target_s"]), 4),
                                "audio_time_refined_s": round(refined, 4),
                                "audio_refinement_delta_ms": round((refined - float(slot["target_s"])) * 1000, 1),
                                "spectral_flux_relative_score": candidate["score"],
                                "telemetry_interval_start_s": round(float(slot["interval_start_s"]), 4),
                                "telemetry_interval_end_s": round(float(slot["interval_end_s"]), 4),
                                "expected_steps_in_gear_jump": int(slot["expected_steps"]),
                                "step_index_in_gear_jump": int(slot["step_index"] + 1),
                                "rpm": ev.get("rpm"), "speed_kmh": ev.get("speed_kmh"),
                                "throttle_pct": ev.get("throttle_pct"), "brake": ev.get("brake"),
                                "review_note": note})
                else:
                    refined = ""
                    clip_start = ""
                    clip_end = ""
                    note = "No strong distinct audio transient matched this expected step; no clip fabricated."
                event_review_rows.append({
                    **ev,
                    "expected_shift_steps": int(slot["expected_steps"]),
                    "step_index_in_transition": int(slot["step_index"] + 1),
                    "estimated_previous_gear": est_old,
                    "estimated_new_gear": est_new,
                    "gear_label_inferred": bool(slot["gear_jump"]),
                    "audio_time_predicted_s": round(float(slot["target_s"]), 4),
                    "audio_time_refined_s": round(refined, 4) if refined != "" else "",
                    "audio_refinement_delta_ms": round((float(refined) - float(slot["target_s"])) * 1000, 1) if refined != "" else "",
                    "spectral_flux_relative_score": candidate["score"] if candidate else "",
                    "candidate_distance_from_target_ms": slot.get("distance_ms", ""),
                    "clip_start_s": round(float(clip_start), 4) if clip_start != "" else "",
                    "clip_end_s": round(float(clip_end), 4) if clip_end != "" else "",
                    "telemetry_skipped_gears": bool(slot["gear_jump"]),
                    "detection_status": "matched_audio_candidate" if candidate else "candidate_not_found",
                    "review_note": note,
                })
            # Extract telemetry-conditioned snippets without changing pitch or synthesizing a tone.
            for state, win in [("high_speed_full_throttle", 1.7), ("mid_gear_acceleration", 1.6), ("corner_exit", 1.5), ("braking", 1.35)]:
                pick = choose_state_window(raw_rows, lap_start, audio_lap_start_s, state, win)
                if pick:
                    add_sample(state, pick["start_s"], pick["end_s"], {"state": state, **pick})
            telemetry_path = track_out / f"{circuit.lower()}_car_data.csv"
            tele_rows = []
            for r in raw_rows:
                try: rel = round((parse_utc(r["date"]) - lap_start).total_seconds(), 3)
                except Exception: continue
                rec = {k: r.get(k) for k in FIELDS}
                rec["relative_to_lap_start_s"] = rel
                rec["audio_time_s"] = round(audio_lap_start_s + rel, 3)
                tele_rows.append(rec)
            write_csv(telemetry_path, tele_rows, [*FIELDS, "relative_to_lap_start_s", "audio_time_s"])
            write_csv(track_out / f"{circuit.lower()}_gear_changes.csv", events,
                      ["timestamp_utc", "relative_to_lap_start_s", "audio_time_s", "previous_gear", "new_gear", "direction", "rpm", "speed_kmh", "throttle_pct", "brake"])
            write_csv(track_out / f"{circuit.lower()}_shift_event_review.csv", event_review_rows,
                      ["timestamp_utc", "relative_to_lap_start_s", "audio_time_s", "telemetry_interval_start_s", "telemetry_interval_end_s", "audio_time_predicted_s", "audio_time_refined_s", "audio_refinement_delta_ms", "spectral_flux_relative_score", "candidate_distance_from_target_ms", "clip_start_s", "clip_end_s", "previous_gear", "new_gear", "estimated_previous_gear", "estimated_new_gear", "gear_label_inferred", "expected_shift_steps", "step_index_in_transition", "direction", "telemetry_skipped_gears", "detection_status", "review_note", "rpm", "speed_kmh", "throttle_pct", "brake"])
            telemetry_summary.append({"circuit": circuit, "samples": len(raw_rows), "gear_changes": len(events), "telemetry_csv": str(telemetry_path)})
        else:
            telemetry_summary.append({"circuit": circuit, "samples": 0, "gear_changes": 0, "telemetry_csv": None,
                                     "note": "No live samples; only the provided first-downshift anchor was used."})
        print(f"[{circuit}] {duration:.2f}s, {sr}Hz, {audio.shape[1]}ch, {len(track_samples)} sample clips")
        time.sleep(0.1)

    if not all_samples:
        print("No samples generated. Check --repo-root and the five app/src/main/assets/reference-laps audio files.", file=sys.stderr)
        return 3
    fields = sorted({k for r in all_samples for k in r.keys()})
    write_csv(out / "sample_index.csv", all_samples, fields)
    (out / "sample_bank.json").write_text(json.dumps({
        "schema_version": 1,
        "source_policy": "All audio clips are excerpts from the supplied 2025 RB21 onboard recordings. Inputs are converted to 48 kHz with FFmpeg only when needed; no pitch shifting, intentional time stretching, denoising, or EQ is applied. Short shift clips use audio spectral-flux timing refinement plus 8 ms boundary fades.",
        "alignment": "audio time = recording_lap_start_s + telemetry time relative to pole-lap start; first-downshift anchor kept as a fallback.",
        "telemetry_source": "OpenF1 historical car_data API",
        "known_limitations": ["Onboard recordings are full scene mixes, not isolated engine stems.", "OpenF1 car_data samples around 3.7 Hz; telemetry defines an uncertainty interval rather than the exact shift time.", "Audio spectral-flux peaks are heuristic candidates and must be listen-checked; missing candidates are left unexported rather than invented.", "Intermediate gear labels inside multi-gear telemetry jumps are estimates, not telemetry observations.", "Snippets are proof-of-concept state-conditioned samples, not yet a real-time Android engine."],
        "telemetry": telemetry_summary, "samples": all_samples
    }, indent=2), encoding="utf-8")
    if montage_parts:
        montage = np.concatenate(montage_parts, axis=0)
        write_wav(out / "sample_bank_audition_montage.wav", montage, TARGET_SR)
    print(f"Output: {out.resolve()} — {len(all_samples)} clips across {len(telemetry_summary)} recordings")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
