package com.sf12.f1soundlab.audio

import android.content.res.AssetManager

import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min

/** Snapshot used by the UI and by any Kotlin-owned timestamped telemetry source. */
data class EngineTelemetry(
    val rpm: Int = 0,
    val speedKmh: Int = 0,
    val gear: Int = 1,
    val wheelLocked: Boolean = false,
    val drsActive: Boolean = false,
    val ersChargePercent: Int = 0
)

/**
 * Kotlin remains responsible for control handling and the telemetry timeline. The native C++
 * Oboe callback receives telemetry targets and synthesizes the entire audio signal in real time.
 * This source tree currently drives the UI from a small live vehicle simulation, and
 * submitTelemetryFrame() is the entry point for a Kotlin file parser/timestamp scheduler.
 */
class F1AudioEngine(private val assetManager: AssetManager) {
    companion object {
        init { System.loadLibrary("carsoundlab_audio") }
        private const val TELEMETRY_TICK_MS = 16L
    }

    @Volatile private var running = false
    @Volatile private var throttleInput = 0.0
    @Volatile private var brakeInput = 0.0
    @Volatile private var drsInput = false
    @Volatile private var externalTelemetryMode = false

    @Volatile var telemetry: EngineTelemetry = EngineTelemetry()
        private set

    private var nativeHandle = 0L
    private var telemetryThread: Thread? = null
    private val simulator = F1VehicleSimulation()
    private val telemetryLock = Any()

    private external fun nativeOpen(assetManager: AssetManager): Long
    private external fun nativeSetControls(handle: Long, throttle: Float, brake: Float)
    private external fun nativeSetTelemetry(
        handle: Long,
        rpm: Int,
        speedKmh: Int,
        gear: Int,
        wheelLocked: Boolean,
        drsActive: Boolean,
        ersChargePercent: Int
    )
    private external fun nativeClose(handle: Long)

    @Synchronized
    fun setPedals(throttle: Double, brake: Double) {
        throttleInput = throttle.coerceIn(0.0, 1.0)
        brakeInput = brake.coerceIn(0.0, 1.0)
        pushControls()
    }

    @Synchronized
    fun setDrsEnabled(enabled: Boolean) {
        drsInput = enabled
    }

    /**
     * Supply one externally parsed telemetry frame. Call this from the Kotlin timestamp scheduler
     * as each source frame becomes due. This switches off the built-in live simulation for the
     * current run; RPM, speed and gear then follow the caller's timeline.
     */
    @Synchronized
    fun submitTelemetryFrame(
        frame: EngineTelemetry,
        throttle: Double = throttleInput,
        brake: Double = brakeInput
    ) {
        synchronized(telemetryLock) {
            externalTelemetryMode = true
            throttleInput = throttle.coerceIn(0.0, 1.0)
            brakeInput = brake.coerceIn(0.0, 1.0)
            drsInput = frame.drsActive
            telemetry = frame.copy(
                rpm = frame.rpm.coerceIn(4_000, 15_000),
                speedKmh = frame.speedKmh.coerceIn(0, 353),
                gear = frame.gear.coerceIn(1, 8),
                ersChargePercent = frame.ersChargePercent.coerceIn(0, 100)
            )
            pushControls()
            pushTelemetry()
        }
    }

    /** Return to the native sound engine's built-in live-drive simulation on a subsequent run. */
    @Synchronized
    fun useLiveSimulation() {
        synchronized(telemetryLock) {
            simulator.reset(telemetry)
            externalTelemetryMode = false
        }
    }

    @Synchronized
    fun start() {
        if (running) return
        val handle = nativeOpen(assetManager)
        check(handle != 0L) { "Could not open the native Oboe audio stream." }
        nativeHandle = handle
        if (!externalTelemetryMode) {
            val seed = telemetry.takeIf { it.rpm in 4_000..15_000 }
                ?: EngineTelemetry(rpm = 4_000, ersChargePercent = 55)
            synchronized(telemetryLock) {
                simulator.reset(seed)
                telemetry = seed
            }
        }
        running = true
        pushControls()
        pushTelemetry()
        telemetryThread = Thread({ telemetryLoop() }, "CarSoundLab-Telemetry").apply {
            priority = Thread.NORM_PRIORITY
            isDaemon = true
            start()
        }
    }

    @Synchronized
    fun stop() {
        // Never leave a stale pedal input active across a stop/restart or interrupted gesture.
        throttleInput = 0.0
        brakeInput = 0.0
        drsInput = false
        running = false
        val oldThread = telemetryThread
        var interrupted = false
        if (oldThread != null && Thread.currentThread() !== oldThread) {
            oldThread.interrupt()
            while (oldThread.isAlive) {
                try { oldThread.join() } catch (_: InterruptedException) {
                    interrupted = true
                    oldThread.interrupt()
                }
            }
        }
        val oldHandle = nativeHandle
        nativeHandle = 0L
        if (oldHandle != 0L) {
            try { nativeClose(oldHandle) } catch (_: Exception) { /* Best-effort native teardown. */ }
        }
        telemetryThread = null
        telemetry = EngineTelemetry()
        synchronized(telemetryLock) {
            externalTelemetryMode = false
            simulator.reset(EngineTelemetry(rpm = 4_000, ersChargePercent = 55))
        }
        if (interrupted) Thread.currentThread().interrupt()
    }

    private fun pushControls() {
        val handle = nativeHandle
        if (running && handle != 0L) nativeSetControls(handle, throttleInput.toFloat(), brakeInput.toFloat())
    }

    private fun pushTelemetry() {
        val handle = nativeHandle
        if (!running || handle == 0L) return
        val t = telemetry
        nativeSetTelemetry(handle, t.rpm, t.speedKmh, t.gear, t.wheelLocked, t.drsActive, t.ersChargePercent)
    }

    private fun telemetryLoop() {
        var previousNanos = System.nanoTime()
        try {
            while (running && !Thread.currentThread().isInterrupted) {
                synchronized(telemetryLock) {
                    if (!externalTelemetryMode) {
                        val now = System.nanoTime()
                        val dt = ((now - previousNanos) / 1_000_000_000.0).coerceIn(0.001, 0.05)
                        previousNanos = now
                        telemetry = simulator.step(throttleInput, brakeInput, drsInput, dt)
                        pushTelemetry()
                    } else {
                        previousNanos = System.nanoTime()
                    }
                }
                Thread.sleep(TELEMETRY_TICK_MS)
            }
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        } catch (_: RuntimeException) {
            running = false
        }
    }
}

/** Low-rate live-car state only; no oscillators, filters, PCM, or audio rendering live here. */
private class F1VehicleSimulation {
    companion object {
        private const val IDLE_RPM = 4_000.0
        private const val REDLINE_RPM = 15_000.0
        private const val ERS_CAPACITY_MJ = 4.0
        private const val MGU_K_MAX_POWER_W = 120_000.0
        private const val MGU_K_MAX_TORQUE_NM = 200.0
        private const val VEHICLE_MASS_KG = 820.0
        private const val WHEEL_CIRCUMFERENCE_M = 2.073
        private val GEAR_RATIOS = doubleArrayOf(14.0, 11.35, 9.35, 7.40, 6.20, 5.40, 4.87, 4.42)
    }

    private var rpm = IDLE_RPM
    private var speedMps = 0.0
    private var gear = 1
    private var ersEnergyMj = 2.2
    private var smoothedThrottle = 0.0
    private var turboSpool = 0.0
    private var mguHAssist = 0.0
    private var shiftTimer = 0.0
    private var brakePressureDuration = 0.0
    private var wheelLocked = false
    private var drsActive = false

    fun reset(from: EngineTelemetry) {
        rpm = from.rpm.coerceIn(IDLE_RPM.toInt(), REDLINE_RPM.toInt()).toDouble()
        speedMps = from.speedKmh.coerceIn(0, 353) / 3.6
        gear = from.gear.coerceIn(1, 8)
        ersEnergyMj = ERS_CAPACITY_MJ * from.ersChargePercent.coerceIn(0, 100) / 100.0
        wheelLocked = from.wheelLocked
        drsActive = from.drsActive
        smoothedThrottle = 0.0
        turboSpool = 0.0
        mguHAssist = 0.0
        shiftTimer = 0.0
        brakePressureDuration = 0.0
    }

    fun step(throttleInput: Double, brakeInput: Double, drsRequested: Boolean, elapsedSeconds: Double): EngineTelemetry {
        val dt = elapsedSeconds.coerceIn(0.001, 0.05)
        val throttle = throttleInput.coerceIn(0.0, 1.0)
        val brake = brakeInput.coerceIn(0.0, 1.0)
        smoothedThrottle += (throttle - smoothedThrottle) * (1.0 - exp(-dt * 34.0))
        val t = smoothedThrottle.coerceIn(0.0, 1.0)
        val normalizedSpeed = (speedMps / 98.0).coerceIn(0.0, 1.0)
        val turboTarget = (t * 0.79 + rpm / REDLINE_RPM * 0.21).coerceIn(0.0, 1.0)
        val turboGap = (turboTarget - turboSpool).coerceAtLeast(0.0)
        val mguHTarget = (0.18 + t * 0.54 + rpm / REDLINE_RPM * 0.22 + turboGap * 1.5).coerceIn(0.0, 1.0)
        val mguHResponse = if (mguHTarget > mguHAssist) 12.0 else 3.2
        mguHAssist += (mguHTarget - mguHAssist) * (1.0 - exp(-mguHResponse * dt))
        val turboResponse = if (turboTarget > turboSpool) 2.8 + 4.6 * mguHAssist else 0.82 + 0.70 * mguHAssist
        turboSpool += (turboTarget - turboSpool) * (1.0 - exp(-turboResponse * dt))

        if (brake >= 0.72 && speedMps > 5.0) {
            brakePressureDuration += dt
            if (brakePressureDuration >= 0.78) wheelLocked = true
        } else if (brake < 0.56 || speedMps <= 1.0) {
            brakePressureDuration = 0.0
            wheelLocked = false
        } else {
            brakePressureDuration = (brakePressureDuration - dt * 0.25).coerceAtLeast(0.0)
        }
        drsActive = drsRequested && speedMps >= 27.8 && brake < 0.10

        val engineBrakeFactor = (1.0 - t / 0.30).coerceIn(0.0, 1.0)
        val gearEngineBrake = 1.15 + (8 - gear).coerceAtLeast(0) * 0.085
        val couplingForBrake = smoothStep(((speedMps - 1.0) / 9.0).coerceIn(0.0, 1.0))
        val engineBrakeDeceleration = engineBrakeFactor * gearEngineBrake * couplingForBrake
        val ersFraction = (ersEnergyMj / ERS_CAPACITY_MJ).coerceIn(0.0, 1.0)
        val deploymentDemand = ((t - 0.12) / 0.88).coerceIn(0.0, 1.0)
        val crankAngularSpeed = rpm * 2.0 * PI / 60.0
        val mgukPowerLimit = min(MGU_K_MAX_POWER_W, MGU_K_MAX_TORQUE_NM * crankAngularSpeed)
        val deploymentPower = mgukPowerLimit * deploymentDemand * ersFraction * (1.0 - brake)
        val electricalAssist = (deploymentPower / (VEHICLE_MASS_KG * max(speedMps, 18.0)) * 0.20).coerceIn(0.0, 1.6)
        val harvestDemand = max(brake * if (wheelLocked) 0.22 else 0.85, engineBrakeFactor * couplingForBrake * 0.30).coerceIn(0.0, 1.0)
        val mgukHarvest = mgukPowerLimit * harvestDemand
        val normalizedRpm = (rpm / REDLINE_RPM).coerceIn(0.0, 1.0)
        val mguHHarvest = 46_000.0 * turboSpool * (0.08 + 0.92 * t) *
            (0.35 + 0.65 * normalizedRpm) * (0.55 + 0.45 * mguHAssist)
        ersEnergyMj = (ersEnergyMj - deploymentPower * dt / 1_000_000.0 +
            (mgukHarvest + mguHHarvest) * dt / 1_000_000.0).coerceIn(0.0, ERS_CAPACITY_MJ)

        val brakingDeceleration = brake * if (wheelLocked) 8.0 else 21.0
        val combustionAcceleration = t * (6.1 + 3.0 * (1.0 - normalizedSpeed))
        val drsDriveGain = if (drsActive && normalizedSpeed > 0.22) 1.065 else 1.0
        val driveAcceleration = (combustionAcceleration + electricalAssist) * (1.0 - 0.985 * brake) * drsDriveGain
        val drag = (0.00065 * speedMps * speedMps + 0.10) * if (drsActive) 0.72 else 1.0
        speedMps = (speedMps + (driveAcceleration - brakingDeceleration - engineBrakeDeceleration - drag) * dt).coerceIn(0.0, 98.0)

        val wheelRpm = speedMps / WHEEL_CIRCUMFERENCE_M * 60.0
        val coupledRpm = wheelRpm * GEAR_RATIOS[gear - 1]
        if (shiftTimer <= 0.0 && speedMps > 3.0) {
            if (coupledRpm > 11_750.0 && gear < GEAR_RATIOS.size) {
                gear++
                shiftTimer = 0.092
            } else if (coupledRpm < 6_300.0 && gear > 1) {
                gear--
                shiftTimer = 0.100
            }
        }
        val currentGearRpm = (wheelRpm * GEAR_RATIOS[gear - 1]).coerceIn(IDLE_RPM, REDLINE_RPM - 80.0)
        val coupling = smoothStep(((speedMps - 1.0) / 9.0).coerceIn(0.0, 1.0))
        val freeRevRpm = IDLE_RPM + t * 10_900.0
        val targetRpm = (freeRevRpm * (1.0 - coupling) + currentGearRpm * coupling).coerceIn(IDLE_RPM, REDLINE_RPM - 50.0)
        val response = when {
            shiftTimer > 0.0 && targetRpm > rpm -> 28.0
            targetRpm > rpm -> 7.4
            else -> 5.2
        }
        rpm += (targetRpm - rpm) * (1.0 - exp(-response * dt))
        rpm = rpm.coerceIn(IDLE_RPM, REDLINE_RPM - 20.0)
        if (shiftTimer > 0.0) shiftTimer = (shiftTimer - dt).coerceAtLeast(0.0)

        return EngineTelemetry(
            rpm = rpm.toInt().coerceIn(IDLE_RPM.toInt(), REDLINE_RPM.toInt()),
            speedKmh = (speedMps * 3.6).toInt().coerceIn(0, 353),
            gear = gear,
            wheelLocked = wheelLocked,
            drsActive = drsActive,
            ersChargePercent = (ersEnergyMj / ERS_CAPACITY_MJ * 100.0).toInt().coerceIn(0, 100)
        )
    }

    private fun smoothStep(x: Double) = x * x * (3.0 - 2.0 * x)
}
