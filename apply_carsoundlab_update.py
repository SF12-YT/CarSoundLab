#!/usr/bin/env python3
"""Apply the CarSoundLab 1.23.0 recording-driven audio update to an existing checkout.

Run from the root of the CarSoundLab repository:
    python apply_carsoundlab_update.py

The script stages changes in memory first, checks that the known source files and source WAVs
exist, then writes the source changes and copies the two bundled RB21 excerpts into Android assets.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path.cwd()
MAIN = Path("app/src/main/java/com/sf12/f1soundlab/MainActivity.kt")
NATIVE = Path("app/src/main/cpp/native_audio.cpp")
GRADLE = Path("app/build.gradle.kts")
README = Path("README_RB21_AUDIO_PEDAL_FIX.md")
WORKFLOW = Path(".github/workflows/android-build.yml")
HIGH_SOURCE = Path("prototype/sample-bank-output/suzuka/suzuka_35_high_speed_full_throttle.wav")
CORNER_SOURCE = Path("prototype/sample-bank-output/suzuka/suzuka_37_corner_exit.wav")
HIGH_DEST = Path("app/src/main/assets/sample-bank/reference_high_speed_loop.wav")
CORNER_DEST = Path("app/src/main/assets/sample-bank/reference_corner_exit_loop.wav")


def need(path: Path) -> None:
    if not (ROOT / path).is_file():
        raise RuntimeError(f"Required file not found: {path}. Run this from the root of the CarSoundLab checkout.")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one occurrence of {label}; found {count}. No files have been written yet.")
    return text.replace(old, new, 1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    for path in (MAIN, NATIVE, GRADLE, README, WORKFLOW, HIGH_SOURCE, CORNER_SOURCE):
        need(path)
    if (ROOT / HIGH_SOURCE).stat().st_size != 326444:
        raise RuntimeError(f"Unexpected high-speed WAV size; check source: {HIGH_SOURCE}")
    if (ROOT / CORNER_SOURCE).stat().st_size != 288044:
        raise RuntimeError(f"Unexpected corner-exit WAV size; check source: {CORNER_SOURCE}")

    content = {p: (ROOT / p).read_text(encoding="utf-8") for p in (MAIN, NATIVE, GRADLE, README, WORKFLOW)}

    # MainActivity: a true touch-position slider; pointer-up, cancellation and disposal reset to zero.
    main_text = content[MAIN]
    for old, new, label in (
        ("import androidx.compose.foundation.layout.Box\n", "import androidx.compose.foundation.layout.Box\nimport androidx.compose.foundation.layout.BoxWithConstraints\n", "BoxWithConstraints import"),
        ("import androidx.compose.foundation.layout.fillMaxSize\n", "import androidx.compose.foundation.layout.fillMaxSize\nimport androidx.compose.foundation.layout.fillMaxHeight\n", "fillMaxHeight import"),
        ("import androidx.compose.foundation.layout.height\n", "import androidx.compose.foundation.layout.height\nimport androidx.compose.foundation.layout.offset\n", "offset import"),
        ("import androidx.compose.ui.input.pointer.pointerInput\n", "import androidx.compose.ui.input.pointer.pointerInput\nimport androidx.compose.ui.input.pointer.consume\n", "pointer consume import"),
    ):
        if new.splitlines()[0] not in main_text or new.splitlines()[1] not in main_text:
            main_text = replace_once(main_text, old, new, label)
    main_text = main_text.replace("import androidx.compose.foundation.gestures.waitForUpOrCancellation\n", "")
    main_text = main_text.replace(
        'hint = "HOLD TO APPLY POWER · RELEASE TO RETURN TO 0%"',
        'hint = "DRAG TO APPLY POWER · HOLD POSITION · RELEASE TO RETURN TO 0%"')
    main_text = main_text.replace(
        'hint = "HOLD TO BRAKE · LOCK-UP ≥72% AFTER 0.8S"',
        'hint = "DRAG TO BRAKE · HOLD POSITION · RELEASE TO RETURN TO 0%"')
    main_text = main_text.replace(
        'onPressed = { pressed -> onThrottleChange(if (pressed) 100f else 0f) }',
        'onValueChange = onThrottleChange')
    main_text = main_text.replace(
        'onPressed = { pressed -> onBrakeChange(if (pressed) 100f else 0f) }',
        'onValueChange = onBrakeChange')

    if "private fun HoldPedal(" not in main_text:
        raise RuntimeError("Could not find HoldPedal composable; no files have been written yet.")
    hold_start = main_text.index("private fun HoldPedal(")
    hold_tail = main_text[hold_start:]
    if not ("onValueChange: (Float) -> Unit" in hold_tail and "DRAG TO SET" in hold_tail):
        main_text = main_text[:hold_start] + r'''private fun HoldPedal(
    label: String,
    value: Float,
    color: Color,
    hint: String,
    onValueChange: (Float) -> Unit
) {
    val latestOnValueChange = rememberUpdatedState(onValueChange)
    val progress = (value / 100f).coerceIn(0f, 1f)
    val shape = RoundedCornerShape(15.dp)

    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(color))
            Spacer(Modifier.width(7.dp))
            Text(label, color = White, fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.1.sp)
            Spacer(Modifier.weight(1f))
            Text("${value.roundToInt()}%", color = color, fontSize = 17.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
        }
        BoxWithConstraints(
            modifier = Modifier
                .fillMaxWidth()
                .height(62.dp)
                .clip(shape)
                .background(PanelRaised)
                .border(1.dp, if (value > 0f) color else Hairline, shape)
                .pointerInput(Unit) {
                    try {
                        awaitEachGesture {
                            val down = awaitFirstDown(requireUnconsumed = false)
                            val pointerId = down.id
                            fun updateAt(x: Float) {
                                val widthPx = size.width.toFloat().coerceAtLeast(1f)
                                latestOnValueChange.value((x / widthPx * 100f).coerceIn(0f, 100f))
                            }
                            try {
                                down.consume()
                                updateAt(down.position.x)
                                while (true) {
                                    val event = awaitPointerEvent()
                                    val change = event.changes.firstOrNull { it.id == pointerId } ?: break
                                    if (!change.pressed) break
                                    updateAt(change.position.x)
                                    change.consume()
                                }
                            } finally {
                                latestOnValueChange.value(0f)
                            }
                        }
                    } finally {
                        latestOnValueChange.value(0f)
                    }
                },
            contentAlignment = Alignment.Center
        ) {
            Box(
                modifier = Modifier
                    .align(Alignment.CenterStart)
                    .fillMaxWidth(progress)
                    .fillMaxHeight()
                    .background(color.copy(alpha = if (value > 0f) 0.24f else 0.0f))
            )
            Box(
                modifier = Modifier
                    .align(Alignment.CenterStart)
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp)
                    .height(4.dp)
                    .clip(CircleShape)
                    .background(Hairline)
            )
            Box(
                modifier = Modifier
                    .align(Alignment.CenterStart)
                    .offset(x = (maxWidth * progress - 10.dp).coerceIn(0.dp, maxWidth - 20.dp))
                    .size(20.dp)
                    .clip(CircleShape)
                    .background(color)
                    .border(2.dp, Night, CircleShape)
            )
            Text(
                text = if (value > 0f) "${value.roundToInt()}%  ·  KEEP HOLDING" else "DRAG TO SET  ·  RELEASE TO RESET",
                color = if (value > 0f) color else White,
                fontSize = 10.sp,
                fontWeight = FontWeight.Black,
                letterSpacing = 0.65.sp,
                textAlign = TextAlign.Center,
                maxLines = 1
            )
        }
        Text(hint, color = Muted, fontSize = 8.sp, letterSpacing = 0.7.sp, modifier = Modifier.align(Alignment.End))
    }
}
'''
    if "onPressed" in main_text or "latestOnValueChange.value(0f)" not in main_text:
        raise RuntimeError("Slider transformation did not pass validation; no files have been written yet.")

    # Native engine: two authentic, telemetry-matched sources with an overlap crossfade at loop seams.
    native_text = content[NATIVE]
    start = native_text.index("    void loadReferenceLoop() {")
    end = native_text.index("    void triggerShiftSample(", start)
    native_text = native_text[:start] + r'''    void loadReferenceLoop() {
        ShiftSample highSpeed;
        if (readPcm16WavAsset("reference_high_speed_loop.wav", highSpeed)) {
            referenceHighSpeedLoop_ = std::move(highSpeed);
        }
        ShiftSample cornerExit;
        if (readPcm16WavAsset("reference_corner_exit_loop.wav", cornerExit)) {
            referenceCornerExitLoop_ = std::move(cornerExit);
        }
    }

    void mixReferenceLoop(float& left, float& right, double throttle, double brake) {
        const double speedKmh = speedMps_ * 3.6;
        // Stable seventh-gear RB21 onboard passage: use only at its matching high-speed state.
        const bool referenceState = (gear_ == 7 || gear_ == 8) &&
                speedKmh >= 265.0 && speedKmh <= 350.0 &&
                rpm_ >= 10500.0 && rpm_ <= 12800.0 && throttle >= 0.72 && brake < 0.12;
        mixLoopSample(referenceHighSpeedLoop_, referenceLoopPosition_, referenceLoopGain_,
                referenceState ? 0.88 : 0.0, 11771.0, 1.22, left, right);
    }

    void mixCornerExitLoop(float& left, float& right, double throttle, double brake) {
        const double speedKmh = speedMps_ * 3.6;
        // Recorded third-gear corner exit; never use it as an idle or unrelated rev sample.
        const bool referenceState = gear_ == 3 && speedKmh >= 52.0 && speedKmh <= 128.0 &&
                rpm_ >= 5400.0 && rpm_ <= 8000.0 &&
                throttle >= 0.18 && throttle <= 0.72 && brake < 0.16;
        mixLoopSample(referenceCornerExitLoop_, referenceCornerExitPosition_, referenceCornerExitGain_,
                referenceState ? 0.78 : 0.0, 6561.0, 1.18, left, right);
    }

    void mixLoopSample(const ShiftSample& sample, double& position, double& currentGain,
                       double targetGain, double sourceRpm, double trim,
                       float& left, float& right) {
        if (sample.pcm.empty() || sample.channels < 1 || sample.sampleRate <= 0) {
            currentGain = 0.0;
            return;
        }
        const size_t frames = sample.pcm.size() / static_cast<size_t>(sample.channels);
        if (frames < 16) { currentGain = 0.0; return; }
        const double response = targetGain > currentGain ? 7.5 : 10.0;
        currentGain += (targetGain - currentGain) * (1.0 - std::exp(-dt_ * response));
        if (currentGain < 1.0e-5) {
            currentGain = 0.0;
            return;
        }

        // Crossfade the last ~100 ms into the beginning; resume after the overlap on wrap.
        const size_t fadeFrames = std::min(
                static_cast<size_t>(sample.sampleRate / 10),
                std::max<size_t>(1u, frames / 4u));
        const size_t loopStart = frames - fadeFrames;
        const double safePosition = std::clamp(position, 0.0, static_cast<double>(frames - 1u));
        const double pitchFollow = clampd(rpm_ / std::max(1.0, sourceRpm), 0.88, 1.12);
        const double rate = (static_cast<double>(sample.sampleRate) / static_cast<double>(sampleRate_)) * pitchFollow;

        auto sampleAt = [&](double framePosition, int channel) -> double {
            const double clamped = std::clamp(framePosition, 0.0, static_cast<double>(frames - 1u));
            const size_t frame0 = static_cast<size_t>(clamped);
            const size_t frame1 = std::min(frame0 + 1u, frames - 1u);
            const double blend = clamped - static_cast<double>(frame0);
            const int c = sample.channels == 1 ? 0 : channel;
            const double first = static_cast<double>(sample.pcm[frame0 * static_cast<size_t>(sample.channels) +
                                                                  static_cast<size_t>(c)]) / 32768.0;
            const double second = static_cast<double>(sample.pcm[frame1 * static_cast<size_t>(sample.channels) +
                                                                   static_cast<size_t>(c)]) / 32768.0;
            return first + (second - first) * blend;
        };

        double sourceLeft = sampleAt(safePosition, 0);
        double sourceRight = sampleAt(safePosition, 1);
        if (safePosition >= static_cast<double>(loopStart)) {
            const double seamBlend = clampd(
                    (safePosition - static_cast<double>(loopStart)) / static_cast<double>(fadeFrames), 0.0, 1.0);
            const double startPosition = safePosition - static_cast<double>(loopStart);
            sourceLeft = sourceLeft * (1.0 - seamBlend) + sampleAt(startPosition, 0) * seamBlend;
            sourceRight = sourceRight * (1.0 - seamBlend) + sampleAt(startPosition, 1) * seamBlend;
        }
        const double gain = currentGain;
        left = static_cast<float>(static_cast<double>(left) * (1.0 - gain) + sourceLeft * trim * gain);
        right = static_cast<float>(static_cast<double>(right) * (1.0 - gain) + sourceRight * trim * gain);

        position += rate;
        if (position >= static_cast<double>(frames)) {
            position = static_cast<double>(fadeFrames) +
                    std::fmod(position - static_cast<double>(frames),
                              static_cast<double>(std::max<size_t>(1u, loopStart)));
        }
    }

''' + native_text[end:]
    old_mix = "        mixReferenceLoop(outLeft, outRight, throttle, brake);\n        // Brief recorded gear-change transients stay above the continuous bed."
    if old_mix in native_text:
        native_text = native_text.replace(old_mix,
            "        mixReferenceLoop(outLeft, outRight, throttle, brake);\n"
            "        mixCornerExitLoop(outLeft, outRight, throttle, brake);\n"
            "        // Brief recorded gear-change transients stay above the continuous bed.", 1)
    elif "mixCornerExitLoop(outLeft" not in native_text:
        raise RuntimeError("Could not add corner-exit layer to the audio callback; no files have been written yet.")
    old_members = "    ShiftSample referenceHighSpeedLoop_;\n    double referenceLoopPosition_ = 0.0;\n    double referenceLoopGain_ = 0.0;"
    new_members = ("    ShiftSample referenceHighSpeedLoop_;\n    ShiftSample referenceCornerExitLoop_;\n"
                   "    double referenceLoopPosition_ = 0.0;\n    double referenceLoopGain_ = 0.0;\n"
                   "    double referenceCornerExitPosition_ = 0.0;\n    double referenceCornerExitGain_ = 0.0;")
    if old_members in native_text:
        native_text = native_text.replace(old_members, new_members, 1)
    if "referenceCornerExitLoop_" not in native_text or "void mixLoopSample(" not in native_text:
        raise RuntimeError("Native audio sample-route validation failed; no files have been written yet.")
    changed_loop = "mixCornerExitLoop(outLeft" in native_text
    if not changed_loop:
        raise RuntimeError("Corner exit layer missing from render path; no files have been written yet.")
    content[NATIVE] = native_text
    content[MAIN] = main_text

    # App version bump.
    gradle = content[GRADLE]
    if 'versionName = "1.22.0"' in gradle and "versionCode = 23" in gradle:
        gradle = gradle.replace("versionCode = 23", "versionCode = 24", 1).replace(
            'versionName = "1.22.0"', 'versionName = "1.23.0"', 1)
    if 'versionName = "1.23.0"' not in gradle or "versionCode = 24" not in gradle:
        raise RuntimeError("app/build.gradle.kts is not version 1.22.0 or already-updated 1.23.0; refusing to guess.")
    content[GRADLE] = gradle

    # Update the documentation so it describes the real files and the remaining limitations accurately.
    doc = content[README]
    doc = doc.replace("# Car Sound Lab — RB21 audio, side-profile and hold-pedal fix",
                      "# Car Sound Lab — recording-driven audio and touch-slider update")
    doc = doc.replace(
        "- Adds a 5.2-second, seam-crossfaded PCM loop made from the supplied Monza RB21 onboard recording's high-speed eighth-gear section (about 8.0–13.3 seconds). The native mixer fades this real stereo onboard layer in only around matching high-speed, high-RPM, high-throttle eighth-gear conditions, rather than misusing wind/road noise as a stationary rev sample. The existing synthesizer remains the fallback at other operating points.",
        "- Uses a telemetry-matched RB21 full-throttle seventh-gear recording for high-speed operation and a third-gear corner-exit recording around its matching RPM/speed/throttle range. Recorded layers use a seam crossfade and conservative RPM-following playback rate, then fade smoothly outside their source conditions. These are full onboard mixes, not isolated engine stems; the RPM-driven engine core remains as fallback outside matching operating states.")
    doc = doc.replace(
        "- Replaces sticky percentage sliders with two press-and-hold pedals. Holding throttle sets it to 100%; releasing, cancelling, or dragging the touch away returns it to 0%. Brake works the same way. Stopping the engine also clears both pedal inputs.",
        "- Replaces all-or-nothing hold controls with touch/drag throttle and brake sliders. Dragging sets a proportional 0–100% input, holding preserves that position, and pointer-up, cancellation or disposal resets the input to 0%. Stopping the engine also clears both pedal inputs.")
    doc = doc.replace(
        "- Bumps the app version to 1.22.0 (version code 23) so a newly built APK can update the previous 1.21.0 build.",
        "- Bumps the app version to 1.23.0 (version code 24) so a newly built APK can update the previous 1.22.0 build.")
    apply_at, validation_at = doc.find("## Apply"), doc.find("## Validation note")
    if apply_at >= 0 and validation_at > apply_at:
        doc = doc[:apply_at] + """## Files changed

- `app/src/main/java/com/sf12/f1soundlab/MainActivity.kt`: proportional throttle/brake drag sliders with release-to-zero behaviour.
- `app/src/main/cpp/native_audio.cpp`: state-gated onboard layers, smooth gain fades, loop-seam crossfades and conservative RPM-linked playback; existing gear-shift samples retained.
- `app/src/main/assets/sample-bank/reference_high_speed_loop.wav`: Suzuka high-speed seventh-gear excerpt.
- `app/src/main/assets/sample-bank/reference_corner_exit_loop.wav`: Suzuka third-gear corner-exit excerpt.
- `app/build.gradle.kts`: app version 1.23.0 / version code 24.
- `.github/workflows/android-build.yml`: builds the APK and verifies the recorded clips are packaged byte-for-byte.

Existing assets, including the RB21 side-profile image and gear-shift bank, are preserved.

""" + doc[validation_at:]
    content[README] = doc

    # CI should compare the exact two known samples in the source tree, app assets and final APK.
    workflow = content[WORKFLOW]
    workflow = workflow.replace("CarSoundLab_v1.21.0_ReferenceHybrid_FullSource.zip",
                                "CarSoundLab_v1.23.0_RecordingDriven_FullSource.zip")
    workflow = workflow.replace("name: car-sound-lab-reference-hybrid-source",
                                "name: car-sound-lab-recording-driven-source")
    if "Verified packaged recording" not in workflow:
        anchor = "          done\n\n      - name: Package complete source tree"
        verification = r'''          done

          for SPEC in "reference_high_speed_loop.wav:suzuka_35_high_speed_full_throttle.wav" "reference_corner_exit_loop.wav:suzuka_37_corner_exit.wav"; do
            NAME="${SPEC%%:*}"
            SOURCE_NAME="${SPEC#*:}"
            SOURCE="prototype/sample-bank-output/suzuka/${SOURCE_NAME}"
            APP_SOURCE="app/src/main/assets/sample-bank/${NAME}"
            APK_PATH="assets/sample-bank/${NAME}"
            test -s "$SOURCE"
            cmp "$SOURCE" "$APP_SOURCE"
            unzip -Z1 "$APK" | grep -Fx "$APK_PATH" >/dev/null
            unzip -p "$APK" "$APK_PATH" > "$RUNNER_TEMP/${NAME}"
            cmp "$SOURCE" "$RUNNER_TEMP/${NAME}"
            echo "Verified packaged recording: ${APK_PATH}"
            sha256sum "$SOURCE"
          done

      - name: Package complete source tree'''
        workflow = replace_once(workflow, anchor, verification, "CI recording verification anchor")
    if "path: CarSoundLab_v1.23.0_RecordingDriven_FullSource.zip" not in workflow:
        raise RuntimeError("Workflow artifact output path failed validation; no files have been written yet.")
    content[WORKFLOW] = workflow

    # Final consistency checks before making any changes.
    if 'versionName = "1.23.0"' not in content[GRADLE]:
        raise RuntimeError("Version check failed; no files have been written yet.")
    if "reference_corner_exit_loop.wav" not in content[NATIVE] or "DRAG TO SET" not in content[MAIN]:
        raise RuntimeError("Final source check failed; no files have been written yet.")

    # Commit source edits only after all transformations validate.
    for path, text in content.items():
        (ROOT / path).write_text(text, encoding="utf-8", newline="\n")
    (ROOT / HIGH_DEST).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / HIGH_SOURCE, ROOT / HIGH_DEST)
    shutil.copyfile(ROOT / CORNER_SOURCE, ROOT / CORNER_DEST)

    print("CarSoundLab 1.23.0 source update applied.")
    print("Updated text files:")
    for path in (MAIN, NATIVE, GRADLE, README, WORKFLOW):
        print(f"  {path}")
    print("Installed real RB21 recording excerpts:")
    for path in (HIGH_DEST, CORNER_DEST):
        print(f"  {path}  { (ROOT / path).stat().st_size} bytes  sha256={sha256(ROOT / path)}")
    print("Next: inspect with `git diff`, then commit and push to trigger the repository's Android build workflow.")
    print("Note: the excerpts are full onboard mixes, not isolated engine stems; real device listening is still required.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
