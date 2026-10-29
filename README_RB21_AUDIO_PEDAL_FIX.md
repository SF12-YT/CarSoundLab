# Car Sound Lab — RB21 audio, side-profile and hold-pedal fix

## What this overlay changes

- Fixes the sustained-rev level drop: audio load no longer depends on RPM and speed increasing during each individual audio sample. The engine stays in its loaded sound while the throttle is held, with a smooth crossfade toward the quieter overrun sound as the throttle is released.
- Adds a 5.2-second, seam-crossfaded PCM loop made from the supplied Monza RB21 onboard recording's high-speed eighth-gear section (about 8.0–13.3 seconds). The native mixer fades this real stereo onboard layer in only around matching high-speed, high-RPM, high-throttle eighth-gear conditions, rather than misusing wind/road noise as a stationary rev sample. The existing synthesizer remains the fallback at other operating points.
- Restores the provided RB21 side-profile image with the Pirelli tyre markings. `MainActivity.kt` now loads `R.drawable.rb21_side_profile`; it no longer imports the missing `F1CarIllustration` composable.
- Replaces sticky percentage sliders with two press-and-hold pedals. Holding throttle sets it to 100%; releasing, cancelling, or dragging the touch away returns it to 0%. Brake works the same way. Stopping the engine also clears both pedal inputs.
- Bumps the app version to 1.22.0 (version code 23) so a newly built APK can update the previous 1.21.0 build.
- Keeps the existing shift sample loading and short gear-change triggers. Existing `shift_*.wav` assets are not included or replaced in this overlay.

## Apply

Extract this ZIP into the root of the existing `CarSoundLab` project and allow it to overwrite matching files. It contains only the changed source files, the RB21 image resource, the new high-speed loop WAV, and this README. It does not contain or delete the rest of the project or your existing `sample-bank/shift_*.wav` assets.

Changed/added files:

- `app/build.gradle.kts`
- `app/src/main/cpp/native_audio.cpp`
- `app/src/main/java/com/sf12/f1soundlab/MainActivity.kt`
- `app/src/main/java/com/sf12/f1soundlab/audio/F1AudioEngine.kt`
- `app/src/main/res/drawable-nodpi/rb21_side_profile.png`
- `app/src/main/assets/sample-bank/reference_high_speed_loop.wav`

## Validation note

Source/resource consistency checks and ZIP integrity checks were performed. This overlay has **not** been built into an APK in this environment; the local Termux setup previously reported that no Android SDK is installed. Build it with the existing GitHub Actions workflow. The new onboard loop is restricted to the speed/gear/RPM range represented by its source segment; more operating-state-matched recordings would be needed to make the full RPM range sample-driven.
