# Car Sound Lab — recording-driven audio and touch-slider update

## What this overlay changes

- Fixes the sustained-rev level drop: audio load no longer depends on RPM and speed increasing during each individual audio sample. The engine stays in its loaded sound while the throttle is held, with a smooth crossfade toward the quieter overrun sound as the throttle is released.
- Uses a telemetry-matched RB21 full-throttle seventh-gear recording for high-speed operation and a third-gear corner-exit recording around its matching RPM/speed/throttle range. Recorded layers use a seam crossfade and conservative RPM-following playback rate, then fade smoothly outside their source conditions. These are full onboard mixes, not isolated engine stems; the RPM-driven engine core remains as fallback outside matching operating states.
- Restores the provided RB21 side-profile image with the Pirelli tyre markings. `MainActivity.kt` now loads `R.drawable.rb21_side_profile`; it no longer imports the missing `F1CarIllustration` composable.
- Replaces all-or-nothing hold controls with touch/drag throttle and brake sliders. Dragging sets a proportional 0–100% input, holding preserves that position, and pointer-up, cancellation or disposal resets the input to 0%. Stopping the engine also clears both pedal inputs.
- Bumps the app version to 1.23.0 (version code 24) so a newly built APK can update the previous 1.22.0 build.
- Keeps the existing shift sample loading and short gear-change triggers. Existing `shift_*.wav` assets are not included or replaced in this overlay.

## Files changed

- `app/src/main/java/com/sf12/f1soundlab/MainActivity.kt`: proportional throttle/brake drag sliders with release-to-zero behaviour.
- `app/src/main/cpp/native_audio.cpp`: state-gated onboard layers, smooth gain fades, loop-seam crossfades and conservative RPM-linked playback; existing gear-shift samples retained.
- `app/src/main/assets/sample-bank/reference_high_speed_loop.wav`: Suzuka high-speed seventh-gear excerpt.
- `app/src/main/assets/sample-bank/reference_corner_exit_loop.wav`: Suzuka third-gear corner-exit excerpt.
- `app/build.gradle.kts`: app version 1.23.1 / version code 25.
- `.github/workflows/android-build.yml`: builds the APK and verifies the recorded clips are packaged byte-for-byte.

Existing assets, including the RB21 side-profile image and gear-shift bank, are preserved.

## Validation note

Source/resource consistency checks and ZIP integrity checks were performed. This overlay has **not** been built into an APK in this environment; the local Termux setup previously reported that no Android SDK is installed. Build it with the existing GitHub Actions workflow. The new onboard loop is restricted to the speed/gear/RPM range represented by its source segment; more operating-state-matched recordings would be needed to make the full RPM range sample-driven.
