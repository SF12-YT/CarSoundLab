package com.sf12.f1soundlab.ui

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import kotlin.math.PI
import kotlin.math.sin

@Composable
fun F1CarIllustration(
    modifier: Modifier = Modifier,
    wheelSpeed: Int,
    wheelLocked: Boolean,
    drsOpen: Boolean
) {
    Canvas(modifier = modifier) {
        val sx = size.width / 428f
        val sy = size.height / 100f

        fun p(x: Float, y: Float) = Offset(x * sx, y * sy)
        fun line(
            x1: Float, y1: Float, x2: Float, y2: Float,
            color: Color, width: Float = 2f
        ) {
            drawLine(color, p(x1, y1), p(x2, y2), width * sx, cap = StrokeCap.Round)
        }

        val red = Color(0xFFE52B36)
        val darkRed = Color(0xFF8F1726)
        val carbon = Color(0xFF171E29)
        val trim = Color(0xFF485566)
        val mint = Color(0xFF3CE6C3)
        val rubber = Color(0xFF090C11)
        val metal = Color(0xFF758393)

        // Ground shadow
        drawLine(
            Color(0x553CE6C3), p(20f, 82f), p(408f, 82f),
            1.2f * sx
        )

        // Front and rear tyres
        val wheels = listOf(90f, 329f)
        wheels.forEach { x ->
            drawRoundRect(
                rubber,
                topLeft = p(x - 15f, 53f),
                size = Size(30f * sx, 30f * sy),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(6f * sx)
            )
            drawCircle(
                metal,
                radius = 9f * sx,
                center = p(x, 68f),
                style = Stroke(2f * sx)
            )
            drawCircle(
                trim,
                radius = 3f * sx,
                center = p(x, 68f)
            )
            for (i in 0..3) {
                val angle = i * PI.toFloat() / 2f
                line(
                    x, 68f,
                    x + 7f * sin(angle + wheelSpeed * 0.002f),
                    68f + 7f * kotlin.math.cos(angle + wheelSpeed * 0.002f),
                    metal, 1f
                )
            }
            if (wheelLocked) {
                line(x - 13f, 51f, x + 13f, 79f, red, 2f)
            }
        }

        // Front wing and nose
        val nose = Path().apply {
            moveTo(p(18f, 57f).x, p(18f, 57f).y)
            lineTo(p(77f, 57f).x, p(77f, 57f).y)
            lineTo(p(112f, 63f).x, p(112f, 63f).y)
            lineTo(p(112f, 68f).x, p(112f, 68f).y)
            lineTo(p(62f, 66f).x, p(62f, 66f).y)
            lineTo(p(18f, 63f).x, p(18f, 63f).y)
            close()
        }
        drawPath(nose, red)
        line(12f, 56f, 79f, 56f, carbon, 3f)
        line(9f, 64f, 65f, 65f, metal, 2f)
        line(33f, 57f, 33f, 64f, darkRed, 1.5f)

        // Main chassis and floor
        val floor = Path().apply {
            moveTo(p(75f, 58f).x, p(75f, 58f).y)
            lineTo(p(129f, 53f).x, p(129f, 53f).y)
            lineTo(p(246f, 57f).x, p(246f, 57f).y)
            lineTo(p(307f, 61f).x, p(307f, 61f).y)
            lineTo(p(352f, 65f).x, p(352f, 65f).y)
            lineTo(p(351f, 72f).x, p(351f, 72f).y)
            lineTo(p(120f, 72f).x, p(120f, 72f).y)
            lineTo(p(87f, 68f).x, p(87f, 68f).y)
            close()
        }
        drawPath(floor, carbon)
        line(119f, 72f, 354f, 72f, mint, 1.4f)

        // Sidepod
        val sidepod = Path().apply {
            moveTo(p(143f, 45f).x, p(143f, 45f).y)
            lineTo(p(211f, 43f).x, p(211f, 43f).y)
            lineTo(p(258f, 51f).x, p(258f, 51f).y)
            lineTo(p(273f, 62f).x, p(273f, 62f).y)
            lineTo(p(173f, 62f).x, p(173f, 62f).y)
            lineTo(p(147f, 55f).x, p(147f, 55f).y)
            close()
        }
        drawPath(sidepod, red)
        line(158f, 53f, 238f, 53f, darkRed, 2f)
        line(174f, 59f, 255f, 59f, Color(0xFFFFB45C), 1.4f)

        // Engine cover and air intake
        val cover = Path().apply {
            moveTo(p(145f, 48f).x, p(145f, 48f).y)
            lineTo(p(161f, 30f).x, p(161f, 30f).y)
            lineTo(p(192f, 23f).x, p(192f, 23f).y)
            lineTo(p(222f, 29f).x, p(222f, 29f).y)
            lineTo(p(239f, 45f).x, p(239f, 45f).y)
            lineTo(p(213f, 48f).x, p(213f, 48f).y)
            close()
        }
        drawPath(cover, red)
        line(168f, 34f, 203f, 34f, darkRed, 2f)
        drawRoundRect(
            carbon,
            topLeft = p(181f, 20f),
            size = Size(21f * sx, 9f * sy),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(3f * sx)
        )

        // Cockpit and halo
        val cockpit = Path().apply {
            moveTo(p(190f, 43f).x, p(190f, 43f).y)
            lineTo(p(202f, 34f).x, p(202f, 34f).y)
            lineTo(p(221f, 37f).x, p(221f, 37f).y)
            lineTo(p(230f, 46f).x, p(230f, 46f).y)
            close()
        }
        drawPath(cockpit, carbon)
        line(190f, 42f, 205f, 32f, metal, 2f)
        line(205f, 32f, 225f, 35f, metal, 2f)
        line(225f, 35f, 232f, 46f, metal, 2f)

        // Rear bodywork and exhaust
        val rear = Path().apply {
            moveTo(p(241f, 48f).x, p(241f, 48f).y)
            lineTo(p(267f, 39f).x, p(267f, 39f).y)
            lineTo(p(302f, 43f).x, p(302f, 43f).y)
            lineTo(p(317f, 54f).x, p(317f, 54f).y)
            lineTo(p(286f, 59f).x, p(286f, 59f).y)
            close()
        }
        drawPath(rear, darkRed)
        drawCircle(metal, 2f * sx, p(276f, 49f))
        line(281f, 49f, 294f, 49f, Color(0xFFFFB45C), 1.4f)

        // Rear wing supports and wing
        line(309f, 45f, 311f, 27f, carbon, 3f)
        line(330f, 47f, 330f, 28f, carbon, 3f)
        line(293f, 26f, 351f, 26f, red, 5f)
        line(299f, 21f, 354f, 21f, carbon, 3f)
        line(300f, 21f, 294f, 26f, mint, 1f)

        // DRS flap indicator
        if (drsOpen) {
            line(300f, 18f, 354f, 13f, mint, 2f)
            line(350f, 13f, 356f, 17f, mint, 1.5f)
        }

        // Suspension arms
        line(77f, 52f, 104f, 62f, metal, 1.3f)
        line(113f, 52f, 91f, 62f, metal, 1.3f)
        line(313f, 50f, 331f, 62f, metal, 1.3f)
        line(346f, 52f, 330f, 62f, metal, 1.3f)

        // Subtle side detail
        line(133f, 65f, 163f, 65f, Color(0xFFCDD6E0), 1f)
        line(208f, 46f, 216f, 46f, mint, 1.4f)
    }
}
