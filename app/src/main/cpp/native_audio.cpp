#include <jni.h>
#include <oboe/Oboe.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <memory>
#include <new>
#include <string>
#include <unordered_map>
#include <vector>

namespace {
constexpr double kPi = 3.1415926535897932384626433832795;
constexpr double kTwoPi = 2.0 * kPi;

static_assert(std::atomic<float>::is_always_lock_free,
              "Control values used by the audio callback must be lock-free.");
static_assert(std::atomic<int32_t>::is_always_lock_free,
              "Telemetry atomics must be lock-free.");
static_assert(std::atomic<uint32_t>::is_always_lock_free,
              "Telemetry sequence counter must be lock-free.");

inline double clampd(double value, double low, double high) {
    return std::max(low, std::min(value, high));
}
inline double wrapPhase(double phase) {
    if (phase >= kTwoPi || phase < 0.0) {
        phase = std::fmod(phase, kTwoPi);
        if (phase < 0.0) phase += kTwoPi;
    }
    return phase;
}
inline double smoothStep(double x) { return x * x * (3.0 - 2.0 * x); }

class EngineAudio final : public oboe::AudioStreamDataCallback {
public:
    explicit EngineAudio(AAssetManager* assetManager) : assetManager_(assetManager) {}
    ~EngineAudio() override { close(); }

    bool open() {
        oboe::AudioStreamBuilder builder;
        builder.setDirection(oboe::Direction::Output);
        builder.setChannelCount(2);
        builder.setFormat(oboe::AudioFormat::I16);
        builder.setPerformanceMode(oboe::PerformanceMode::LowLatency);
        builder.setUsage(oboe::Usage::Game);
        builder.setDataCallback(this);
        builder.setSharingMode(oboe::SharingMode::Exclusive);

        oboe::Result result = builder.openStream(stream_);
        if (result != oboe::Result::OK) {
            stream_.reset();
            builder.setSharingMode(oboe::SharingMode::Shared);
            result = builder.openStream(stream_);
        }
        if (result != oboe::Result::OK || !stream_) return false;

        sampleRate_ = stream_->getSampleRate();
        if (sampleRate_ <= 0) {
            stream_->close();
            stream_.reset();
            return false;
        }
        dt_ = 1.0 / static_cast<double>(sampleRate_);
        // Decode bundled shift one-shots before starting the real-time callback. Missing
        // sample assets are non-fatal: the existing synthesised engine remains available.
        loadShiftSamples();
        loadReferenceLoop();
        configureFilters();
        const int32_t burst = stream_->getFramesPerBurst();
        if (burst > 0) stream_->setBufferSizeInFrames(burst * 2);

        result = stream_->requestStart();
        if (result != oboe::Result::OK) {
            stream_->close();
            stream_.reset();
            return false;
        }
        return true;
    }

    void close() {
        if (!stream_) return;
        stream_->requestStop();
        stream_->close();
        stream_.reset();
    }

    void setControls(float throttle, float brake) {
        throttleInput_.store(std::clamp(throttle, 0.0f, 1.0f), std::memory_order_relaxed);
        brakeInput_.store(std::clamp(brake, 0.0f, 1.0f), std::memory_order_relaxed);
    }

    void setTelemetry(int32_t rpm, int32_t speedKmh, int32_t gear,
                      int32_t wheelLocked, int32_t drsActive, int32_t ersPercent) {
        // One Kotlin producer, one audio consumer. The sequence counter lets the callback keep
        // the previous coherent frame rather than blocking/retrying if a write overlaps a read.
        telemetryVersion_.fetch_add(1, std::memory_order_acq_rel); // odd = writer active
        targetRpm_.store(std::clamp(rpm, 4000, 15000), std::memory_order_relaxed);
        targetSpeedKmh_.store(std::clamp(speedKmh, 0, 353), std::memory_order_relaxed);
        targetGear_.store(std::clamp(gear, 1, 8), std::memory_order_relaxed);
        targetWheelLocked_.store(wheelLocked != 0 ? 1 : 0, std::memory_order_relaxed);
        targetDrsActive_.store(drsActive != 0 ? 1 : 0, std::memory_order_relaxed);
        targetErsPercent_.store(std::clamp(ersPercent, 0, 100), std::memory_order_relaxed);
        telemetryVersion_.fetch_add(1, std::memory_order_release); // even = published
    }

    oboe::DataCallbackResult onAudioReady(oboe::AudioStream*, void* audioData,
                                          int32_t numFrames) override {
        if (audioData == nullptr || numFrames <= 0) return oboe::DataCallbackResult::Continue;
        auto* output = static_cast<int16_t*>(audioData);
        const float throttleTarget = throttleInput_.load(std::memory_order_relaxed);
        const float brakeTarget = brakeInput_.load(std::memory_order_relaxed);
        // Snapshot Kotlin-owned telemetry once per callback; the audio callback never calls JNI.
        // Never spin here: if a producer update overlaps this read, retain the previous snapshot.
        const uint32_t versionBefore = telemetryVersion_.load(std::memory_order_acquire);
        if ((versionBefore & 1u) == 0u) {
            const int32_t nextRpm = targetRpm_.load(std::memory_order_relaxed);
            const int32_t nextSpeed = targetSpeedKmh_.load(std::memory_order_relaxed);
            const int32_t nextGear = targetGear_.load(std::memory_order_relaxed);
            const int32_t nextWheelLocked = targetWheelLocked_.load(std::memory_order_relaxed);
            const int32_t nextDrsActive = targetDrsActive_.load(std::memory_order_relaxed);
            const int32_t nextErsPercent = targetErsPercent_.load(std::memory_order_relaxed);
            const uint32_t versionAfter = telemetryVersion_.load(std::memory_order_acquire);
            if (versionBefore == versionAfter && (versionAfter & 1u) == 0u) {
                targetRpmBlock_ = nextRpm;
                targetSpeedKmhBlock_ = nextSpeed;
                targetGearBlock_ = nextGear;
                targetWheelLockedBlock_ = nextWheelLocked;
                targetDrsActiveBlock_ = nextDrsActive;
                targetErsPercentBlock_ = nextErsPercent;
            }
        }

        for (int32_t frame = 0; frame < numFrames; ++frame) {
            float left = 0.0f, right = 0.0f;
            renderOne(throttleTarget, brakeTarget, left, right);
            // Final, unconditional hard clamp protects the output from overload.
            output[frame * 2] = static_cast<int16_t>(std::lrint(clampd(left, -1.0, 1.0) * 32767.0));
            output[frame * 2 + 1] = static_cast<int16_t>(std::lrint(clampd(right, -1.0, 1.0) * 32767.0));
        }
        return oboe::DataCallbackResult::Continue;
    }

private:
    struct BiquadBandPass {
        double b0 = 0.0, b1 = 0.0, b2 = 0.0, a1 = 0.0, a2 = 0.0;
        double x1 = 0.0, x2 = 0.0, y1 = 0.0, y2 = 0.0;
        void setup(double sr, double centerHz, double q) {
            const double omega = kTwoPi * centerHz / sr;
            const double alpha = std::sin(omega) / (2.0 * q);
            const double d = 1.0 + alpha;
            b0 = alpha / d; b1 = 0.0; b2 = -alpha / d;
            a1 = -2.0 * std::cos(omega) / d; a2 = (1.0 - alpha) / d;
            x1 = x2 = y1 = y2 = 0.0;
        }
        double process(double input) {
            const double out = b0 * input + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
            x2 = x1; x1 = input; y2 = y1; y1 = out;
            return out;
        }
    };

    struct MultibandCompressor {
        double lowSplitAlpha = 0.0, highSplitAlpha = 0.0;
        double lowAttack = 0.0, midAttack = 0.0, highAttack = 0.0;
        double lowRelease = 0.0, midRelease = 0.0, highRelease = 0.0;
        double lowSplit = 0.0, highSplit = 0.0;
        double lowEnvelope = 0.0, midEnvelope = 0.0, highEnvelope = 0.0;

        void setup(double sampleRate) {
            const double dt = 1.0 / sampleRate;
            lowSplitAlpha = 1.0 - std::exp(-kTwoPi * 230.0 * dt);
            highSplitAlpha = 1.0 - std::exp(-kTwoPi * 2700.0 * dt);
            lowAttack = 1.0 - std::exp(-dt / 0.006);
            midAttack = 1.0 - std::exp(-dt / 0.0035);
            highAttack = 1.0 - std::exp(-dt / 0.0015);
            lowRelease = 1.0 - std::exp(-dt / 0.115);
            midRelease = 1.0 - std::exp(-dt / 0.075);
            highRelease = 1.0 - std::exp(-dt / 0.045);
        }
        static double follow(double current, double input, double attack, double release) {
            const double coefficient = input > current ? attack : release;
            return current + coefficient * (input - current);
        }
        static double gainFor(double envelope, double threshold, double ratio) {
            if (envelope <= threshold || envelope <= 1.0e-9) return 1.0;
            const double compressed = threshold + (envelope - threshold) / ratio;
            return clampd(compressed / envelope, 0.12, 1.0);
        }
        double process(double input) {
            lowSplit += lowSplitAlpha * (input - lowSplit);
            highSplit += highSplitAlpha * (input - highSplit);
            const double low = lowSplit;
            const double mid = highSplit - lowSplit;
            const double high = input - highSplit;
            lowEnvelope = follow(lowEnvelope, std::abs(low), lowAttack, lowRelease);
            midEnvelope = follow(midEnvelope, std::abs(mid), midAttack, midRelease);
            highEnvelope = follow(highEnvelope, std::abs(high), highAttack, highRelease);
            return low * gainFor(lowEnvelope, 0.24, 4.0) +
                   mid * gainFor(midEnvelope, 0.20, 4.5) +
                   high * gainFor(highEnvelope, 0.14, 5.0);
        }
    };

    void configureFilters() {
        intakeLow_.setup(sampleRate_, 720.0, 2.2);
        intakeHigh_.setup(sampleRate_, std::min(1080.0, sampleRate_ * 0.20), 2.8);
        leftMasterCompressor_.setup(sampleRate_);
        rightMasterCompressor_.setup(sampleRate_);
        liftNoiseAlpha_ = 1.0 - std::exp(-kTwoPi * 1450.0 * dt_);
        turboLowAlpha_ = 1.0 - std::exp(-kTwoPi * std::min(6500.0, sampleRate_ * 0.18) * dt_);
        turboHighAlpha_ = 1.0 - std::exp(-kTwoPi * std::min(11500.0, sampleRate_ * 0.38) * dt_);
    }

    uint32_t nextRandom() {
        // Xorshift32: deterministic, constant-time, allocation-free noise for real-time DSP.
        randomState_ ^= randomState_ << 13;
        randomState_ ^= randomState_ >> 17;
        randomState_ ^= randomState_ << 5;
        return randomState_;
    }
    double noise() { return (static_cast<double>(nextRandom()) / 2147483648.0) - 1.0; }
    double sawVoice(double phase) const {
        double sum = 0.0;
        for (int harmonic = 1; harmonic <= 11; ++harmonic)
            sum += std::sin(phase * harmonic) / static_cast<double>(harmonic);
        return (2.0 / kPi) * sum;
    }

    void beginShift(int direction) {
        if (direction > 0) {
            upshiftCutRemaining_ = 0.040;
            upshiftClickRemaining_ = 0.0;
            downshiftBendRemaining_ = downshiftPopRemaining_ = 0.0;
        } else {
            downshiftBendRemaining_ = downshiftPopRemaining_ = 0.100;
            downshiftBendAge_ = downshiftPopAge_ = 0.0;
            upshiftCutRemaining_ = upshiftClickRemaining_ = 0.0;
        }
    }

    void renderOne(float throttleInput, float brakeInput, float& outLeft, float& outRight) {
        smoothedThrottle_ += (static_cast<double>(throttleInput) - smoothedThrottle_) * (1.0 - std::exp(-dt_ * 34.0));
        const double throttle = clampd(smoothedThrottle_, 0.0, 1.0);
        const double brake = clampd(static_cast<double>(brakeInput), 0.0, 1.0);
        const double throttleRise = std::max(0.0, throttle - previousThrottle_);
        const double throttleDrop = std::max(0.0, previousThrottle_ - throttle);

        // RPM/speed/gear/status are supplied by Kotlin. Smooth target changes at sample rate,
        // keeping audio continuous while preserving the Kotlin source's timestamped telemetry.
        const int incomingGear = std::clamp(targetGearBlock_, 1, 8);
        if (incomingGear != gear_) {
            triggerShiftSample(gear_, incomingGear);
            beginShift(incomingGear > gear_ ? 1 : -1);
            gear_ = incomingGear;
        }
        const double rpmTarget = clampd(static_cast<double>(targetRpmBlock_), kIdleRpm, kRedlineRpm);
        const double rpmSmoothing = rpmTarget > rpm_ ? 105.0 : 78.0;
        rpm_ += (rpmTarget - rpm_) * (1.0 - std::exp(-dt_ * rpmSmoothing));
        const double speedTargetMps = static_cast<double>(targetSpeedKmhBlock_) / 3.6;
        speedMps_ += (speedTargetMps - speedMps_) * (1.0 - std::exp(-dt_ * 72.0));
        speedMps_ = clampd(speedMps_, 0.0, 98.1);
        wheelLocked_ = targetWheelLockedBlock_ != 0;
        drsActive_ = targetDrsActiveBlock_ != 0;

        // Audio load must follow the driver's pedal, not whether RPM/speed happen to rise
        // during this one sample. The old test made a sustained high-RPM note fall to 30%
        // whenever the telemetry settled, even with the throttle still held down.
        const bool accelerating = throttle > 0.08 && brake < 0.95;
        const double normalizedSpeed = clampd(speedMps_ / 98.0, 0.0, 1.0);
        const double rpmBrightness = clampd((rpm_ - kIdleRpm) / (kRedlineRpm - kIdleRpm), 0.0, 1.0);
        const double turboTarget = clampd(throttle * 0.79 + rpm_ / kRedlineRpm * 0.21, 0.0, 1.0);
        const double turboGap = std::max(0.0, turboTarget - turboSpool_);
        const double mguHTarget = clampd(0.18 + throttle * 0.54 + rpm_ / kRedlineRpm * 0.22 + turboGap * 1.5 + throttleRise * 2.0, 0.0, 1.0);
        const double mguHResponse = mguHTarget > mguHAssist_ ? 12.0 : 3.2;
        mguHAssist_ += (mguHTarget - mguHAssist_) * (1.0 - std::exp(-mguHResponse * dt_));
        const double turboResponse = turboTarget > turboSpool_ ? 2.8 + 4.6 * mguHAssist_ : 0.82 + 0.70 * mguHAssist_;
        turboSpool_ += (turboTarget - turboSpool_) * (1.0 - std::exp(-turboResponse * dt_));

        // One six-cylinder four-stroke firing cycle per 120 crank degrees: base firing pitch RPM/20.
        loadPulsePhase_ = wrapPhase(loadPulsePhase_ + kTwoPi * (5.1 + 2.2 * throttle) * dt_);
        const double instantaneousRpm = rpm_;
        const double baseFiringHz = rpm_ / 20.0;
        const double downshiftBend = downshiftBendRemaining_ > 0.0
            ? 1.0 + 0.36 * std::exp(-downshiftBendAge_ / 0.028) : 1.0;
        const double firingHz = baseFiringHz * (1.0 + clampd(noise(), -1.0, 1.0) * 0.005) * downshiftBend;
        const double oldEnginePhase = enginePhase_;
        enginePhase_ = wrapPhase(enginePhase_ + kTwoPi * firingHz * dt_);
        if (enginePhase_ < oldEnginePhase) {
            combustionAttackEnvelope_ = std::max(combustionAttackEnvelope_, (0.58 + 0.42 * throttle) * (0.55 + 0.45 * rpmBrightness));
            exhaustPressureEnvelope_ = std::max(exhaustPressureEnvelope_, 0.62 + 0.38 * throttle);
            metallicRingEnvelope_ = std::max(metallicRingEnvelope_, 0.72 + 0.20 * throttle);
        }
        combustionAttackEnvelope_ *= std::exp(-dt_ * (930.0 + 260.0 * rpmBrightness));
        exhaustPressureEnvelope_ *= std::exp(-dt_ * (255.0 + 105.0 * rpmBrightness));
        metallicRingEnvelope_ *= std::exp(-dt_ * 24.0);
        crankPhase_ = wrapPhase(crankPhase_ + kTwoPi * (instantaneousRpm / 60.0) * downshiftBend * dt_);
        const double lowGearFrequency = std::max(38.0, instantaneousRpm / 60.0 * 0.78 * downshiftBend);
        lowGearPhase_ = wrapPhase(lowGearPhase_ + kTwoPi * lowGearFrequency * dt_);

        const double phaseWobble = std::sin(crankPhase_ * 2.0 + 0.31) * (0.008 + 0.018 * throttle) +
                std::sin(loadPulsePhase_ * 1.7) * (0.004 + 0.010 * throttle);
        const double p = enginePhase_ + phaseWobble;
        const double h1 = std::sin(p), h2 = std::sin(p * 2.0 + 0.15), h3 = std::sin(p * 3.0 + 0.42);
        const double h4 = std::sin(p * 4.0 + 0.71), h5 = std::sin(p * 5.0 + 1.05), h6 = std::sin(p * 6.0 + 1.32);
        const double h7 = std::sin(p * 7.0 + 1.53), h8 = std::sin(p * 8.0 + 1.77), h9 = std::sin(p * 9.0 + 1.96);
        const double h10 = std::sin(p * 10.0 + 2.13), h11 = std::sin(p * 11.0 + 2.29), h12 = std::sin(p * 12.0 + 2.46);
        const double loadPulse = 0.94 + 0.06 * std::sin(loadPulsePhase_) * (0.35 + 0.65 * throttle);
        const double brightness = clampd(0.40 + 0.48 * throttle + 0.27 * rpmBrightness + 0.055 * std::sin(loadPulsePhase_ * 0.73), 0.0, 1.18);
        const double combustionCore = (h1 * 0.34 + h2 * 0.245 + h3 * 0.172 + h4 * 0.112 + h5 * 0.078 + h6 * 0.057 +
                h7 * (0.041 * brightness) + h8 * (0.032 * brightness) + h9 * (0.024 * brightness) + h10 * (0.019 * brightness) +
                h11 * (0.014 * brightness) + h12 * (0.010 * brightness)) * loadPulse;

        constexpr std::array<double, 10> ratios{0.995,0.996,0.997,0.998,0.999,1.001,1.002,1.003,1.004,1.005};
        constexpr std::array<double, 10> wobbleWeights{0.72,-0.61,0.44,-0.38,0.57,-0.49,0.31,-0.24,0.63,-0.53};
        constexpr std::array<double, 10> leftWeights{0.18,0.15,0.13,0.11,0.09,0.075,0.06,0.045,0.03,0.02};
        constexpr std::array<double, 10> rightWeights{0.02,0.03,0.045,0.06,0.075,0.09,0.11,0.13,0.15,0.18};
        double unisonLeft = 0.0, unisonRight = 0.0;
        for (size_t i = 0; i < unisonPhase_.size(); ++i) {
            unisonPhase_[i] = wrapPhase(unisonPhase_[i] + kTwoPi * firingHz * ratios[i] * dt_);
            const double saw = sawVoice(unisonPhase_[i] + phaseWobble * wobbleWeights[i]);
            unisonLeft += saw * leftWeights[i];
            unisonRight += saw * rightWeights[i];
        }
        const double centreSaw = sawVoice(p) * 0.10;
        double combustionLeft = combustionCore * 0.40 + unisonLeft * 1.16 + centreSaw;
        double combustionRight = combustionCore * 0.40 + unisonRight * 1.16 + centreSaw;

        const double lowGearAmount = gear_ == 1 ? 1.0 : gear_ == 2 ? 0.82 : gear_ == 3 ? 0.53 : gear_ == 4 ? 0.19 : 0.035;
        const double lowRaw = std::sin(lowGearPhase_) + std::sin(lowGearPhase_ * 3.0 + 0.11) * 0.46 +
                std::sin(lowGearPhase_ * 5.0 + 0.29) * 0.15 + std::sin(lowGearPhase_ * 2.0 + 0.37) * 0.075;
        const double lowGearLayer = std::tanh(lowRaw * 2.35) * 0.58 * lowGearAmount *
                (0.055 + 0.115 * throttle) * (0.72 + 0.28 * (1.0 - rpmBrightness));

        const double metallicFreqA = 310.0 + firingHz * 0.62 + 75.0 * turboSpool_;
        const double metallicFreqB = 570.0 + firingHz * 1.07 + 110.0 * turboSpool_;
        const double metallicFreqC = 880.0 + firingHz * 1.57 + 155.0 * turboSpool_;
        const double metallicFreqD = 1320.0 + firingHz * 2.18 + 210.0 * turboSpool_;
        metallicPhaseA_ = wrapPhase(metallicPhaseA_ + kTwoPi * metallicFreqA * dt_);
        metallicPhaseB_ = wrapPhase(metallicPhaseB_ + kTwoPi * metallicFreqB * dt_);
        metallicPhaseC_ = wrapPhase(metallicPhaseC_ + kTwoPi * metallicFreqC * dt_);
        metallicPhaseD_ = wrapPhase(metallicPhaseD_ + kTwoPi * metallicFreqD * dt_);
        const double metallicShape = std::sin(metallicPhaseA_ + std::sin(crankPhase_) * 0.11) * 0.62 +
                std::sin(metallicPhaseB_ + std::sin(exhaustFlutterPhase_) * 0.14 + 0.23) * 0.34 +
                std::sin(metallicPhaseC_ + 0.61) * 0.17 + std::sin(metallicPhaseD_ + 1.07) * 0.075;
        const double metallicLevel = (0.018 + 0.048 * throttle + 0.016 * rpmBrightness) *
                (0.35 + 0.65 * turboSpool_) * (0.50 + 0.50 * metallicRingEnvelope_);
        const double metallicLayer = std::tanh(metallicShape * 1.18) * metallicLevel;

        const double exhaustTone = std::sin(crankPhase_) * 0.210 + std::sin(crankPhase_ * 2.0 + 0.8) * 0.085 + std::sin(crankPhase_ * 3.0 + 0.24) * 0.035;
        const double exhaustFlutterHz = 2.2 + throttle * 5.4 + rpmBrightness * 2.0;
        exhaustFlutterPhase_ = wrapPhase(exhaustFlutterPhase_ + kTwoPi * exhaustFlutterHz * dt_);
        const double exhaustModeFrequency = 640.0 + 0.105 * instantaneousRpm + 250.0 * turboSpool_ + 18.0 * std::sin(exhaustFlutterPhase_);
        exhaustModePhase_ = wrapPhase(exhaustModePhase_ + kTwoPi * exhaustModeFrequency * dt_);
        const double exhaustMode = std::sin(exhaustModePhase_ + std::sin(crankPhase_) * (0.08 + 0.08 * throttle)) *
                (0.015 + 0.040 * throttle) * (0.48 + 0.52 * rpmBrightness) * (0.70 + 0.30 * std::sin(exhaustFlutterPhase_));
        const double exhaustPulseBloom = std::sin(exhaustModePhase_ + std::sin(crankPhase_) * 0.12) *
                exhaustPressureEnvelope_ * (0.018 + 0.052 * throttle) * (0.36 + 0.64 * rpmBrightness);

        const double engineNormRpm = clampd((instantaneousRpm - kIdleRpm) / (kRedlineRpm - kIdleRpm), 0.0, 1.0);
        const double intakeFrequency = 600.0 + 600.0 * engineNormRpm;
        intakePlenumPhase_ = wrapPhase(intakePlenumPhase_ + kTwoPi * intakeFrequency * dt_);
        const double intakeSaw = sawVoice(intakePlenumPhase_ + std::sin(crankPhase_) * 0.025) * 0.78 +
                std::sin(intakePlenumPhase_ * 2.0 + 0.24) * 0.10 + std::sin(intakePlenumPhase_ * 3.0 + 0.57) * 0.045;
        const double intakePlenum = (intakeLow_.process(intakeSaw) * 0.82 + intakeHigh_.process(intakeSaw) * 0.54) *
                (0.030 + 0.085 * throttle) * (0.45 + 0.55 * rpmBrightness);

        const double speedKmh = speedMps_ * 3.6;
        const double speedNorm = clampd(speedKmh / 350.0, 0.0, 1.0);
        const double gearboxFrequency = 3500.0 * speedNorm;
        gearboxPhase_ = wrapPhase(gearboxPhase_ + kTwoPi * gearboxFrequency * dt_);
        const double gearboxWhine = std::sin(gearboxPhase_) * 0.082 * speedNorm;

        // Turbo and airflow DSP ported from the Kotlin model, now sample-generated in C++.
        const double nyquistSafe = std::max(1000.0, sampleRate_ * 0.44);
        const double white = noise();
        if (throttleDrop > 0.025) {
            turboLiftFlutterEnvelope_ = std::max(turboLiftFlutterEnvelope_, std::min(1.0, throttleDrop * 2.0));
        }
        turboLiftFlutterEnvelope_ *= std::exp(-dt_ * 28.0);
        turboLiftFlutterPhase_ = wrapPhase(turboLiftFlutterPhase_ + kTwoPi * (34.0 + 30.0 * (1.0 - throttle)) * dt_);

        const double turboFrequency = 330.0 + 2150.0 * std::pow(turboSpool_, 0.82) + 180.0 * normalizedSpeed +
                42.0 * mguHAssist_ + 24.0 * std::sin(exhaustFlutterPhase_ * 0.63);
        turboPhase_ = wrapPhase(turboPhase_ + kTwoPi * turboFrequency * dt_);
        turboHarmonicPhase_ = wrapPhase(turboHarmonicPhase_ + kTwoPi * turboFrequency * 2.17 * dt_);
        const double turboLevel = clampd(0.06 + 0.76 * turboSpool_ + 0.10 * mguHAssist_, 0.0, 0.95);
        const double turboTone = (std::sin(turboPhase_) * 0.105 +
                std::sin(turboHarmonicPhase_ + std::sin(crankPhase_) * 0.045) * 0.028) * turboLevel;

        const double turboHighFrequencyRequested = accelerating
            ? clampd(8000.0 + 6000.0 * throttle + std::sin(turboLiftFlutterPhase_) * turboLiftFlutterEnvelope_ * 210.0, 7800.0, 14200.0)
            : clampd(12000.0 + std::sin(turboLiftFlutterPhase_) * (520.0 + 380.0 * turboSpool_), 10800.0, 13200.0);
        const double turboHighFrequency = std::min(turboHighFrequencyRequested, sampleRate_ * 0.45);
        turboHighPhase_ = wrapPhase(turboHighPhase_ + kTwoPi * turboHighFrequency * dt_);
        const double turboHighWave = (2.0 / kPi) * std::asin(std::sin(turboHighPhase_));
        const double turboHighLevel = accelerating
            ? (0.004 + 0.030 * turboSpool_ + 0.018 * throttle) *
                (1.0 + turboLiftFlutterEnvelope_ * 0.40 * std::sin(turboLiftFlutterPhase_))
            : (0.014 + 0.050 * turboSpool_ + 0.012 * turboLiftFlutterEnvelope_) *
                (0.70 + 0.30 * std::sin(turboLiftFlutterPhase_));

        const double turboNoiseCentre = clampd(8000.0 + 5400.0 * throttle, 8000.0, 13400.0);
        const double turboNoiseLowCut = std::max(5500.0, turboNoiseCentre - 1700.0);
        const double turboNoiseHighCut = std::min(sampleRate_ * 0.44, turboNoiseCentre + 2000.0);
        turboLowAlpha_ = 1.0 - std::exp(-kTwoPi * turboNoiseLowCut * dt_);
        turboHighAlpha_ = 1.0 - std::exp(-kTwoPi * turboNoiseHighCut * dt_);
        turboNoiseLowState_ += turboLowAlpha_ * (white - turboNoiseLowState_);
        const double turboHighPassed = white - turboNoiseLowState_;
        turboNoiseBandState_ += turboHighAlpha_ * (turboHighPassed - turboNoiseBandState_);
        const double turboNoiseBurst = turboNoiseBandState_ *
                (0.012 + 0.060 * throttle + 0.032 * turboSpool_) * (0.38 + 0.62 * turboSpool_) *
                (1.0 + 0.45 * turboLiftFlutterEnvelope_ * std::sin(turboLiftFlutterPhase_));

        liftNoiseState_ += liftNoiseAlpha_ * (white - liftNoiseState_);
        filteredNoise_ = filteredNoise_ * 0.88 + white * 0.12;
        intakeNoiseState_ = intakeNoiseState_ * 0.965 + white * 0.035;
        roughNoiseState_ = roughNoiseState_ * 0.72 + white * 0.28;
        const double exhaustNoise = (white - filteredNoise_) * (0.012 + throttle * 0.060) +
                roughNoiseState_ * (0.006 + 0.018 * throttle) * (0.35 + rpmBrightness);
        const double intakeRush = (white - intakeNoiseState_) * (0.004 + throttle * 0.034) * (0.35 + 0.65 * turboSpool_);
        const double liftAmount = throttleDrop;
        const double exhaustGasTurbulence = liftNoiseState_ * (accelerating ? 0.0 : 0.050 + 0.085 * (1.0 - throttle)) *
                (0.45 + 0.55 * turboSpool_);

        const double ersFraction = clampd(static_cast<double>(targetErsPercentBlock_) / 100.0, 0.0, 1.0);
        const double mgukDeploymentLevel = clampd((throttle - 0.12) / 0.88, 0.0, 1.0) * ersFraction * (1.0 - brake);
        const double regenRequest = std::max(brake * (wheelLocked_ ? 0.22 : 0.85), (1.0 - throttle) * 0.42);
        const double regenLevel = regenRequest * clampd(speedMps_ / 38.0, 0.0, 1.0);
        const double motorFrequency = std::min(nyquistSafe, 510.0 + 12.0 * speedMps_ + 260.0 * regenLevel + 170.0 * mgukDeploymentLevel);
        motorPhase_ = wrapPhase(motorPhase_ + kTwoPi * motorFrequency * dt_);
        const double motorTone = std::sin(motorPhase_) * (regenLevel * 0.045 + mgukDeploymentLevel * 0.034);
        mguHeterodynePhaseA_ = wrapPhase(mguHeterodynePhaseA_ + kTwoPi * std::min(10200.0 + 1000.0 * mgukDeploymentLevel, sampleRate_ * 0.45) * dt_);
        mguHeterodynePhaseB_ = wrapPhase(mguHeterodynePhaseB_ + kTwoPi * std::min(8500.0 + 300.0 * regenLevel, sampleRate_ * 0.45) * dt_);
        mguHeterodyneLfoPhase_ = wrapPhase(mguHeterodyneLfoPhase_ + kTwoPi * (7.0 + 5.0 * regenLevel) * dt_);
        const double mguHeterodyne = std::sin(mguHeterodynePhaseA_) * std::sin(mguHeterodynePhaseB_) *
                (0.006 + 0.042 * regenLevel + 0.030 * mgukDeploymentLevel) *
                (0.84 + 0.16 * std::sin(mguHeterodyneLfoPhase_));

        if (liftAmount > 0.025) shiftWhooshEnvelope_ = std::max(shiftWhooshEnvelope_, std::min(0.8, liftAmount * 1.8));
        if (throttleRise > 0.045) shiftWhooshEnvelope_ = std::max(shiftWhooshEnvelope_, std::min(0.65, throttleRise * 1.5));
        shiftWhooshEnvelope_ *= std::exp(-dt_ * 4.5);
        const double airflowWhoosh = (white - intakeNoiseState_) * shiftWhooshEnvelope_ * (0.045 + 0.055 * normalizedSpeed);
        const double inductionLayer = turboTone + turboHighWave * turboHighLevel + turboNoiseBurst +
                exhaustNoise + intakeRush + airflowWhoosh + exhaustGasTurbulence + motorTone + mguHeterodyne;

        limiterPhase_ = wrapPhase(limiterPhase_ + kTwoPi * 19.0 * dt_);
        const bool limiterActive = rpm_ > 14650.0 && throttle > 0.82 && std::sin(limiterPhase_) > 0.42;
        const double limiterGain = limiterActive ? 0.32 : 1.0;
        const double loadGain = (0.30 + 0.82 * std::pow(throttle, 0.72)) * (0.95 + 0.05 * std::sin(loadPulsePhase_ * 0.91) * (0.20 + 0.80 * throttle));
        const double firingAttack = (h3 * 0.050 + h5 * 0.042 + h7 * 0.030 + h9 * 0.019 + (white - filteredNoise_) * 0.075) *
                combustionAttackEnvelope_ * (0.28 + 0.72 * throttle) * (0.35 + 0.65 * rpmBrightness);

        const double engineRawLeft = combustionLeft + intakePlenum + exhaustTone + exhaustMode + exhaustPulseBloom + lowGearLayer + metallicLayer + firingAttack;
        const double engineRawRight = combustionRight + intakePlenum + exhaustTone + exhaustMode + exhaustPulseBloom + lowGearLayer + metallicLayer + firingAttack;
        // Crossfade between a quieter overrun sound and the loaded engine sound based on
        // throttle position. This stays fully loaded at steady RPM, and lifts smoothly rather
        // than switching abruptly when an RPM derivative reaches zero.
        const double loadedBlend = clampd((throttle - 0.015) / 0.20, 0.0, 1.0);
        const double drivenLeft = std::tanh(engineRawLeft * 2.35) * 1.22;
        const double drivenRight = std::tanh(engineRawRight * 2.35) * 1.22;
        const double coastLeft = engineRawLeft * 0.30;
        const double coastRight = engineRawRight * 0.30;
        double engineLeft = (coastLeft + (drivenLeft - coastLeft) * loadedBlend) * loadGain * limiterGain;
        double engineRight = (coastRight + (drivenRight - coastRight) * loadedBlend) * loadGain * limiterGain;

        const double click = upshiftClickRemaining_ > 0.0 ?
            (0.76 * noise() + 0.42 * std::sin(gearClickPhase_)) * std::pow(clampd(upshiftClickRemaining_ / 0.005, 0.0, 1.0), 2.0) : 0.0;
        gearClickPhase_ = wrapPhase(gearClickPhase_ + kTwoPi * 3800.0 * dt_);
        const double downshiftPop = downshiftPopRemaining_ > 0.0 ?
            (noise() * 2.20 + std::sin(downshiftPopPhase_) * 0.85) * std::exp(-downshiftPopAge_ / 0.0018) : 0.0;
        downshiftPopPhase_ = wrapPhase(downshiftPopPhase_ + kTwoPi * (1450.0 + downshiftPopAge_ * 32000.0) * dt_);

        // A slightly wider high-speed presentation with the same signal centre in both channels.
        const double stereoAir = std::sin(gearboxPhase_ * 0.5 + 0.3) * speedNorm * 0.003;
        const double busLeft = engineLeft + gearboxWhine + inductionLayer + click + downshiftPop - stereoAir;
        const double busRight = engineRight + gearboxWhine + inductionLayer + click + downshiftPop + stereoAir;
        const bool cutActive = upshiftCutRemaining_ > 0.0;
        const double sampleLeft = cutActive ? 0.0 : busLeft;
        const double sampleRight = cutActive ? 0.0 : busRight;
        // Mild output saturation plus a final hard clip in onAudioReady().
        outLeft = static_cast<float>(leftMasterCompressor_.process(sampleLeft) * 1.62);
        outRight = static_cast<float>(rightMasterCompressor_.process(sampleRight) * 1.62);
        // At matching high-speed operating points, fade toward a real stereo RB21 onboard
        // segment rather than forcing the synthesizer to carry the whole timbre. The loop is
        // a 5.2 s eighth-gear section from the supplied Monza recording, crossfaded at its seam.
        mixReferenceLoop(outLeft, outRight, throttle, brake);
        mixCornerExitLoop(outLeft, outRight, throttle, brake);
        // Brief recorded gear-change transients stay above the continuous bed.
        mixShiftSample(outLeft, outRight);

        if (upshiftCutRemaining_ > 0.0) {
            upshiftCutRemaining_ = std::max(0.0, upshiftCutRemaining_ - dt_);
            if (upshiftCutRemaining_ == 0.0) upshiftClickRemaining_ = 0.005;
        } else if (upshiftClickRemaining_ > 0.0) upshiftClickRemaining_ = std::max(0.0, upshiftClickRemaining_ - dt_);
        if (downshiftBendRemaining_ > 0.0) {
            downshiftBendRemaining_ = std::max(0.0, downshiftBendRemaining_ - dt_); downshiftBendAge_ += dt_;
        }
        if (downshiftPopRemaining_ > 0.0) {
            downshiftPopRemaining_ = std::max(0.0, downshiftPopRemaining_ - dt_); downshiftPopAge_ += dt_;
        }
        previousThrottle_ = throttle;
    }


    struct ShiftSample {
        std::vector<int16_t> pcm;
        int sampleRate = 48000;
        int channels = 2;
        size_t leadInFrames = 0;
    };

    static uint16_t readLe16(const uint8_t* p) {
        return static_cast<uint16_t>(p[0]) |
               static_cast<uint16_t>(static_cast<uint16_t>(p[1]) << 8);
    }
    static uint32_t readLe32(const uint8_t* p) {
        return static_cast<uint32_t>(p[0]) |
               (static_cast<uint32_t>(p[1]) << 8) |
               (static_cast<uint32_t>(p[2]) << 16) |
               (static_cast<uint32_t>(p[3]) << 24);
    }
    static int gearPairKey(int from, int to) { return from * 16 + to; }

    bool readPcm16WavAsset(const std::string& filename, ShiftSample& sample) {
        if (assetManager_ == nullptr) return false;
        const std::string path = "sample-bank/" + filename;
        AAsset* asset = AAssetManager_open(assetManager_, path.c_str(), AASSET_MODE_BUFFER);
        if (asset == nullptr) return false;
        const off_t length = AAsset_getLength(asset);
        if (length < 44 || static_cast<uint64_t>(length) > 64u * 1024u * 1024u) {
            AAsset_close(asset);
            return false;
        }
        std::vector<uint8_t> bytes(static_cast<size_t>(length));
        size_t readTotal = 0;
        while (readTotal < bytes.size()) {
            const int count = AAsset_read(asset, bytes.data() + readTotal, bytes.size() - readTotal);
            if (count <= 0) break;
            readTotal += static_cast<size_t>(count);
        }
        AAsset_close(asset);
        if (readTotal != bytes.size() || std::memcmp(bytes.data(), "RIFF", 4) != 0 ||
            std::memcmp(bytes.data() + 8, "WAVE", 4) != 0) return false;

        uint16_t formatTag = 0, channels = 0, bitsPerSample = 0;
        uint32_t sampleRate = 0;
        size_t dataOffset = 0, dataSize = 0;
        size_t offset = 12;
        while (offset + 8 <= bytes.size()) {
            const uint8_t* chunk = bytes.data() + offset;
            const uint32_t chunkSize32 = readLe32(chunk + 4);
            const size_t chunkBody = offset + 8;
            const size_t chunkSize = static_cast<size_t>(chunkSize32);
            if (chunkBody > bytes.size() || chunkSize > bytes.size() - chunkBody) break;
            if (std::memcmp(chunk, "fmt ", 4) == 0 && chunkSize >= 16) {
                formatTag = readLe16(bytes.data() + chunkBody);
                channels = readLe16(bytes.data() + chunkBody + 2);
                sampleRate = readLe32(bytes.data() + chunkBody + 4);
                bitsPerSample = readLe16(bytes.data() + chunkBody + 14);
            } else if (std::memcmp(chunk, "data", 4) == 0) {
                dataOffset = chunkBody;
                dataSize = chunkSize;
            }
            offset = chunkBody + chunkSize + (chunkSize & 1u);
        }
        // The bundler writes ordinary little-endian PCM16 WAVs at 48 kHz.
        if (formatTag != 1 || (channels != 1 && channels != 2) || bitsPerSample != 16 ||
            sampleRate < 8000 || sampleRate > 192000 || dataOffset == 0 || dataSize < channels * 2 ||
            (dataSize % (channels * 2)) != 0) return false;
        sample.pcm.resize(dataSize / sizeof(int16_t));
        std::memcpy(sample.pcm.data(), bytes.data() + dataOffset, dataSize);
        sample.sampleRate = static_cast<int>(sampleRate);
        sample.channels = static_cast<int>(channels);

        // Filename form: shift_up_3_4_baku_004_p035.wav. The pNNN suffix is the
        // clip's pre-transient lead-in in milliseconds; start playback at that point
        // so the recorded transient aligns with the detected gear-change moment.
        const size_t marker = filename.rfind("_p");
        if (marker != std::string::npos && marker + 2 < filename.size()) {
            size_t end = marker + 2;
            while (end < filename.size() && filename[end] >= '0' && filename[end] <= '9') ++end;
            try {
                const int leadMs = std::stoi(filename.substr(marker + 2, end - (marker + 2)));
                const size_t totalFrames = sample.pcm.size() / static_cast<size_t>(sample.channels);
                sample.leadInFrames = std::min(totalFrames > 1 ? totalFrames - 1 : size_t{0},
                    static_cast<size_t>(std::max(0, leadMs)) * static_cast<size_t>(sample.sampleRate) / 1000u);
            } catch (...) { sample.leadInFrames = 0; }
        }
        return true;
    }

    void loadShiftSamples() {
        if (assetManager_ == nullptr) return;
        AAssetDir* dir = AAssetManager_openDir(assetManager_, "sample-bank");
        if (dir == nullptr) return;
        std::vector<std::string> filenames;
        const char* next = nullptr;
        while ((next = AAssetDir_getNextFileName(dir)) != nullptr) {
            std::string name(next);
            if (name.rfind("shift_", 0) == 0 && name.size() > 4 &&
                name.compare(name.size() - 4, 4, ".wav") == 0) filenames.push_back(std::move(name));
        }
        AAssetDir_close(dir);
        std::sort(filenames.begin(), filenames.end());
        for (const std::string& filename : filenames) {
            // We only bundle filenames that carry a direct gear pair at positions 2 and 3.
            std::vector<std::string> parts;
            size_t start = 0;
            for (size_t i = 0; i <= filename.size(); ++i) {
                if (i == filename.size() || filename[i] == '_') {
                    parts.push_back(filename.substr(start, i - start));
                    start = i + 1;
                }
            }
            if (parts.size() < 7 || parts[0] != "shift" ||
                (parts[1] != "up" && parts[1] != "down")) continue;
            int from = 0, to = 0;
            try { from = std::stoi(parts[2]); to = std::stoi(parts[3]); }
            catch (...) { continue; }
            if (from < 1 || from > 8 || to < 1 || to > 8 || from == to) continue;
            if ((parts[1] == "up") != (to > from)) continue;
            ShiftSample sample;
            if (!readPcm16WavAsset(filename, sample)) continue;
            const int key = gearPairKey(from, to);
            shiftSamples_[key].push_back(std::move(sample));
            shiftSampleCursors_.emplace(key, 0u);
        }
    }

    void loadReferenceLoop() {
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

    void triggerShiftSample(int from, int to) {
        activeShiftSample_ = nullptr;
        activeShiftPosition_ = 0.0;
        const auto found = shiftSamples_.find(gearPairKey(from, to));
        if (found == shiftSamples_.end() || found->second.empty()) return;
        auto cursor = shiftSampleCursors_.find(gearPairKey(from, to));
        size_t index = 0;
        if (cursor != shiftSampleCursors_.end()) {
            index = cursor->second % found->second.size();
            cursor->second = (cursor->second + 1u) % found->second.size();
        }
        activeShiftSample_ = &found->second[index];
        activeShiftPosition_ = static_cast<double>(activeShiftSample_->leadInFrames);
    }

    void mixShiftSample(float& left, float& right) {
        const ShiftSample* sample = activeShiftSample_;
        if (sample == nullptr || sample->channels < 1 || sample->sampleRate <= 0) return;
        const size_t frames = sample->pcm.size() / static_cast<size_t>(sample->channels);
        const size_t index = static_cast<size_t>(activeShiftPosition_);
        if (index + 1 >= frames) { activeShiftSample_ = nullptr; return; }
        const double fraction = activeShiftPosition_ - static_cast<double>(index);
        auto getChannel = [&](size_t frame, int channel) -> double {
            const int c = sample->channels == 1 ? 0 : channel;
            return static_cast<double>(sample->pcm[frame * static_cast<size_t>(sample->channels) + static_cast<size_t>(c)]) / 32768.0;
        };
        const double l0 = getChannel(index, 0), l1 = getChannel(index + 1, 0);
        const double r0 = getChannel(index, 1), r1 = getChannel(index + 1, 1);
        constexpr double kShiftSampleGain = 0.18; // conservative: clips contain the full onboard mix
        left += static_cast<float>((l0 + (l1 - l0) * fraction) * kShiftSampleGain);
        right += static_cast<float>((r0 + (r1 - r0) * fraction) * kShiftSampleGain);
        activeShiftPosition_ += static_cast<double>(sample->sampleRate) / static_cast<double>(sampleRate_);
        if (activeShiftPosition_ + 1.0 >= static_cast<double>(frames)) activeShiftSample_ = nullptr;
    }

    static constexpr double kIdleRpm = 4000.0;
    static constexpr double kRedlineRpm = 15000.0;
    static constexpr std::array<double, 8> kGearRatios{14.0,11.35,9.35,7.40,6.20,5.40,4.87,4.42};

    AAssetManager* assetManager_ = nullptr;
    std::unordered_map<int, std::vector<ShiftSample>> shiftSamples_;
    std::unordered_map<int, size_t> shiftSampleCursors_;
    ShiftSample referenceHighSpeedLoop_;
    ShiftSample referenceCornerExitLoop_;
    double referenceLoopPosition_ = 0.0;
    double referenceLoopGain_ = 0.0;
    double referenceCornerExitPosition_ = 0.0;
    double referenceCornerExitGain_ = 0.0;
    const ShiftSample* activeShiftSample_ = nullptr; // immutable after stream start
    double activeShiftPosition_ = 0.0;
    std::shared_ptr<oboe::AudioStream> stream_;
    int32_t sampleRate_ = 0;
    double dt_ = 1.0 / 48000.0;
    std::atomic<float> throttleInput_{0.0f}, brakeInput_{0.0f};
    std::atomic<uint32_t> telemetryVersion_{0};
    std::atomic<int32_t> targetRpm_{4000}, targetSpeedKmh_{0}, targetGear_{1};
    std::atomic<int32_t> targetWheelLocked_{0}, targetDrsActive_{0}, targetErsPercent_{55};
    // Only the Oboe callback reads these snapshots; producer-side changes are copied per callback.
    int32_t targetRpmBlock_ = 4000, targetSpeedKmhBlock_ = 0, targetGearBlock_ = 1;
    int32_t targetWheelLockedBlock_ = 0, targetDrsActiveBlock_ = 0, targetErsPercentBlock_ = 55;

    // Smoothed audio state; written only from the Oboe callback thread.
    double rpm_ = 4000.0, speedMps_ = 0.0;
    int gear_ = 1;
    bool wheelLocked_ = false, drsActive_ = false;
    double smoothedThrottle_ = 0.0, previousThrottle_ = 0.0, turboSpool_ = 0.0, mguHAssist_ = 0.0;

    // Persistent oscillator/filter state; all allocated once with the audio engine.
    uint32_t randomState_ = 0x13579BDFu;
    double enginePhase_ = 0.0, crankPhase_ = 0.0, lowGearPhase_ = 0.0, intakePlenumPhase_ = 0.0;
    double gearboxPhase_ = 0.0, turboPhase_ = 0.0, turboHarmonicPhase_ = 0.0;
    double turboHighPhase_ = 0.0, turboLiftFlutterPhase_ = 0.0, turboLiftFlutterEnvelope_ = 0.0;
    double metallicPhaseA_ = 0.0, metallicPhaseB_ = 1.31, metallicPhaseC_ = 2.17, metallicPhaseD_ = 0.74;
    double exhaustFlutterPhase_ = 0.0, exhaustModePhase_ = 0.0, loadPulsePhase_ = 0.0;
    double motorPhase_ = 0.0, mguHeterodynePhaseA_ = 0.0, mguHeterodynePhaseB_ = 0.0, mguHeterodyneLfoPhase_ = 0.0;
    double limiterPhase_ = 0.0, gearClickPhase_ = 0.0, downshiftPopPhase_ = 0.0;
    double combustionAttackEnvelope_ = 0.0, exhaustPressureEnvelope_ = 0.0, metallicRingEnvelope_ = 0.0;
    double upshiftCutRemaining_ = 0.0, upshiftClickRemaining_ = 0.0, downshiftBendRemaining_ = 0.0, downshiftBendAge_ = 0.0;
    double downshiftPopRemaining_ = 0.0, downshiftPopAge_ = 0.0, shiftWhooshEnvelope_ = 0.0;
    double liftNoiseState_ = 0.0, turboNoiseLowState_ = 0.0, turboNoiseBandState_ = 0.0;
    double intakeNoiseState_ = 0.0, filteredNoise_ = 0.0, roughNoiseState_ = 0.0;
    double liftNoiseAlpha_ = 0.0, turboLowAlpha_ = 0.0, turboHighAlpha_ = 0.0;
    BiquadBandPass intakeLow_, intakeHigh_;
    MultibandCompressor leftMasterCompressor_, rightMasterCompressor_;
    std::array<double, 10> unisonPhase_{0.49,2.91,1.07,4.22,2.31,5.45,0.38,3.63,1.66,5.20};
};

EngineAudio* fromHandle(jlong handle) {
    return reinterpret_cast<EngineAudio*>(static_cast<intptr_t>(handle));
}
} // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_sf12_f1soundlab_audio_F1AudioEngine_nativeOpen(JNIEnv* env, jobject, jobject assetManagerObject) {
    AAssetManager* assetManager = AAssetManager_fromJava(env, assetManagerObject);
    auto* engine = new (std::nothrow) EngineAudio(assetManager);
    if (engine == nullptr) return 0;
    if (!engine->open()) { delete engine; return 0; }
    return static_cast<jlong>(reinterpret_cast<intptr_t>(engine));
}

extern "C" JNIEXPORT void JNICALL
Java_com_sf12_f1soundlab_audio_F1AudioEngine_nativeSetControls(JNIEnv*, jobject, jlong handle,
                                                                jfloat throttle, jfloat brake) {
    EngineAudio* engine = fromHandle(handle);
    if (engine != nullptr) engine->setControls(throttle, brake);
}

extern "C" JNIEXPORT void JNICALL
Java_com_sf12_f1soundlab_audio_F1AudioEngine_nativeSetTelemetry(JNIEnv*, jobject, jlong handle,
        jint rpm, jint speedKmh, jint gear, jboolean wheelLocked, jboolean drsActive, jint ersPercent) {
    EngineAudio* engine = fromHandle(handle);
    if (engine != nullptr) {
        engine->setTelemetry(rpm, speedKmh, gear, wheelLocked == JNI_TRUE ? 1 : 0,
                             drsActive == JNI_TRUE ? 1 : 0, ersPercent);
    }
}

extern "C" JNIEXPORT void JNICALL
Java_com_sf12_f1soundlab_audio_F1AudioEngine_nativeClose(JNIEnv*, jobject, jlong handle) {
    delete fromHandle(handle);
}
