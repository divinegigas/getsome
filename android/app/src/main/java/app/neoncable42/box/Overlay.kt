package app.neoncable42.box

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Shader
import android.os.SystemClock
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils
import android.view.View
import kotlin.math.max
import kotlin.math.min
import kotlin.random.Random

/** Everything drawn over the picture, in the desktop's style: static, colour bars, the big channel
 *  number, the info banner, cards over the made-up commercials, the restricted-channel veil, the touch
 *  buttons, number / PIN pad, guide list and menu. TvActivity sets the fields and calls invalidate(). */
class Overlay(ctx: Context) : View(ctx) {

    // ---------------------------------------------------------------- what to show (set by TvActivity)
    var staticOn = false
    var bars = false
    var barsNote = ""

    var number = ""
    var numberUntil = 0L

    data class Banner(val header: String, val title: String, val line1: String, val line2: String)
    var banner: Banner? = null
    var bannerUntil = 0L

    data class Card(val heading: String, val title: String, val body: String, val image: Bitmap? = null,
                    val poster: Boolean = false)
    var card: Card? = null

    var locked = false
    var lockOpaque = false              // the blur filter isn't available: hide the picture instead
    var lockNote = ""

    var controlsUntil = 0L

    var pad: String? = null           // null, "channel" or "pin"
    var padDigits = ""

    data class GuideRow(val number: Int, val name: String, val now: String, val next: String)
    var guide: List<GuideRow>? = null
    var guideSel = 0
    var guideScroll = 0f

    data class MenuRow(val label: String, val value: String, val slider: Float? = null, val active: Boolean = true)
    var menu: List<MenuRow>? = null
    var menuSel = 0

    var info: List<String>? = null     // INFO readout (menu), top left

    var message = ""
    var messageUntil = 0L

    // ---------------------------------------------------------------- touch targets (filled while drawing)
    sealed class Hit {
        data class Button(val key: String) : Hit()
        data class PadKey(val key: String) : Hit()
        data class GuideRowHit(val index: Int) : Hit()
        data class MenuChange(val row: Int, val dir: Int) : Hit()
        object Outside : Hit()
    }

    private val targets = ArrayList<Pair<RectF, Hit>>()

    fun hit(x: Float, y: Float): Hit? = targets.lastOrNull { it.first.contains(x, y) }?.second

    // ---------------------------------------------------------------- paints
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private val tp = TextPaint(Paint.ANTI_ALIAS_FLAG)
    private val noise = Bitmap.createBitmap(160, 90, Bitmap.Config.ARGB_8888)
    private val noisePx = IntArray(160 * 90)
    private val noisePaint = Paint().apply { isFilterBitmap = false }
    private val rnd = Random(7)
    private var u = 1f

    init {
        Brand.load(ctx)
        setWillNotDraw(false)
    }

    fun showMessage(text: String, seconds: Double = 3.0) {
        message = text
        messageUntil = SystemClock.uptimeMillis() + (seconds * 1000).toLong()
        invalidate()
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        u = min(h / 1080f, w / 1920f * 1.25f)
        targets.clear()
        val now = SystemClock.uptimeMillis()
        var animate = false

        if (bars) drawBars(c, w, h)
        card?.let { drawCard(c, w, h, it) }
        if (locked && !staticOn) drawLocked(c, w, h)
        if (staticOn) {
            drawStatic(c, w, h)
            animate = true
        }
        if (now < numberUntil || staticOn) drawNumber(c, w)
        if (now < bannerUntil) banner?.let { drawBanner(c, w, h, it) }
        if (now < controlsUntil && pad == null && guide == null && menu == null) drawControls(c, w, h)
        guide?.let { drawGuide(c, w, h, it) }
        menu?.let { drawMenu(c, w, h, it) }
        pad?.let { drawPad(c, w, h, it) }
        info?.let { if (it.isNotEmpty()) drawInfo(c, it) }
        if (now < messageUntil) drawMessage(c, w, h)

        val next = listOf(numberUntil, bannerUntil, controlsUntil, messageUntil).filter { it > now }.minOrNull()
        if (animate) postInvalidateOnAnimation() else if (next != null) postInvalidateDelayed(next - now + 20)
    }

    // ---------------------------------------------------------------- pieces
    private fun text(c: Canvas, s: String, x: Float, y: Float, size: Float, color: Int, numbers: Boolean = false,
                     align: Paint.Align = Paint.Align.LEFT, glow: Int = 0) {
        p.reset()
        p.isAntiAlias = true
        p.typeface = if (numbers) Brand.numbers else Brand.text
        p.textSize = size
        p.color = color
        p.textAlign = align
        if (glow != 0) p.setShadowLayer(size * 0.18f, 0f, 0f, glow)
        c.drawText(s, x, y, p)
        p.clearShadowLayer()
    }

    private fun ellipsize(s: String, size: Float, width: Float): String {
        tp.typeface = Brand.text
        tp.textSize = size
        return TextUtils.ellipsize(s, tp, width, TextUtils.TruncateAt.END).toString()
    }

    private fun wrapped(c: Canvas, s: String, x: Float, y: Float, width: Float, size: Float, color: Int, maxLines: Int) {
        if (s.isEmpty()) return
        tp.typeface = Brand.text
        tp.textSize = size
        tp.color = color
        val l = StaticLayout.Builder.obtain(s, 0, s.length, tp, max(1, width.toInt()))
            .setAlignment(Layout.Alignment.ALIGN_NORMAL).setMaxLines(maxLines)
            .setEllipsize(TextUtils.TruncateAt.END).build()
        c.save()
        c.translate(x, y)
        l.draw(c)
        c.restore()
    }

    private fun panel(c: Canvas, r: RectF, outline: Int = Brand.SECOND) {
        val s = 30 * u
        val path = Path().apply {
            moveTo(r.left + s, r.top); lineTo(r.right, r.top); lineTo(r.right - s, r.bottom); lineTo(r.left, r.bottom); close()
        }
        p.reset()
        p.isAntiAlias = true
        p.shader = LinearGradient(0f, r.top, 0f, r.bottom, Brand.PANEL_TOP, Brand.PANEL_BOTTOM, Shader.TileMode.CLAMP)
        c.drawPath(path, p)
        p.shader = null
        p.style = Paint.Style.STROKE
        p.strokeWidth = max(1.5f, 2 * u)
        p.color = outline
        c.drawPath(path, p)
        p.style = Paint.Style.FILL
        p.color = Brand.ACCENT
        c.drawRect(r.left + s, r.top - 6 * u, r.left + s + r.width() * 0.35f, r.top, p)
    }

    private fun drawStatic(c: Canvas, w: Float, h: Float) {
        for (i in noisePx.indices) {
            val v = rnd.nextInt(256)
            noisePx[i] = (0xFF shl 24) or (v shl 16) or (v shl 8) or v
        }
        noise.setPixels(noisePx, 0, 160, 0, 0, 160, 90)
        c.drawBitmap(noise, null, RectF(0f, 0f, w, h), noisePaint)
    }

    private fun drawBars(c: Canvas, w: Float, h: Float) {
        // SMPTE-style colour bars (75%), drawn: the off-air picture under the 1 kHz tone
        val top = intArrayOf(0xFFC0C0C0.toInt(), 0xFFC0C000.toInt(), 0xFF00C0C0.toInt(), 0xFF00C000.toInt(),
            0xFFC000C0.toInt(), 0xFFC00000.toInt(), 0xFF0000C0.toInt())
        val mid = intArrayOf(0xFF0000C0.toInt(), 0xFF131313.toInt(), 0xFFC000C0.toInt(), 0xFF131313.toInt(),
            0xFF00C0C0.toInt(), 0xFF131313.toInt(), 0xFFC0C0C0.toInt())
        val bw = w / 7
        p.reset()
        for (i in 0..6) {
            p.color = top[i]; c.drawRect(i * bw, 0f, (i + 1) * bw, h * 0.67f, p)
            p.color = mid[i]; c.drawRect(i * bw, h * 0.67f, (i + 1) * bw, h * 0.75f, p)
        }
        val low = intArrayOf(0xFF00214C.toInt(), 0xFFFFFFFF.toInt(), 0xFF32006A.toInt(), 0xFF131313.toInt(),
            0xFF090909.toInt(), 0xFF131313.toInt(), 0xFF1D1D1D.toInt(), 0xFF131313.toInt())
        val lw = floatArrayOf(1.25f, 1.25f, 1.25f, 1.25f, 0.33f, 0.34f, 0.33f, 1f)
        var x = 0f
        for (i in low.indices) {
            val ww = bw * lw[i]
            p.color = low[i]; c.drawRect(x, h * 0.75f, x + ww, h, p)
            x += ww
        }
        if (barsNote.isNotEmpty()) {
            val r = RectF(w * 0.18f, h * 0.40f, w * 0.82f, h * 0.40f + 120 * u)
            p.color = 0xC0000000.toInt(); c.drawRect(r, p)
            text(c, ellipsize(barsNote, 40 * u, r.width() - 40 * u), r.centerX(), r.centerY() + 14 * u, 40 * u,
                0xFFFFFFFF.toInt(), align = Paint.Align.CENTER)
        }
    }

    private fun drawNumber(c: Canvas, w: Float) {
        if (number.isEmpty()) return
        text(c, number, w - 60 * u, 190 * u, 170 * u, Brand.ACCENT, numbers = true, align = Paint.Align.RIGHT,
            glow = Brand.ACCENT)
    }

    private fun drawBanner(c: Canvas, w: Float, h: Float, b: Banner) {
        val r = RectF(60 * u, h - 250 * u, w - 60 * u, h - 50 * u)
        panel(c, r)
        val x = r.left + 60 * u
        val tw = r.width() - 120 * u
        text(c, ellipsize(b.header, 34 * u, tw), x, r.top + 52 * u, 34 * u, Brand.SECOND)
        text(c, ellipsize(b.title, 52 * u, tw), x, r.top + 112 * u, 52 * u, 0xFFFFFFFF.toInt())
        text(c, ellipsize(b.line1, 32 * u, tw), x, r.top + 154 * u, 32 * u, Brand.DIM)
        text(c, ellipsize(b.line2, 30 * u, tw), x, r.top + 190 * u, 30 * u, Brand.HILITE)
    }

    private fun drawCard(c: Canvas, w: Float, h: Float, k: Card) {
        p.reset()
        p.color = 0xFF000000.toInt()
        c.drawRect(0f, 0f, w, h, p)
        val img = k.image
        if (img != null && !k.poster) {          // backdrop: fill the screen, darkened
            val sc = max(w / img.width, h / img.height)
            val dw = img.width * sc
            val dh = img.height * sc
            p.isFilterBitmap = true
            c.drawBitmap(img, null, RectF((w - dw) / 2, (h - dh) / 2, (w + dw) / 2, (h + dh) / 2), p)
            p.shader = LinearGradient(0f, 0f, 0f, h, 0x66000000, 0xE6000000.toInt(), Shader.TileMode.CLAMP)
            c.drawRect(0f, 0f, w, h, p)
            p.shader = null
        }
        var x = 120 * u
        if (img != null && k.poster) {           // poster on the left
            val ph = h * 0.70f
            val pw = ph * img.width / img.height
            val r = RectF(x, (h - ph) / 2, x + pw, (h + ph) / 2)
            p.isFilterBitmap = true
            c.drawBitmap(img, null, r, p)
            p.style = Paint.Style.STROKE; p.strokeWidth = 3 * u; p.color = Brand.SECOND
            c.drawRect(r, p)
            p.style = Paint.Style.FILL
            x = r.right + 80 * u
        }
        val tw = w - x - 120 * u
        var y = h * 0.30f
        p.color = Brand.ACCENT
        c.drawRect(x, y - 70 * u, x + 160 * u, y - 62 * u, p)
        text(c, ellipsize(k.heading, 44 * u, tw), x, y, 44 * u, Brand.HILITE)
        y += 30 * u
        if (k.title.isNotEmpty()) {
            tp.typeface = Brand.numbers
            wrappedTitle(c, k.title, x, y, tw)
            y += 230 * u
        }
        wrapped(c, k.body, x, y, tw, 40 * u, 0xFFEDE6FF.toInt(), 7)
        text(c, Brand.APP_NAME.uppercase(), w - 60 * u, h - 50 * u, 34 * u, Brand.DIM, align = Paint.Align.RIGHT)
    }

    private fun wrappedTitle(c: Canvas, s: String, x: Float, y: Float, width: Float) {
        tp.typeface = Brand.numbers
        tp.textSize = 84 * u
        tp.color = 0xFFFFFFFF.toInt()
        tp.setShadowLayer(14 * u, 0f, 0f, Brand.ACCENT)
        val l = StaticLayout.Builder.obtain(s, 0, s.length, tp, max(1, width.toInt()))
            .setMaxLines(2).setEllipsize(TextUtils.TruncateAt.END).build()
        c.save()
        c.translate(x, y)
        l.draw(c)
        c.restore()
        tp.clearShadowLayer()
    }

    private fun drawLocked(c: Canvas, w: Float, h: Float) {
        p.reset()
        p.color = if (lockOpaque) 0xF5000000.toInt() else 0x8C000000.toInt()
        c.drawRect(0f, 0f, w, h, p)
        val r = RectF(w * 0.2f, h * 0.38f, w * 0.8f, h * 0.38f + 200 * u)
        panel(c, r, Brand.ACCENT)
        text(c, "RESTRICTED", r.centerX(), r.top + 80 * u, 64 * u, Brand.ACCENT, numbers = true,
            align = Paint.Align.CENTER, glow = Brand.ACCENT)
        text(c, "Tap to enter the PIN" + if (lockNote.isNotEmpty()) "  ·  $lockNote" else "", r.centerX(),
            r.top + 150 * u, 34 * u, 0xFFFFFFFF.toInt(), align = Paint.Align.CENTER)
        targets.add(r to Hit.Button("pin"))
    }

    private val buttons = listOf("up" to "CH ▲", "down" to "CH ▼", "pad" to "123", "guide" to "GUIDE",
        "menu" to "MENU", "last" to "LAST")

    private fun drawControls(c: Canvas, w: Float, h: Float) {
        val bh = 110 * u
        val gap = 24 * u
        val bw = min(250 * u, (w - 120 * u - gap * (buttons.size - 1)) / buttons.size)
        var x = (w - (bw * buttons.size + gap * (buttons.size - 1))) / 2
        val top = (if (banner != null && SystemClock.uptimeMillis() < bannerUntil)
            h - 290 * u else h - 50 * u) - bh
        for ((key, label) in buttons) {
            val r = RectF(x, top, x + bw, top + bh)
            p.reset(); p.isAntiAlias = true
            p.color = Brand.PANEL_TOP; c.drawRoundRect(r, 16 * u, 16 * u, p)
            p.style = Paint.Style.STROKE; p.strokeWidth = 2.5f * u; p.color = Brand.ACCENT
            c.drawRoundRect(r, 16 * u, 16 * u, p)
            text(c, label, r.centerX(), r.centerY() + 14 * u, 40 * u, 0xFFFFFFFF.toInt(), align = Paint.Align.CENTER)
            targets.add(r to Hit.Button(key))
            x += bw + gap
        }
    }

    private fun drawPad(c: Canvas, w: Float, h: Float, mode: String) {
        p.reset(); p.color = 0x99000000.toInt(); c.drawRect(0f, 0f, w, h, p)
        targets.add(RectF(0f, 0f, w, h) to Hit.Outside)
        val pw = 640 * u
        val ph = 900 * u
        val r = RectF((w - pw) / 2, (h - ph) / 2, (w + pw) / 2, (h + ph) / 2)
        panel(c, r, if (mode == "pin") Brand.ACCENT else Brand.SECOND)
        targets.add(r to Hit.PadKey(""))                     // taps on the panel's edge do nothing
        text(c, if (mode == "pin") "ENTER PIN" else "CHANNEL", r.left + 60 * u, r.top + 80 * u, 50 * u,
            Brand.SECOND, numbers = true)
        val shown = if (mode == "pin") "●".repeat(padDigits.length) else padDigits
        text(c, shown.ifEmpty { "_" }, r.right - 60 * u, r.top + 82 * u, 64 * u, Brand.ACCENT, numbers = mode != "pin",
            align = Paint.Align.RIGHT, glow = Brand.ACCENT)
        val keys = listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "OK")
        val kw = (pw - 120 * u - 2 * 24 * u) / 3
        val kh = 150 * u
        for ((i, k) in keys.withIndex()) {
            val kx = r.left + 60 * u + (i % 3) * (kw + 24 * u)
            val ky = r.top + 130 * u + (i / 3) * (kh + 24 * u)
            val kr = RectF(kx, ky, kx + kw, ky + kh)
            p.reset(); p.isAntiAlias = true
            p.color = if (k == "OK") Brand.SELECT else 0x22FFFFFF
            c.drawRoundRect(kr, 14 * u, 14 * u, p)
            p.style = Paint.Style.STROKE; p.strokeWidth = 2 * u; p.color = Brand.DIM
            c.drawRoundRect(kr, 14 * u, 14 * u, p)
            text(c, k, kr.centerX(), kr.centerY() + 22 * u, 64 * u, 0xFFFFFFFF.toInt(), align = Paint.Align.CENTER)
            targets.add(kr to Hit.PadKey(k))
        }
    }

    fun guideRowHeight() = 116 * u

    private fun drawGuide(c: Canvas, w: Float, h: Float, rows: List<GuideRow>) {
        p.reset(); p.color = 0xF0000000.toInt(); c.drawRect(0f, 0f, w, h, p)
        targets.add(RectF(0f, 0f, w, h) to Hit.Outside)
        text(c, "GUIDE", 60 * u, 110 * u, 80 * u, Brand.ACCENT, numbers = true, glow = Brand.ACCENT)
        text(c, java.time.LocalTime.now().format(java.time.format.DateTimeFormatter.ofPattern("h:mm a")),
            w - 60 * u, 100 * u, 48 * u, Brand.SECOND, align = Paint.Align.RIGHT)
        val top = 160 * u
        val rh = guideRowHeight()
        val maxScroll = max(0f, rows.size * rh - (h - top - 20 * u))
        guideScroll = guideScroll.coerceIn(0f, maxScroll)
        c.save()
        c.clipRect(0f, top, w, h)
        for ((i, row) in rows.withIndex()) {
            val y = top + i * rh - guideScroll
            if (y + rh < top || y > h) continue
            val r = RectF(40 * u, y + 6 * u, w - 40 * u, y + rh - 6 * u)
            p.reset()
            p.color = if (i == guideSel) Brand.SELECT else 0x16FFFFFF
            c.drawRect(r, p)
            if (i == guideSel) { p.color = Brand.ACCENT; c.drawRect(r.left, r.top, r.left + 6 * u, r.bottom, p) }
            text(c, row.number.toString(), r.left + 150 * u, r.top + 78 * u, 64 * u, Brand.ACCENT, numbers = true,
                align = Paint.Align.RIGHT)
            text(c, ellipsize(row.name, 30 * u, 260 * u), r.left + 190 * u, r.top + 48 * u, 30 * u, Brand.DIM)
            val nx = r.left + 190 * u
            val nw = r.width() - 230 * u
            text(c, ellipsize(row.now.ifEmpty { "…" }, 42 * u, nw * 0.55f), nx, r.top + 92 * u, 42 * u, 0xFFFFFFFF.toInt())
            text(c, ellipsize(row.next, 32 * u, nw * 0.42f), r.right - 30 * u, r.top + 90 * u, 32 * u, Brand.SECOND,
                align = Paint.Align.RIGHT)
            targets.add(RectF(r.left, max(r.top, top), r.right, r.bottom) to Hit.GuideRowHit(i))
        }
        c.restore()
    }

    private fun drawMenu(c: Canvas, w: Float, h: Float, rows: List<MenuRow>) {
        p.reset(); p.color = 0x78000000; c.drawRect(0f, 0f, w, h, p)
        targets.add(RectF(0f, 0f, w, h) to Hit.Outside)
        val rh = 96 * u
        val pw = min(w - 80 * u, 1100 * u)
        val ph = 170 * u + rows.size * rh
        val r = RectF((w - pw) / 2, (h - ph) / 2, (w + pw) / 2, (h + ph) / 2)
        panel(c, r)
        targets.add(r to Hit.PadKey(""))
        text(c, "MENU", r.left + 70 * u, r.top + 90 * u, 64 * u, Brand.SECOND, numbers = true, glow = Brand.ACCENT)
        var y = r.top + 130 * u
        for ((i, row) in rows.withIndex()) {
            val box = RectF(r.left + 40 * u, y, r.right - 70 * u, y + rh - 10 * u)
            p.reset()
            if (i == menuSel) {
                p.color = Brand.SELECT; c.drawRect(box, p)
                p.color = Brand.ACCENT; c.drawRect(box.left, box.top, box.left + 6 * u, box.bottom, p)
            }
            val cy = box.centerY() + 13 * u
            text(c, row.label, box.left + 26 * u, cy, 36 * u, if (i == menuSel) Brand.SECOND else Brand.DIM)
            val vx = box.left + 290 * u
            val vw = box.right - vx - 70 * u
            val s = row.slider
            if (s != null) {
                val bar = RectF(vx, box.centerY() - 10 * u, vx + vw * 0.62f, box.centerY() + 10 * u)
                p.color = 0x33FFFFFF; c.drawRect(bar, p)
                p.shader = LinearGradient(bar.left, 0f, bar.right, 0f, Brand.SECOND, Brand.ACCENT, Shader.TileMode.CLAMP)
                c.drawRect(bar.left, bar.top, bar.left + bar.width() * s.coerceIn(0f, 1f), bar.bottom, p)
                p.shader = null
                text(c, row.value, bar.right + 30 * u, cy, 38 * u, 0xFFFFFFFF.toInt())
            } else {
                text(c, ellipsize(row.value, 38 * u, vw), vx, cy, 38 * u,
                    if (row.active) 0xFFFFFFFF.toInt() else Brand.DIM)
            }
            if (row.active && row.value.isNotEmpty()) {
                text(c, "◂", vx - 36 * u, cy, 40 * u, Brand.HILITE, align = Paint.Align.CENTER)
                text(c, "▸", box.right - 20 * u, cy, 40 * u, Brand.HILITE, align = Paint.Align.CENTER)
            }
            val mid = vx + (box.right - vx) / 2
            targets.add(RectF(box.left, box.top, mid, box.bottom) to Hit.MenuChange(i, -1))
            targets.add(RectF(mid, box.top, box.right + 60 * u, box.bottom) to Hit.MenuChange(i, 1))
            y += rh
        }
        text(c, "Tap ◂ ▸ to change", r.left + 70 * u, r.bottom - 26 * u, 28 * u, Brand.DIM)
    }

    private fun drawInfo(c: Canvas, lines: List<String>) {
        val size = 26 * u
        val lh = size * 1.35f
        val r = RectF(30 * u, 30 * u, 30 * u + 880 * u, 30 * u + 30 * u + lines.size * lh)
        p.reset(); p.color = 0xB4000000.toInt(); c.drawRect(r, p)
        for ((i, s) in lines.withIndex())
            text(c, ellipsize(s, size, r.width() - 40 * u), r.left + 20 * u, r.top + 15 * u + (i + 1) * lh - size * 0.3f,
                size, if (i == 0) Brand.HILITE else 0xFFFFFFFF.toInt())
    }

    private fun drawMessage(c: Canvas, w: Float, h: Float) {
        tp.typeface = Brand.text
        tp.textSize = 38 * u
        val tw = min(tp.measureText(message) + 80 * u, w - 80 * u)
        val r = RectF((w - tw) / 2, 50 * u, (w + tw) / 2, 130 * u)
        p.reset(); p.isAntiAlias = true; p.color = 0xD0000000.toInt()
        c.drawRoundRect(r, 18 * u, 18 * u, p)
        text(c, ellipsize(message, 38 * u, tw - 60 * u), r.centerX(), r.centerY() + 13 * u, 38 * u,
            0xFFFFFFFF.toInt(), align = Paint.Align.CENTER)
    }
}
