#!/usr/bin/env python3
"""Bundle verified-ish shift one-shots from sample_bank.json into Android assets.

Run from the CarSoundLab repository root after prepare_sample_bank.py:
  python prototype/bundle_sample_bank_android.py --out prototype/sample-bank-output

Only single-step telemetry changes are included. Inferred intermediate gears are excluded
from runtime trigger samples because their labels are not confirmed by OpenF1.
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

PAIR = re.compile(r"^(\d+)\s*->\s*(\d+)$")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, default=Path("prototype/sample-bank-output"))
    parser.add_argument("--keep-existing", action="store_true", help="Preserve non-WAV files in assets/sample-bank")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    out = args.out if args.out.is_absolute() else root / args.out
    out = out.resolve()
    bank_path = out / "sample_bank.json"
    if not bank_path.exists():
        print(f"Missing {bank_path}. Run prepare_sample_bank.py first.", file=sys.stderr)
        return 2
    try:
        bank = json.loads(bank_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read {bank_path}: {exc}", file=sys.stderr)
        return 2

    asset_dir = root / "app/src/main/assets/sample-bank"
    if asset_dir.exists() and not args.keep_existing:
        shutil.rmtree(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)

    selected = []
    skipped_inferred = 0
    missing = []
    for item in bank.get("samples", []):
        state = str(item.get("state", ""))
        if state not in ("upshift_event", "downshift_event") or item.get("anchor", False):
            continue
        if item.get("gear_label_inferred", False):
            skipped_inferred += 1
            continue
        match = PAIR.match(str(item.get("gear_change", "")))
        if not match:
            continue
        gear_from, gear_to = int(match.group(1)), int(match.group(2))
        if not (1 <= gear_from <= 8 and 1 <= gear_to <= 8) or gear_from == gear_to:
            continue
        direction = "up" if gear_to > gear_from else "down"
        if (direction == "up") != (state == "upshift_event"):
            continue
        raw_sample_path = str(item.get("sample_file", "")).strip()
        src = Path(raw_sample_path) if raw_sample_path else Path("__missing_sample_path__")
        if not src.is_absolute():
            src = out / src
        if not src.is_file():
            # Output indexes may contain absolute paths from another device/session, or paths
            # relative to the repo root rather than --out. First try common interpretations.
            raw = Path(raw_sample_path) if raw_sample_path else Path("__missing_sample_path__")
            candidates = [
                out / raw,
                root / raw,
                out / raw.name,
                root / raw.name,
            ]
            found = next((candidate for candidate in candidates if candidate.is_file()), None)
            if found is None and raw.name:
                # Last-resort relocation: find the exact filename anywhere under the generated
                # sample-bank output. Never substitute a different clip by label or index.
                try:
                    found = next((candidate for candidate in out.rglob(raw.name) if candidate.is_file()), None)
                except OSError:
                    found = None
            if found is not None:
                src = found
        if not src.is_file():
            missing.append(raw_sample_path or "<empty sample_file>")
            continue
        start_s = float(item.get("source_start_s", 0.0) or 0.0)
        event_s = float(item.get("audio_time_refined_s", start_s) or start_s)
        lead_ms = max(0, min(999, round((event_s - start_s) * 1000)))
        circuit = re.sub(r"[^a-z0-9]+", "", str(item.get("circuit", "unknown")).lower()) or "unknown"
        label = f"shift_{direction}_{gear_from}_{gear_to}_{circuit}_{len(selected)+1:03d}_p{lead_ms:03d}.wav"
        shutil.copyfile(src, asset_dir / label)
        selected.append({
            "asset": f"sample-bank/{label}", "circuit": item.get("circuit"),
            "direction": direction, "gear_from": gear_from, "gear_to": gear_to,
            "lead_in_ms": lead_ms, "duration_s": item.get("duration_s"),
            "sample_rate_hz": item.get("sample_rate_hz", 48000),
            "source_sample": str(item.get("sample_file", "")),
        })

    manifest = {
        "schema_version": 1,
        "mix": "Each shift one-shot is mixed at 0.18 gain into the native synth bed.",
        "policy": "Only direct, non-inferred, single-step gear labels are bundled for runtime selection.",
        "samples": selected,
        "skipped_inferred_events": skipped_inferred,
        "missing_source_files": missing,
    }
    (asset_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pairs = sorted({(s["gear_from"], s["gear_to"]) for s in selected})
    print(f"Bundled {len(selected)} shift samples for {len(pairs)} observed gear pairs.")
    print(f"Skipped {skipped_inferred} inferred intermediate-gear clips.")
    if missing:
        print(f"WARNING: {len(missing)} source clips were missing; see missing_source_files in manifest.json.")
    print(f"Android assets: {asset_dir}")
    if not selected:
        print("No shift clips bundled. Check sample_bank.json and sample_file paths.", file=sys.stderr)
        return 3
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
