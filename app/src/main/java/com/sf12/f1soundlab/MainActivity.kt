package com.sf12.f1soundlab

import android.graphics.Color as AndroidColor
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.foundation.Image
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sf12.f1soundlab.audio.EngineTelemetry
import com.sf12.f1soundlab.audio.F1AudioEngine
import kotlinx.coroutines.delay
import kotlin.math.roundToInt

private val Night = Color(0xFF080C12)
private val Panel = Color(0xFF111923)
private val PanelRaised = Color(0xFF172330)
private val White = Color(0xFFF1F5F8)
private val Muted = Color(0xFF8797A8)
private val Mint = Color(0xFF3CE6C3)
private val Amber = Color(0xFFFFB45C)
private val Red = Color(0xFFFF5C68)
private val Hairline = Color(0xFF263443)

class MainActivity : ComponentActivity() {
    @Suppress("DEPRECATION")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = AndroidColor.rgb(8, 12, 18)
        window.navigationBarColor = AndroidColor.rgb(8, 12, 18)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = Mint,
                    onPrimary = Night,
                    secondary = Amber,
                    background = Night,
                    surface = Panel,
                    onSurface = White,
                    error = Red
                )
            ) {
                CarSoundLabScreen()
            }
        }
    }
}

@Composable
private fun CarSoundLabScreen() {
    val appContext = LocalContext.current.applicationContext
    val engine = remember(appContext) { F1AudioEngine(appContext.assets) }
    var throttle by remember { mutableFloatStateOf(0f) }
    var brake by remember { mutableFloatStateOf(0f) }
    var drsRequested by remember { mutableStateOf(false) }
    var running by remember { mutableStateOf(false) }
    var audioError by remember { mutableStateOf<String?>(null) }
    var telemetry by remember { mutableStateOf(EngineTelemetry()) }

    DisposableEffect(engine) {
        onDispose { engine.stop() }
    }

    LaunchedEffect(running) {
        if (!running) {
            telemetry = engine.telemetry
        } else {
            while (running) {
                telemetry = engine.telemetry
                delay(90)
            }
        }
    }

    Surface(modifier = Modifier.fillMaxSize(), color = Night) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .windowInsetsPadding(WindowInsets.safeDrawing)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 18.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            AppHeader(running = running)
            TelemetryPanel(telemetry = telemetry, running = running)
            CarPanel(
                wheelLocked = telemetry.wheelLocked,
                drsOpen = telemetry.drsActive
            )
            DrsPanel(
                requested = drsRequested,
                active = telemetry.drsActive,
                onToggle = {
                    drsRequested = !drsRequested
                    engine.setDrsEnabled(drsRequested)
                }
            )
            PedalPanel(
                throttle = throttle,
                brake = brake,
                onThrottleChange = {
                    throttle = it
                    engine.setPedals(throttle / 100.0, brake / 100.0)
                },
                onBrakeChange = {
                    brake = it
                    engine.setPedals(throttle / 100.0, brake / 100.0)
                }
            )

            Button(
                onClick = {
                    if (running) {
                        throttle = 0f
                        brake = 0f
                        engine.setPedals(0.0, 0.0)
                        engine.stop()
                        telemetry = EngineTelemetry()
                        running = false
                        drsRequested = false
                        engine.setDrsEnabled(false)
                        audioError = null
                    } else {
                        try {
                            engine.setPedals(throttle / 100.0, brake / 100.0)
                            engine.start()
                            running = true
                            audioError = null
                        } catch (_: Exception) {
                            running = false
                            audioError = "Audio could not start. Check your media volume and try again."
                        }
                    }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(58.dp),
                shape = RoundedCornerShape(18.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (running) Color(0xFF40212A) else Mint,
                    contentColor = if (running) Red else Night
                )
            ) {
                Text(
                    text = if (running) "■   STOP ENGINE" else "▶   START ENGINE SOUND",
                    fontWeight = FontWeight.Black,
                    fontSize = 15.sp,
                    letterSpacing = 1.3.sp
                )
            }

            if (audioError != null) {
                Text(
                    text = audioError.orEmpty(),
                    color = Red,
                    fontSize = 12.sp,
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center
                )
            }

            Text(
                text = "HYBRID AUDIO  •  OFFLINE  •  UNOFFICIAL SIMULATOR",
                modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                color = Muted,
                fontSize = 9.sp,
                letterSpacing = 1.15.sp,
                textAlign = TextAlign.Center
            )
        }
    }
}

@Composable
private fun AppHeader(running: Boolean) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Box(
            modifier = Modifier
                .size(48.dp)
                .clip(RoundedCornerShape(15.dp))
                .background(PanelRaised)
                .border(1.dp, Hairline, RoundedCornerShape(15.dp)),
            contentAlignment = Alignment.Center
        ) {
            Image(
                painter = painterResource(com.sf12.f1soundlab.R.drawable.ic_engine_symbol),
                contentDescription = "Engine icon",
                contentScale = ContentScale.Fit,
                modifier = Modifier.size(34.dp)
            )
        }
        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                "F1 SOUND LAB",
                color = White,
                fontSize = 19.sp,
                fontWeight = FontWeight.Black,
                letterSpacing = 1.2.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            Text("2025 HYBRID POWER UNIT", color = Muted, fontSize = 10.sp, letterSpacing = 1.35.sp)
        }
        Row(
            modifier = Modifier
                .clip(CircleShape)
                .background(if (running) Color(0xFF153B35) else Panel)
                .border(1.dp, if (running) Color(0xFF286A5D) else Hairline, CircleShape)
                .padding(horizontal = 10.dp, vertical = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(if (running) Mint else Muted))
            Text(if (running) "LIVE" else "READY", color = if (running) Mint else Muted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun TelemetryPanel(telemetry: EngineTelemetry, running: Boolean) {
    Surface(
        color = Panel,
        shape = RoundedCornerShape(22.dp),
        modifier = Modifier.fillMaxWidth().border(1.dp, Hairline, RoundedCornerShape(22.dp))
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 18.dp, vertical = 15.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1.25f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text("ENGINE SPEED", color = Muted, fontSize = 10.sp, letterSpacing = 1.25.sp, fontWeight = FontWeight.Bold)
                Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    Text(
                        text = (if (running) telemetry.rpm else 0).toString().padStart(5, '0'),
                        color = if (running) Mint else White,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 33.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = (-1.2).sp
                    )
                    Text("RPM", color = Muted, fontSize = 10.sp, modifier = Modifier.padding(bottom = 6.dp))
                }
                Text("15,000 RPM REDLINE", color = Muted, fontSize = 9.sp, letterSpacing = 1.sp)
            }
            Box(Modifier.width(1.dp).height(52.dp).background(Hairline))
            Spacer(Modifier.width(16.dp))
            Column(modifier = Modifier.weight(0.8f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text("SPEED", color = Muted, fontSize = 10.sp, letterSpacing = 1.2.sp, fontWeight = FontWeight.Bold)
                Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        telemetry.speedKmh.toString().padStart(3, '0'),
                        color = White,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 25.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Text("km/h", color = Muted, fontSize = 10.sp, modifier = Modifier.padding(bottom = 4.dp))
                }
                Text("GEAR  ${telemetry.gear}", color = Amber, fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
                Text(
                    if (running) "ERS  ${telemetry.ersChargePercent}%" else "ERS OFF",
                    color = if (running && telemetry.ersChargePercent < 20) Amber else Mint,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 0.7.sp
                )
            }
        }
    }
}

@Composable
private fun CarPanel(wheelLocked: Boolean, drsOpen: Boolean) {
    Surface(
        color = Panel,
        shape = RoundedCornerShape(22.dp),
        modifier = Modifier.fillMaxWidth().border(1.dp, Hairline, RoundedCornerShape(22.dp))
    ) {
        Column(
            modifier = Modifier.padding(start = 14.dp, end = 14.dp, top = 13.dp, bottom = 12.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("SIDE PROFILE", color = White, fontSize = 11.sp, fontWeight = FontWeight.Black, letterSpacing = 1.25.sp)
                Spacer(Modifier.weight(1f))
                Text(if (drsOpen) "DRS OPEN" else "25  /  HYBRID ERA", color = if (drsOpen) Amber else Mint, fontSize = 9.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
            }
            Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                Image(
                    painter = painterResource(R.drawable.rb21_side_profile),
                    contentDescription = "2025 Red Bull Racing RB21 side profile with Pirelli tyres",
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxWidth().aspectRatio(4.28f)
                )
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(7.dp)
            ) {
                Text("1.6 L", color = White, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                TinySeparator()
                Text("90° V6", color = White, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                TinySeparator()
                Text("TURBO", color = Muted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                TinySeparator()
                Text("MGU-K / MGU-H", color = Muted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
            }
            if (wheelLocked) {
                Text(
                    "WHEEL LOCK-UP — EASE OFF THE BRAKE TO REGAIN ROTATION",
                    color = Red,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Black,
                    letterSpacing = 0.55.sp
                )
            }
        }
    }
}

@Composable
private fun DrsPanel(requested: Boolean, active: Boolean, onToggle: () -> Unit) {
    Surface(
        color = Panel,
        shape = RoundedCornerShape(18.dp),
        modifier = Modifier.fillMaxWidth().border(1.dp, Hairline, RoundedCornerShape(18.dp))
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text("DRS · DRAG REDUCTION", color = White, fontSize = 11.sp, fontWeight = FontWeight.Black, letterSpacing = 0.8.sp)
                Text(
                    text = when {
                        active -> "OPEN · reduced aerodynamic drag"
                        requested -> "ARMED · needs 100 km/h and no braking"
                        else -> "CLOSED · rear wing in high-downforce mode"
                    },
                    color = if (active) Amber else Muted,
                    fontSize = 9.sp
                )
            }
            Button(
                onClick = onToggle,
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (requested) Amber else PanelRaised,
                    contentColor = if (requested) Night else White
                ),
                modifier = Modifier.height(42.dp)
            ) {
                Text(if (requested) "DRS ON" else "DRS OFF", fontWeight = FontWeight.Black, fontSize = 11.sp)
            }
        }
    }
}

@Composable
private fun TinySeparator() {
    Box(Modifier.size(3.dp).clip(CircleShape).background(Hairline))
}

@Composable
private fun PedalPanel(
    throttle: Float,
    brake: Float,
    onThrottleChange: (Float) -> Unit,
    onBrakeChange: (Float) -> Unit
) {
    Surface(
        color = Panel,
        shape = RoundedCornerShape(22.dp),
        modifier = Modifier.fillMaxWidth().border(1.dp, Hairline, RoundedCornerShape(22.dp))
    ) {
        Column(modifier = Modifier.padding(horizontal = 17.dp, vertical = 14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("DRIVER INPUTS", color = White, fontSize = 11.sp, fontWeight = FontWeight.Black, letterSpacing = 1.2.sp)
                Spacer(Modifier.weight(1f))
                Text("0–100%", color = Muted, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
            }
            HoldPedal(
                label = "THROTTLE PEDAL",
                value = throttle,
                color = Mint,
                hint = "HOLD TO APPLY POWER · RELEASE TO RETURN TO 0%",
                onPressed = { pressed -> onThrottleChange(if (pressed) 100f else 0f) }
            )
            Box(Modifier.fillMaxWidth().height(1.dp).background(Hairline))
            HoldPedal(
                label = "BRAKING PEDAL",
                value = brake,
                color = Amber,
                hint = "HOLD TO BRAKE · LOCK-UP ≥72% AFTER 0.8S",
                onPressed = { pressed -> onBrakeChange(if (pressed) 100f else 0f) }
            )
        }
    }
}

@Composable
private fun HoldPedal(
    label: String,
    value: Float,
    color: Color,
    hint: String,
    onPressed: (Boolean) -> Unit
) {
    val latestOnPressed = rememberUpdatedState(onPressed)
    val pressed = value > 0f
    val shape = RoundedCornerShape(15.dp)

    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(color))
            Spacer(Modifier.width(7.dp))
            Text(label, color = White, fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.1.sp)
            Spacer(Modifier.weight(1f))
            Text("${value.roundToInt()}%", color = color, fontSize = 17.sp, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)
        }
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(62.dp)
                .clip(shape)
                .background(if (pressed) color.copy(alpha = 0.17f) else PanelRaised)
                .border(1.dp, if (pressed) color else Hairline, shape)
                .pointerInput(Unit) {
                    awaitEachGesture {
                        awaitFirstDown(requireUnconsumed = false)
                        latestOnPressed.value(true)
                        try {
                            waitForUpOrCancellation()
                        } finally {
                            // Release and cancellation both release the pedal immediately.
                            latestOnPressed.value(false)
                        }
                    }
                },
            contentAlignment = Alignment.Center
        ) {
            Text(
                text = if (pressed) "APPLIED  ·  RELEASE TO LIFT" else "PRESS AND HOLD",
                color = if (pressed) color else White,
                fontSize = 12.sp,
                fontWeight = FontWeight.Black,
                letterSpacing = 1.1.sp
            )
        }
        Text(hint, color = Muted, fontSize = 8.sp, letterSpacing = 0.7.sp, modifier = Modifier.align(Alignment.End))
    }
}
