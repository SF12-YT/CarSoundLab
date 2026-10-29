# Applying this Car Sound Lab overlay

This ZIP is an **overlay for the existing `SF12-YT/CarSoundLab` repository**, not a standalone export. Extract it into the root of the existing repository and allow files to merge/overwrite. Do not delete existing files that are not present in this overlay; in particular, retain the existing Kotlin source under `app/src/main/java/`.

This overlay deliberately includes the root Gradle files, `app/src/main/AndroidManifest.xml`, the native CMake project, the RB21 illustration resource, and both reference files. The reference directory is exactly:

- `app/src/main/assets/reference-laps/MONZARB21.mp3`
- `app/src/main/assets/reference-laps/MONZARB21.txt`

After committing/pushing the overlay to `main` (or `master`), GitHub Actions will build the debug APK, verify that both reference files are inside the APK byte-for-byte, and upload a complete source ZIP as a second artifact. The source ZIP is produced from the whole repository checkout, so it includes the existing Kotlin UI source that is intentionally left unchanged by this overlay.

The C++ audio source was syntax-checked and exercised with a host-side Oboe callback stub. This does **not** count as an Android/NDK Gradle build or a physical-device latency test. Listen to the included WAV preview and the resulting APK on the target phone before deciding whether the timbre is an improvement.
