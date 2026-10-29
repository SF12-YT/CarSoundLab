#!/usr/bin/env python3
"""Fetch lap-window car telemetry from OpenF1 and export sample/gear-change CSVs.

Example:
    python fetch_lap_telemetry.py pole_lap_alignment.json --out telemetry-output

Requires only Python 3.10+ and network access to api.openf1.org.
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.openf1.org/v1/car_data"
FIELDS = ["date", "driver_number", "session_key", "n_gear", "rpm", "speed", "throttle", "brake", "drs"]


def parse_utc(value: str) -> datetime:
    """Parse OpenF1's ISO-8601 timestamp and normalize it to UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_json(url: str, timeout: float = 30.0) -> Any:
    request = Request(url, headers={"User-Agent": "CarSoundLab-telemetry-alignment/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def fetch_lap_window(item: dict[str, Any], padding_before: float, padding_after: float) -> list[dict[str, Any]]:
    lap_start = parse_utc(item["pole_lap_start_utc"])
    lap_end = lap_start + timedelta(seconds=float(item["pole_lap_duration_s"]))
    start = (lap_start - timedelta(seconds=padding_before)).isoformat(timespec="milliseconds")
    end = (lap_end + timedelta(seconds=padding_after)).isoformat(timespec="milliseconds")
    params = {
        "session_key": str(item["session_key"]),
        "driver_number": "1",
        "date>": start,
        "date<": end,
    }
    url = API + "?" + urlencode(params)
    data = fetch_json(url)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected OpenF1 response for {item['circuit']}: {data!r}")
    data.sort(key=lambda row: parse_utc(row["date"]))
    return data


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="pole_lap_alignment.json")
    parser.add_argument("--out", type=Path, default=Path("telemetry-output"), help="output directory")
    parser.add_argument("--padding-before", type=float, default=3.0, help="seconds of telemetry before lap start")
    parser.add_argument("--padding-after", type=float, default=2.0, help="seconds after pole-lap finish")
    args = parser.parse_args()

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read manifest: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    for idx, item in enumerate(manifest.get("recordings", [])):
        circuit = item["circuit"]
        try:
            records = fetch_lap_window(item, args.padding_before, args.padding_after)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"[{circuit}] ERROR: {exc}", file=sys.stderr)
            continue
        if not records:
            print(f"[{circuit}] no telemetry returned", file=sys.stderr)
            continue

        lap_start = parse_utc(item["pole_lap_start_utc"])
        raw_rows: list[dict[str, Any]] = []
        gear_rows: list[dict[str, Any]] = []
        previous_gear: int | None = None
        for record in records:
            stamp = parse_utc(record["date"])
            row = {key: record.get(key) for key in FIELDS}
            row["relative_to_lap_start_s"] = round((stamp - lap_start).total_seconds(), 3)
            raw_rows.append(row)
            gear = record.get("n_gear")
            if isinstance(gear, int) and isinstance(previous_gear, int) and gear != previous_gear:
                gear_rows.append({
                    "circuit": circuit,
                    "timestamp_utc": record["date"],
                    "relative_to_lap_start_s": row["relative_to_lap_start_s"],
                    "previous_gear": previous_gear,
                    "new_gear": gear,
                    "direction": "downshift" if gear < previous_gear else "upshift",
                    "rpm": record.get("rpm"),
                    "speed_kmh": record.get("speed"),
                    "throttle_pct": record.get("throttle"),
                    "brake": record.get("brake"),
                })
            if isinstance(gear, int):
                previous_gear = gear
        stem = circuit.lower().replace(" ", "_")
        write_csv(args.out / f"{stem}_car_data.csv", raw_rows,
                  [*FIELDS, "relative_to_lap_start_s"])
        write_csv(args.out / f"{stem}_gear_changes.csv", gear_rows,
                  ["circuit", "timestamp_utc", "relative_to_lap_start_s", "previous_gear", "new_gear", "direction", "rpm", "speed_kmh", "throttle_pct", "brake"])
        shifts = sum(1 for row in gear_rows if row["direction"] == "downshift")
        print(f"[{circuit}] {len(raw_rows)} samples, {len(gear_rows)} gear changes ({shifts} downshifts)")
        if idx < len(manifest.get("recordings", [])) - 1:
            time.sleep(0.15)
    print(f"Output: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
