# CarSoundLab 1.23.1 — GitHub Actions build

This is the complete Android project source. The GitHub Actions workflow is included at `.github/workflows/android-build.yml`.

## Build with GitHub Actions

1. Extract this ZIP on your device or computer. Do not upload the ZIP as a single file.
2. Upload the extracted project contents to the root of your GitHub repository, preserving the directory structure (especially `.github/workflows/`).
3. Commit/push to the `main` branch. The `Build Car Sound Lab APK` workflow runs automatically.
4. Alternatively, open the repository's **Actions** tab, select **Build Car Sound Lab APK**, and choose **Run workflow**.
5. When the run completes, download `car-sound-lab-debug-apk` for the APK and `car-sound-lab-v1.23.1-full-source` for the full source ZIP.

## Fix included

The invalid `androidx.compose.ui.input.pointer.consume` import has been removed from `MainActivity.kt`. The `down.consume()` and `change.consume()` calls remain.

The app version is `1.23.1` (version code `25`) so the APK can update the previous `1.23.0` build.

No signing keystore or `local.properties` is included. The workflow builds the debug APK.
