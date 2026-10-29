"""Offline smoke tests: run with `python -m unittest test_sample_bank.py` in prototype/."""
from __future__ import annotations
import unittest
import numpy as np
import prepare_sample_bank as prep


class SampleBankTests(unittest.TestCase):
    def test_ffmpeg_resampling_keeps_tone_and_duration(self) -> None:
        source_sr = 44100
        t = np.arange(source_sr, dtype=np.float32) / source_sr
        tone = np.stack((0.3 * np.sin(2 * np.pi * 440 * t),
                         0.25 * np.sin(2 * np.pi * 440 * t)), axis=1).astype(np.float32)
        converted = prep.resample_audio(tone, source_sr, 48000)
        self.assertEqual(converted.shape[1], 2)
        self.assertLess(abs(len(converted) / 48000 - 1.0), 0.001)
        segment = converted[4800:-4800, 0]
        spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
        frequency_bins = np.fft.rfftfreq(len(segment), 1 / 48000)
        dominant_hz = float(frequency_bins[int(np.argmax(spectrum))])
        self.assertLess(abs(dominant_hz - 440), 1.0)

    def test_transient_detector_finds_synthetic_shift_onsets(self) -> None:
        sr = 48000
        duration = 4.0
        t = np.arange(round(sr * duration)) / sr
        freq = np.select((t < 1.0, t < 1.18, t < 2.1), (420.0, 280.0, 520.0), default=310.0)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        signal = (0.35 * np.sin(phase) + 0.08 * np.sin(phase * 2.1)).astype(np.float32)
        rng = np.random.default_rng(3)
        expected = (1.0, 1.18, 2.1)
        for at in expected:
            start, end = int((at - 0.008) * sr), int((at + 0.012) * sr)
            signal[start:end] += rng.normal(0, 0.2, end - start).astype(np.float32)
        stereo = np.column_stack((signal, signal * 0.87))
        candidates = prep.detect_audio_transients(stereo, sr)
        for at in expected:
            self.assertTrue(any(abs(float(c["time_s"]) - at) < 0.10 for c in candidates),
                            f"No onset candidate near {at}s")

    def test_multigear_jump_gets_distinct_inferred_steps(self) -> None:
        event = {
            "timestamp_utc": "2025-01-01T00:00:00Z", "relative_to_lap_start_s": 1.0,
            "audio_time_s": 1.0, "telemetry_interval_start_s": 0.72,
            "telemetry_interval_end_s": 1.0, "previous_gear": 8, "new_gear": 6,
            "direction": "downshift", "rpm": 10000, "speed_kmh": 200,
            "throttle_pct": 0, "brake": 100,
        }
        candidates = [
            {"time_s": 0.79, "score": 2.2, "flux": 1.0, "local_baseline": 0.4},
            {"time_s": 0.93, "score": 2.6, "flux": 1.1, "local_baseline": 0.4},
        ]
        slots, matched = prep.match_shift_slots([event], candidates, 3.0)
        self.assertEqual(len(slots), 2)
        self.assertEqual(len(matched), 2)
        self.assertEqual([(s["estimated_previous_gear"], s["estimated_new_gear"]) for s in matched],
                         [(8, 7), (7, 6)])
        self.assertTrue(all(s["clip_end_s"] - s["clip_start_s"] < 0.15 for s in matched))
        self.assertGreaterEqual(matched[0]["clip_end_s"], matched[0]["candidate"]["time_s"])
        self.assertLessEqual(matched[0]["clip_end_s"], matched[1]["candidate"]["time_s"])


if __name__ == "__main__":
    unittest.main()
