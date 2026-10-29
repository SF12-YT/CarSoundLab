# CarSoundLab Sample Hybrid Android Overlay — merged path-fix edition

This is an **overlay**, not a full standalone Android project. Extract it into the root of your existing CarSoundLab repository and allow matching files to be replaced.

## What this ZIP contains

- The sample-hybrid Android overlay source files from `CarSoundLab_SampleHybrid_AndroidOverlay_v1.zip`.
- The path-fixed `prototype/bundle_sample_bank_android.py` from the follow-up patch.
- No deletion instructions and no replacement of your existing project tree.

## Existing assets are preserved

ZIP extraction overwrites only files with matching paths included in this archive. It does **not** delete files that are already in your project but absent from the archive. In particular, this ZIP does not include or remove `app/src/main/assets/sample-bank/`, so the 143 WAV files and `manifest.json` already bundled there should remain untouched.

## Apply it

1. Keep working in your existing `CarSoundLab` project folder.
2. Extract this ZIP into that folder, allowing matching files to be replaced.
3. Confirm the existing sample bank remains present:

   ```bash
   find app/src/main/assets/sample-bank -maxdepth 1 -type f | wc -l
   ```

4. Do not rerun the bundler unless you intentionally need to regenerate the bank. The 143 WAV assets are already bundled.
5. Build from the existing project when ready:

   ```bash
   ./gradlew clean assembleDebug
   ```

This ZIP has not itself been compiled into an APK. A successful Gradle build and on-device playback test are still required.
