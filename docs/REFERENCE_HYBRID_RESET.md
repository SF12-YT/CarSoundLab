# v1.21.0 — Reference-driven hybrid audio reset

## Intent

This is the first deliberately minimal baseline after the accumulated supersaw and effect layers. The goal is to use the recording to constrain the sound design while retaining a controllable, real-time engine source.

The reference point is fixed onboard T-cam/cockpit perspective. This implementation does not add a trackside fly-by, synthetic listener movement, arbitrary panning or Doppler effects.

## Acoustic observations used

The bundled `MONZARB21.mp3` has 44.1 kHz stereo PCM, 32.509 s decoded duration, a measured peak of approximately 0.478 (left) and 0.332 (right), RMS of approximately 0.0906 (left) and 0.0765 (right), and left/right sample correlation of approximately 0.024.

In the 8.0–13.3 s section, an aggregate windowed spectrum showed the left channel's dominant component around 199–210 Hz and the right around 296–312 Hz. The left channel had roughly 67.5% of its 50 Hz–16 kHz energy in 50–250 Hz, while the right channel had roughly 41.1% in 250–600 Hz and 25.7% in 1.5–4 kHz. Band percentages are separately normalised per channel. They are not loudness percentages and do not identify isolated physical sound sources.

For the same section, the dominant low-frequency regions are broadly consistent with a crank-order-like component near RPM/60 on the left and an additional order near 1.5 × RPM/60 on the right at the observed high RPM. These are empirical modelling clues, not claims that the recording has isolated those physical engine orders.

The TXT file marks gears and approximate state values. It includes a rapid descent through gears 7 to 2 between approximately 13.504 and 14.854 s, followed by higher gears from 19.396 s onward. Its measurements are sparse and some timestamps may be off by around 100 ms; it is not a frame-by-frame telemetry stream.

## Current DSP path

1. Kotlin provides RPM and controls as atomically published values.
2. The native callback performs short smoothing (6 ms throttle; 9 ms RPM rise and 12 ms RPM fall constants).
3. The core generates coherent 1× crank-order, 1.5× order and supporting order components, plus a smooth firing-pulse waveform. Event cadence is three times crankshaft rotation rate, i.e. RPM/20.
4. A 2048-entry pulse table is calculated when the stream opens. The audio callback interpolates it; it does not allocate, read files, lock or decode MP3 frames.
5. Independent low-pass split banks apply empirical channel-specific spectral balance. Output uses gentle tanh shaping and a final sample clamp for safety.
6. Output remains stereo, with no moving pan or spatial trajectory.

The MP3 is not mixed back into the live output in this initial prototype. It is a mixed onboard recording, not a clean engine stem; direct mixing would duplicate the engine sound and possibly add scene content. Sample-driven texture can be evaluated later using carefully selected sections and an A/B test, rather than being enabled by assumption.

## Deliberately removed

- Ten-voice detuned supersaw stack and central saw.
- Random per-sample frequency jitter.
- Arbitrary speed-only gearbox sine.
- Synthetic 8–14 kHz turbo carrier and lift-off flutter/noise.
- Procedural metallic ringing and added MGU-K/MGU-H tones.
- Full-bus upshift gate, engagement click and downshift pop.
- Acceleration boolean that attenuated the main engine signal whenever speed and RPM were not both increasing.

The absence of these layers is intentional for a baseline. It does not mean the real car lacks these components; it means they are not added until evidence and listening justify their inclusion.

## Host-side prototype check (not an Android build)

The replacement `native_audio.cpp` was compiled with a host compiler against a minimal Oboe callback stub, then rendered to an 8-second stereo WAV for inspection. This verifies C++ syntax, callback output, finite sample values and absence of clipping in that harness; it does not verify Android NDK integration, Oboe device behaviour, perceived realism or latency.

For the first 1.7 seconds of the preview, the test state is held at 12,240 RPM. This is a calibration choice because 1x crank order (204 Hz), 1.5x order (306 Hz) and firing-event rate (612 Hz) sit close to the measured regions in the reference's 8–13.3 s section. It is **not** a claim that 12,240 RPM was recorded in the TXT at those timestamps; the annotations do not provide an RPM anchor there.

At that steady test state, the render's approximate energy shares in 50–250, 250–600, 600–1500, 1500–4000 and 4000–8000 Hz were 68%, 5%, 13%, 11% and 3% in the left channel; and 12%, 42%, 17%, 27% and 3% in the right channel. The comparable reference window measured approximately 68%, 5%, 14%, 11% and 3% left, and 14%, 41%, 16%, 26% and 2% right. These broad-band matches were used to initialise the channel EQ; they do not prove a perceptual match. The render's left/right waveform correlation remains about 0.58, compared with about 0.024 in the reference, so its stereo texture is still materially simpler than the recording.

## Performance design

The callback uses a small fixed set of sine functions and low-pass states rather than dozens of detuned oscillators and many additional per-sample transcendental operations. The pulse waveform is precomputed outside the callback. The stream continues requesting Oboe low-latency mode and a two-burst buffer target; this is a configuration request, not a measured latency guarantee.

No physical-device loopback latency or underrun result is bundled. The next check should measure stream sample rate, frames-per-burst, buffer size, callback xruns and control-response delay on the target phone. A separate audio backend should only be selected after this measurement.

## Validation criteria

1. Confirm the APK packages the exact MP3 and TXT bytes under `assets/reference-laps/`.
2. Render/replay the steady high-speed section at a comparable listening level and compare spectral balance independently for left and right channels.
3. Compare transients around the annotated gear sequence after considering the annotation's timing uncertainty.
4. Confirm throttle changes affect level/timbre promptly without adding clicks or unstable pitch; pitch remains governed by RPM.
5. Measure callback stability and end-to-end input-to-audio response on a physical device.
6. Do not claim a perceptual match until the generated output has actually been listened to alongside the reference.
