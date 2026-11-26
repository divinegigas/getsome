package app.neoncable42.box

import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.GestureDetector
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.SurfaceView
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.FrameLayout
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.time.Duration
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.abs
import kotlin.math.max

/** TV mode on a phone or tablet: a cable box for the PC that shares the channels. Like the desktop's
 *  TV mode in cable-box mode (getsome/tv/app.py): asks the PC what is on right now, streams that file
 *  from the right second, and moves on when the segment ends; static and hiss cover channel changes. */
class TvActivity : Activity(), Player.Listener {

    data class Ch(val number: Int, val name: String, val guide: Boolean = false)

    data class Restriction(val mode: String, val from: Int, val to: Int) {
        /** As model.Channel.locked_at: "always", or locked except from `from` to `to` o'clock. */
        fun lockedAt(hour: Int): Boolean {
            if (mode != "hours" || from == to) return true
            val free = Math.floorMod(hour - from, 24) < Math.floorMod(to - from, 24)
            return !free
        }
    }

    data class Seg(val path: String, val fileEnd: Double, val startsAt: LocalDateTime, val endsAt: LocalDateTime,
                   val skip: Double, val title: String, val contentType: String, val mediaType: String) {
        fun positionAt(t: LocalDateTime) = skip + HeadEnd.secondsBetween(startsAt, t)
        val isImage get() = mediaType == "image" || path.lowercase(Locale.ROOT).let {
            it.endsWith(".png") || it.endsWith(".jpg") || it.endsWith(".jpeg")
        }

        companion object {
            fun from(j: JSONObject) = Seg(j.getString("path"), j.optDouble("file_end", 0.0),
                HeadEnd.parse(j.getString("starts_at")), HeadEnd.parse(j.getString("ends_at")),
                j.optDouble("skip", 0.0), j.optString("title"), j.optString("content_type", "feature"),
                j.optString("media_type", "video"))
        }
    }

    private lateinit var prefs: Prefs
    private lateinit var he: HeadEnd
    private lateinit var player: Player
    private lateinit var overlay: Overlay
    private lateinit var tones: Tones
    private lateinit var gestures: GestureDetector
    private val ui = Handler(Looper.getMainLooper())
    private val bg: ExecutorService = Executors.newFixedThreadPool(3)

    private var channels: List<Ch> = emptyList()
    private var index = 0
    private var prevIndex = 0
    private var token = 0
    private var staticShown = false
    private var tuneStartedAt = 0L
    private var seg: Seg? = null
    private var corrected = false
    private var prefsAppliedFor: Seg? = null
    private var timer: Runnable? = null
    private var restricted: Map<Int, Restriction> = emptyMap()
    private var pin = "69"
    private var relockMinutes = 120
    private var unlocked = false
    private var awaySince: Long? = null
    private var bannerData: Overlay.Banner? = null
    private var entry = ""
    private var backAt = 0L
    private var running = false

    private val time: DateTimeFormatter = DateTimeFormatter.ofPattern("h:mm a", Locale.getDefault())

    // ================================================================ lifecycle
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        if (!prefs.paired) {
            startActivity(Intent(this, PairActivity::class.java))
            finish()
            return
        }
        Brand.load(this)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes = window.attributes.apply {
                layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }
        volumeControlStream = AudioManager.STREAM_MUSIC
        val root = FrameLayout(this)
        root.setBackgroundColor(0xFF000000.toInt())
        val surface = SurfaceView(this)
        overlay = Overlay(this)
        root.addView(surface, FrameLayout.LayoutParams(-1, -1))
        root.addView(overlay, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)
        hideSystemUi()

        tones = Tones()
        player = Player(this, this)
        surface.holder.addCallback(player)
        he = prefs.headEnd()
        gestures = GestureDetector(this, Touch())
    }

    override fun onStart() {
        super.onStart()
        if (!::player.isInitialized) return
        running = true
        overlay.staticOn = true
        staticShown = true
        tuneStartedAt = SystemClock.uptimeMillis()
        tones.hiss(true)
        overlay.invalidate()
        loadInfo(first = true)
        ui.postDelayed(tick, 30_000)
    }

    override fun onStop() {
        super.onStop()
        if (!::player.isInitialized) return
        running = false
        token++
        ui.removeCallbacksAndMessages(null)
        timer = null
        player.stop()
        tones.hiss(false)
        tones.tone(false)
        if (channels.isNotEmpty()) prefs.lastChannel = channels[index].number
    }

    override fun onDestroy() {
        if (::player.isInitialized) {
            player.destroy()
            tones.release()
        }
        bg.shutdownNow()
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    @Suppress("DEPRECATION")
    private fun hideSystemUi() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.let {
                it.hide(WindowInsets.Type.systemBars())
                it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN)
        }
    }

    // ================================================================ background work
    private fun <T> work(tk: Int, job: () -> T, done: (Result<T>) -> Unit) {
        try {
            bg.execute {
                val r = runCatching(job)
                ui.post { if (tk == token && running) done(r) }
            }
        } catch (e: java.util.concurrent.RejectedExecutionException) {
            // shutting down
        }
    }

    private fun later(tk: Int, ms: Long, what: () -> Unit) {
        timer?.let { ui.removeCallbacks(it) }
        val r = Runnable { if (tk == token && running) what() }
        timer = r
        ui.postDelayed(r, max(50L, ms))
    }

    private fun lostPairing() {
        prefs.forget()
        startActivity(Intent(this, PairActivity::class.java)
            .putExtra(PairActivity.NOTE, "${prefs.pcName.ifEmpty { "The PC" }} no longer knows this device. Pair it again."))
        finish()
    }

    @Suppress("UNUSED_PARAMETER")
    private fun unreachable(e: Throwable): String =
        "Can't reach ${prefs.pcName.ifEmpty { "the PC" }}: is it on, with sharing switched on? Trying again…"

    // ================================================================ line-up
    private fun loadInfo(first: Boolean) {
        val tk = token
        work(tk, { he.obj("info") }) { r ->
            val e = r.exceptionOrNull()
            if (e is HeadEnd.NotPaired) return@work lostPairing()
            val info = r.getOrNull()
            if (info == null) {
                overlay.showMessage(unreachable(e ?: IOException()), 5.0)
                later(tk, 5000) { loadInfo(first) }
                return@work
            }
            applyInfo(info)
            if (channels.isEmpty()) {
                overlay.staticOn = false
                tones.hiss(false)
                overlay.bars = true
                overlay.barsNote = "No channels yet: build some on the PC"
                overlay.invalidate()
                later(tk, 60_000) { loadInfo(first) }
                return@work
            }
            if (first) {
                val last = channels.indexOfFirst { it.number == prefs.lastChannel && !it.guide }
                tune(if (last >= 0) last else channels.indexOfFirst { !it.guide }.coerceAtLeast(0))
            }
        }
    }

    private fun applyInfo(info: JSONObject) {
        val out = ArrayList<Ch>()
        val list = info.optJSONArray("channels") ?: JSONArray()
        for (i in 0 until list.length()) {
            val c = list.getJSONObject(i)
            if (c.optString("network_type") == "standard" && !c.optBoolean("hidden"))
                out.add(Ch(c.getInt("number"), c.optString("name")))
        }
        val g = info.optInt("guide_channel", 0)
        if (g > 0 && out.none { it.number == g }) out.add(Ch(g, "Guide", guide = true))
        out.sortBy { it.number }
        val keep = channels.getOrNull(index)?.number
        channels = out
        if (keep != null) index = channels.indexOfFirst { it.number == keep }.coerceAtLeast(0)
        val rs = HashMap<Int, Restriction>()
        val ra = info.optJSONArray("restricted") ?: JSONArray()
        for (i in 0 until ra.length()) {
            val r = ra.getJSONObject(i)
            rs[r.getInt("number")] = Restriction(r.optString("lock_mode", "always"), r.optInt("free_from", 21),
                r.optInt("free_to", 6))
        }
        restricted = rs
        pin = info.optString("restricted_pin", "69")
        relockMinutes = info.optInt("relock_minutes", 120)
    }

    // ================================================================ tuning
    private fun tune(i: Int) {
        if (channels.isEmpty()) return
        val to = Math.floorMod(i, channels.size)
        if (to != index) prevIndex = index
        index = to
        val ch = channels[index]
        prefs.lastChannel = ch.number
        token++
        val tk = token
        timer?.let { ui.removeCallbacks(it) }
        seg = null
        bannerData = null
        overlay.card = null
        overlay.bars = false
        tones.tone(false)
        overlay.number = ch.number.toString()
        overlay.numberUntil = SystemClock.uptimeMillis() + 4000
        overlay.pad = null
        overlay.menu = null
        if (ch.guide) {
            player.stop()
            endStatic()
            applyLock()
            openGuide()
            return
        }
        overlay.guide = null
        staticShown = true
        tuneStartedAt = SystemClock.uptimeMillis()
        overlay.staticOn = true
        tones.hiss(true)
        applyLock()
        overlay.invalidate()
        loadNow(tk)
        fetchBanner(tk, ch)
    }

    private fun step(d: Int) {
        if (channels.isNotEmpty()) tune(index + d)
    }

    private fun loadNow(tk: Int) {
        val ch = channels.getOrNull(index) ?: return
        work(tk, { he.obj("segment_at", ch.name, HeadEnd.iso(LocalDateTime.now())) }) { r ->
            val e = r.exceptionOrNull()
            when {
                e is HeadEnd.NotPaired -> lostPairing()
                e != null -> {
                    overlay.showMessage(unreachable(e), 4.0)
                    later(tk, 5000) { loadNow(tk) }
                }
                else -> onSegment(tk, ch, r.getOrNull() ?: JSONObject())
            }
        }
    }

    private fun onSegment(tk: Int, ch: Ch, j: JSONObject) {
        if (j.has("nosignal") || !j.has("path")) {
            offAir(tk, ch)
            return
        }
        val s = try {
            Seg.from(j)
        } catch (e: Exception) {
            offAir(tk, ch)
            return
        }
        seg = s
        corrected = false
        overlay.bars = false
        tones.tone(false)
        overlay.card = null
        val url = he.fileUrl(s.path)
        val now = LocalDateTime.now()
        when {
            s.path.endsWith("intermission.png") -> {
                player.stop()
                intermission(tk, ch, s)
                finishTuning(tk)
            }
            s.isImage -> player.load(url, null, null, false)
            s.contentType == "off_air" -> player.load(url, s.positionAt(now), null, true)
            else -> player.load(url, s.positionAt(now), s.fileEnd, false)
        }
        applyVolume()
        player.setZoom(if (s.contentType == "feature") prefs.zoom else "normal")
        generatedCard(tk, ch, s)
        overlay.invalidate()
        // the next show / bumper / commercial on this channel: no static in between
        later(tk, Duration.between(LocalDateTime.now(), s.endsAt).toMillis()) { loadNow(tk) }
    }

    private fun offAir(tk: Int, ch: Ch) {
        seg = null
        player.stop()
        overlay.card = null
        overlay.bars = true
        overlay.barsNote = "${ch.number}  ${ch.name}  ·  Off the air"
        endStatic()
        tones.tone(!overlay.locked)
        overlay.invalidate()
        later(tk, 60_000) { loadNow(tk) }
    }

    private fun finishTuning(tk: Int) {
        if (!staticShown) return
        val wait = 350 - (SystemClock.uptimeMillis() - tuneStartedAt)
        ui.postDelayed({
            if (tk == token && staticShown) {
                endStatic()
                showBanner()
            }
        }, max(0L, wait))
    }

    private fun endStatic() {
        staticShown = false
        overlay.staticOn = false
        tones.hiss(false)
        overlay.invalidate()
    }

    override fun onPlaybackRestart() {
        if (!running) return
        finishTuning(token)
        val s = seg ?: return
        if (prefsAppliedFor !== s) {
            prefsAppliedFor = s
            applyTrackPrefs()
        }
        // live: if the stream started somewhere else (slow start, keyframes), catch up once
        if (!corrected && !s.isImage && s.contentType != "off_air") {
            val expected = s.positionAt(LocalDateTime.now())
            val pos = player.position
            if (pos != null && abs(pos - expected) > MAX_DRIFT && expected < s.fileEnd - 1) {
                corrected = true
                player.seek(expected)
            }
        }
    }

    override fun onEndFile(reason: Int, error: Int) {
        if (!running || reason != END_ERROR || seg == null) return
        val tk = token
        overlay.showMessage("Couldn't play this from ${prefs.pcName.ifEmpty { "the PC" }}. Trying again…", 4.0)
        later(tk, 4000) { loadNow(tk) }
    }

    // ================================================================ sound, lock
    private fun applyVolume() {
        player.setVolume(if (seg?.contentType == "commercial") prefs.adVolume.toDouble() else 100.0)
    }

    private fun isLocked(ch: Ch?): Boolean {
        if (unlocked || ch == null || ch.guide) return false
        return restricted[ch.number]?.lockedAt(LocalDateTime.now().hour) == true
    }

    private fun applyLock() {
        val ch = channels.getOrNull(index)
        val locked = isLocked(ch)
        overlay.locked = locked
        val r = ch?.let { restricted[it.number] }
        overlay.lockNote = if (locked && r != null && r.mode == "hours" && r.from != r.to) "Opens by itself at ${hourText(r.from)}" else ""
        // blurred like the desktop; if this phone's player can't blur, the veil hides the picture instead
        overlay.lockOpaque = !player.setFilter(if (locked) BLUR_VF else "")
        player.setMute(locked)
        if (locked) tones.tone(false)
        overlay.invalidate()
    }

    private fun hourText(h: Int): String {
        val x = Math.floorMod(h, 24)
        return "${if (x % 12 == 0) 12 else x % 12} ${if (x < 12) "AM" else "PM"}"
    }

    /** Every 30 s: the lock schedule (a channel can open or close on the hour) and the silent relock
     *  after relock_minutes away from restricted channels, as on the desktop. */
    private val tick: Runnable = object : Runnable {
        override fun run() {
            if (!running) return
            val ch = channels.getOrNull(index)
            if (unlocked && relockMinutes > 0 && ch != null && ch.number !in restricted) {
                val now = SystemClock.uptimeMillis()
                val since = awaySince ?: now.also { awaySince = it }
                if (now - since >= relockMinutes * 60_000L) {
                    unlocked = false
                    awaySince = null
                }
            } else {
                awaySince = null
            }
            if (isLocked(ch) != overlay.locked) applyLock()
            if (overlay.guide != null) refreshGuide()
            ui.postDelayed(this, 30_000)
        }
    }

    // ================================================================ banner
    private fun prog(j: JSONObject?): Triple<String, String, String>? {
        if (j == null) return null
        val ep = j.optJSONArray("ep")
        val code = ep?.optString(0).orEmpty()
        val name = ep?.optString(1).orEmpty()
        val start = runCatching { HeadEnd.parse(j.getString("start")) }.getOrNull()
        val end = runCatching { HeadEnd.parse(j.getString("end")) }.getOrNull()
        val times = if (start != null && end != null) "${start.format(time)} – ${end.format(time)}" else ""
        return Triple(j.optString("title"), listOf(code, name).filter { it.isNotEmpty() }.joinToString("  ·  "), times)
    }

    private fun fetchBanner(tk: Int, ch: Ch) {
        work(tk, { he.arr("now_and_next", ch.name, HeadEnd.iso(LocalDateTime.now())) }) { r ->
            val a = r.getOrNull() ?: return@work
            val now = prog(a.optJSONObject(0))
            val next = a.optJSONObject(1)
            val nextLine = next?.let {
                val t = runCatching { HeadEnd.parse(it.getString("start")).format(time) }.getOrDefault("")
                "Next: ${it.optString("title")}" + if (t.isNotEmpty()) "  at $t" else ""
            } ?: ""
            bannerData = Overlay.Banner("${ch.number}  ${ch.name.uppercase(Locale.getDefault())}",
                now?.first?.ifEmpty { null } ?: seg?.title ?: ch.name,
                listOf(now?.second.orEmpty(), now?.third.orEmpty()).filter { it.isNotEmpty() }.joinToString("     "),
                nextLine)
            if (!staticShown) showBanner()
        }
    }

    private fun showBanner(seconds: Long = 6) {
        val b = bannerData ?: return
        overlay.banner = b
        overlay.bannerUntil = SystemClock.uptimeMillis() + seconds * 1000
        overlay.invalidate()
    }

    // ================================================================ cards over the made-up clips
    private fun loadImage(path: String?): Bitmap? {
        if (path.isNullOrEmpty()) return null
        return try {
            val c = URL(he.fileUrl(path)).openConnection() as HttpURLConnection
            c.connectTimeout = 4000
            c.readTimeout = 10000
            val bytes = c.inputStream.use { it.readBytes() }
            c.disconnect()
            val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
            var sample = 1
            while (o.outWidth / (sample * 2) >= 1280) sample *= 2
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
        } catch (e: Exception) {
            null
        }
    }

    private fun art(path: String?): JSONObject = if (path.isNullOrEmpty()) JSONObject()
        else runCatching { he.obj("artwork", path) }.getOrNull() ?: JSONObject()

    private fun firstOf(j: JSONObject, vararg keys: String): String? =
        keys.map { j.optString(it) }.firstOrNull { it.isNotEmpty() && it != "null" }

    private fun nextAfter(ch: Ch, s: Seg): JSONObject? =
        he.arr("now_and_next", ch.name, HeadEnd.iso(s.endsAt.plusSeconds(2)))?.optJSONObject(0)

    private fun show(tk: Int, s: Seg, card: Overlay.Card?) {
        if (tk == token && seg === s && card != null) {
            overlay.card = card
            overlay.invalidate()
        }
    }

    private fun generatedCard(tk: Int, ch: Ch, s: Seg) {
        val path = s.path
        val upnext = Regex("/upnext/upnext_(\\d+)\\.mp4$").find(path)
        val bump = Regex("/breakbumps/(?:pre|post)/(brb|back)_(\\d+)\\.mp4$").find(path)
        val ad = Regex("/ads/[^/]+/(ad|id)_(\\d+)\\.mp4$").find(path)
        val card = Regex("/ads/[^/]+/(card_[^/]+\\.mp4)$").find(path)
        val promo = Regex("/ads/[^/]+/(promo_[^/]+\\.mp4)$").find(path)
        val chLine = "Channel ${ch.number}  ·  ${ch.name}"
        when {
            upnext != null -> work(tk, {
                val n = nextAfter(ch, s)
                val p = prog(n)
                val a = art(n?.optString("path"))
                Overlay.Card("UP NEXT", p?.first ?: "More great TV",
                    listOfNotNull(p?.second?.ifEmpty { null }, n?.let { "at " + HeadEnd.parse(it.getString("start")).format(time) })
                        .joinToString("\n"), loadImage(firstOf(a, "fanart", "landscape", "still")))
            }) { show(tk, s, it.getOrNull()) }
            bump != null -> work(tk, {
                val now = prog(he.arr("now_and_next", ch.name, HeadEnd.iso(s.endsAt.plusSeconds(2)))?.optJSONObject(0))
                if (bump.groupValues[1] == "brb") Overlay.Card(chLine.uppercase(Locale.getDefault()), "We'll be right back", now?.first.orEmpty())
                else Overlay.Card("AND NOW, BACK TO", now?.first ?: ch.name, now?.second.orEmpty())
            }) { show(tk, s, it.getOrNull()) }
            ad != null -> show(tk, s, if (ad.groupValues[1] == "id")
                Overlay.Card("YOU'RE WATCHING", Brand.APP_NAME, chLine)
                else Overlay.Card("A WORD FROM OUR SPONSORS", "Stay tuned", "$chLine\nWe'll be right back after these messages."))
            card != null -> work(tk, { cardFor(ch, path) }) { show(tk, s, it.getOrNull()) }
            promo != null -> work(tk, {
                val d = he.obj("promo_for", path) ?: JSONObject()
                Overlay.Card("COMING UP ON CHANNEL ${ch.number}", d.optString("title"), d.optString("plot"),
                    loadImage(firstOf(d, "fanart", "poster")), poster = d.optString("fanart").isEmpty())
            }) { show(tk, s, it.getOrNull()) }
        }
    }

    private fun cardFor(ch: Ch, path: String): Overlay.Card? {
        val d = he.obj("card_for", path) ?: return null
        val title = d.optString("title")
        return when (d.optString("kind")) {
            "promo" -> Overlay.Card("COMING UP ON CHANNEL ${ch.number}", title,
                listOf(d.optString("year"), d.optString("plot")).filter { it.isNotEmpty() && it != "null" }.joinToString("  ·  "),
                loadImage(firstOf(d, "fanart", "poster")), poster = firstOf(d, "fanart") == null)
            "spotlight", "stills" -> Overlay.Card("SPOTLIGHT", title, "Only on channel ${ch.number}",
                loadImage(firstOf(d, "fanart", "logo")))
            "trivia", "film_fact" -> Overlay.Card("DID YOU KNOW?", title, d.optString("fact"),
                loadImage(firstOf(d, "poster", "fanart")), poster = firstOf(d, "poster") != null)
            "lineup" -> {
                val now = LocalDateTime.now()
                val ps = he.arr("programmes", ch.name, HeadEnd.iso(now), HeadEnd.iso(now.plusHours(6)), 6) ?: JSONArray()
                val lines = (0 until ps.length()).mapNotNull { i ->
                    ps.optJSONObject(i)?.let { "${HeadEnd.parse(it.getString("start")).format(time)}    ${it.optString("title")}" }
                }
                Overlay.Card("COMING UP ON CHANNEL ${ch.number}", ch.name, lines.joinToString("\n"))
            }
            "wall" -> {
                val posters = d.optJSONArray("posters")
                Overlay.Card(Brand.APP_NAME.uppercase(Locale.getDefault()), "All on channel ${ch.number}", ch.name,
                    loadImage(posters?.optString(0)), poster = true)
            }
            "onthisday" -> {
                val o = he.arr("on_this_day", path) ?: return null
                val items = o.optJSONArray(1) ?: JSONArray()
                if (items.length() == 0) return null
                val lines = (0 until minOf(4, items.length())).mapNotNull { i ->
                    items.optJSONObject(i)?.let { x ->
                        listOf(x.optString("date").take(4), x.optString("show"), x.optString("title"))
                            .filter { it.isNotEmpty() && it != "null" }.joinToString("  ·  ")
                    }
                }
                Overlay.Card(o.optString(0), "", lines.joinToString("\n"),
                    loadImage(items.optJSONObject(0)?.let { firstOf(it, "fanart", "poster") }))
            }
            else -> if (title.isNotEmpty()) Overlay.Card("COMING UP ON CHANNEL ${ch.number}", title, "") else null
        }
    }

    /** A movie channel's wait before the next film (drawn live on the desktop): when it starts. */
    private fun intermission(tk: Int, ch: Ch, s: Seg) {
        overlay.card = Overlay.Card("FEATURE PRESENTATION", ch.name, "")
        work(tk, {
            val n = nextAfter(ch, s)
            val a = art(n?.optString("path"))
            Overlay.Card("FEATURE PRESENTATION", n?.optString("title") ?: ch.name,
                n?.let { "Starts at " + HeadEnd.parse(it.getString("start")).format(time) }.orEmpty(),
                loadImage(firstOf(a, "poster", "fanart")), poster = firstOf(a, "poster") != null)
        }) { show(tk, s, it.getOrNull()) }
    }

    // ================================================================ guide
    private fun openGuide() {
        val rows = channels.filter { !it.guide }.map { Overlay.GuideRow(it.number, it.name, "", "") }
        val cur = channels.getOrNull(index)
        overlay.guide = rows
        overlay.guideSel = rows.indexOfFirst { it.number == cur?.number }.coerceAtLeast(0)
        overlay.guideScroll = max(0f, overlay.guideSel * overlay.guideRowHeight() - overlay.guideRowHeight() * 2)
        overlay.invalidate()
        refreshGuide()
    }

    private fun refreshGuide() {
        val list = channels.filter { !it.guide }
        val tk = token
        work(tk, {
            val now = LocalDateTime.now()
            list.map { ch ->
                val a = runCatching { he.arr("now_and_next", ch.name, HeadEnd.iso(now)) }.getOrNull()
                val n = a?.optJSONObject(0)
                val x = a?.optJSONObject(1)
                Overlay.GuideRow(ch.number, ch.name, n?.optString("title").orEmpty().ifEmpty { "Off the air" },
                    x?.let { "${HeadEnd.parse(it.getString("start")).format(time)}  ${it.optString("title")}" }.orEmpty())
            }
        }) { r ->
            val rows = r.getOrNull() ?: return@work
            if (overlay.guide != null) {
                overlay.guide = rows
                overlay.invalidate()
            }
        }
    }

    private fun closeGuide() {
        overlay.guide = null
        val ch = channels.getOrNull(index)
        if (ch != null && ch.guide) {         // leaving the guide channel: back where we were
            tune(if (channels.getOrNull(prevIndex)?.guide == false) prevIndex else channels.indexOfFirst { !it.guide })
        }
        overlay.invalidate()
    }

    private fun guidePick(i: Int) {
        val row = overlay.guide?.getOrNull(i) ?: return
        overlay.guide = null
        val to = channels.indexOfFirst { it.number == row.number }
        if (to >= 0) tune(to)
    }

    private fun guideMove(d: Int) {
        val rows = overlay.guide ?: return
        overlay.guideSel = (overlay.guideSel + d).coerceIn(0, rows.size - 1)
        val rh = overlay.guideRowHeight()
        val top = overlay.guideSel * rh
        val visible = overlay.height - 180 * (overlay.height / 1080f)
        if (top < overlay.guideScroll) overlay.guideScroll = top
        if (top + rh > overlay.guideScroll + visible) overlay.guideScroll = top + rh - visible
        overlay.invalidate()
    }

    // ================================================================ menu
    private val menuKeys = listOf("audio", "subs", "picture", "ads", "pc", "close")
    private val zooms = listOf("normal" to "Normal", "zoom" to "Zoom (fill, no bars)", "stretch" to "Stretch to fill")

    private fun trackLabel(t: Player.Track): String {
        val lang = Locale.forLanguageTag(t.lang).displayLanguage.ifEmpty { t.lang.uppercase(Locale.ROOT) }.ifEmpty { "Unknown" }
        return listOf(lang, t.title, t.codec.uppercase(Locale.ROOT)).filter { it.isNotEmpty() }.joinToString("  ·  ")
    }

    private fun buildMenu(): List<Overlay.MenuRow> {
        val tracks = runCatching { player.tracks() }.getOrDefault(emptyList())
        val audio = tracks.filter { it.type == "audio" }
        val subs = tracks.filter { it.type == "sub" }
        val a = audio.firstOrNull { it.selected }
        val s = subs.firstOrNull { it.selected }
        return listOf(
            Overlay.MenuRow("AUDIO", a?.let { trackLabel(it) } ?: "None", active = audio.size > 1),
            Overlay.MenuRow("SUBTITLES", s?.let { trackLabel(it) } ?: "Off", active = subs.isNotEmpty()),
            Overlay.MenuRow("PICTURE", zooms.firstOrNull { it.first == prefs.zoom }?.second ?: "Normal"),
            Overlay.MenuRow("ADS", if (prefs.adVolume == 0) "Silent" else "${prefs.adVolume}%", slider = prefs.adVolume / 100f),
            Overlay.MenuRow("FROM", prefs.pcName.ifEmpty { prefs.host } + "   (▸ pair again)"),
            Overlay.MenuRow("CLOSE", "", active = false),
        )
    }

    private fun openMenu() {
        overlay.menu = buildMenu()
        overlay.menuSel = 0
        overlay.invalidate()
    }

    private fun menuChange(row: Int, d: Int) {
        overlay.menuSel = row
        when (menuKeys[row]) {
            "audio", "subs" -> {
                val type = if (menuKeys[row] == "audio") "audio" else "sub"
                val list = runCatching { player.tracks() }.getOrDefault(emptyList()).filter { it.type == type }
                if (list.isEmpty()) return
                val opts: List<Player.Track?> = if (type == "sub") listOf(null) + list else list
                val cur = opts.indexOfFirst { it?.selected ?: (list.none { t -> t.selected }) }.coerceAtLeast(0)
                val pick = opts[Math.floorMod(cur + d, opts.size)]
                player.setTrack(type, pick?.id)
                if (type == "audio") prefs.audioLang = pick?.lang.orEmpty() else prefs.subLang = pick?.lang ?: "off"
            }
            "picture" -> {
                val i = zooms.indexOfFirst { it.first == prefs.zoom }.coerceAtLeast(0)
                prefs.zoom = zooms[Math.floorMod(i + d, zooms.size)].first
                if (seg?.contentType == "feature") player.setZoom(prefs.zoom)
            }
            "ads" -> {
                prefs.adVolume = (prefs.adVolume + d * 10).coerceIn(0, 100)
                applyVolume()
            }
            "pc" -> {
                startActivity(Intent(this, PairActivity::class.java).putExtra(PairActivity.FROM_TV, true))
                return
            }
            "close" -> {
                overlay.menu = null
                overlay.invalidate()
                return
            }
        }
        // track changes take a moment to show in mpv's list
        ui.postDelayed({ if (overlay.menu != null) { overlay.menu = buildMenu(); overlay.invalidate() } }, 150)
    }

    /** Preferred audio / subtitle languages carry on from file to file, as on the desktop. */
    private fun applyTrackPrefs() {
        val tracks = runCatching { player.tracks() }.getOrDefault(emptyList())
        val al = prefs.audioLang
        if (al.isNotEmpty()) tracks.firstOrNull { it.type == "audio" && it.lang == al && !it.selected }?.let { player.setTrack("audio", it.id) }
        when (val sl = prefs.subLang) {
            "" -> {}
            "off" -> player.setTrack("sub", null)
            else -> tracks.firstOrNull { it.type == "sub" && it.lang == sl }?.let { player.setTrack("sub", it.id) }
        }
    }

    // ================================================================ number and PIN pad
    private fun openPad(mode: String) {
        overlay.pad = mode
        overlay.padDigits = ""
        overlay.invalidate()
    }

    private fun padKey(k: String) {
        val mode = overlay.pad ?: return
        when (k) {
            "" -> return
            "⌫" -> overlay.padDigits = overlay.padDigits.dropLast(1)
            "OK" -> {
                val d = overlay.padDigits
                overlay.pad = null
                if (mode == "pin") {
                    if (d == pin) {
                        unlocked = true
                        awaySince = null
                        applyLock()
                    } else overlay.showMessage("That PIN isn't right", 2.5)
                } else if (d.isNotEmpty()) goTo(d.toInt())
            }
            else -> if (overlay.padDigits.length < (if (mode == "pin") 8 else 3)) overlay.padDigits += k
        }
        if (mode == "channel" && overlay.padDigits.length == 3) padKey("OK")
        overlay.invalidate()
    }

    private fun goTo(n: Int) {
        val i = channels.indexOfFirst { it.number == n }
        if (i >= 0) tune(i) else overlay.showMessage("There's no channel $n", 2.5)
    }

    private val commitEntry = Runnable {
        val n = entry.toIntOrNull()
        entry = ""
        if (n != null) goTo(n)
    }

    private fun digit(d: Int) {
        if (overlay.pad != null) return padKey(d.toString())
        entry += d
        overlay.number = entry
        overlay.numberUntil = SystemClock.uptimeMillis() + 2500
        overlay.invalidate()
        ui.removeCallbacks(commitEntry)
        if (entry.length >= 3) commitEntry.run() else ui.postDelayed(commitEntry, 1800)
    }

    // ================================================================ touch
    private fun showControls() {
        overlay.controlsUntil = SystemClock.uptimeMillis() + 6000
        showBanner()
        overlay.invalidate()
    }

    private fun button(key: String) {
        overlay.controlsUntil = SystemClock.uptimeMillis() + 6000
        when (key) {
            "up" -> step(1)
            "down" -> step(-1)
            "pad" -> openPad("channel")
            "guide" -> openGuide()
            "menu" -> openMenu()
            "last" -> tune(prevIndex)
            "pin" -> openPad("pin")
        }
    }

    private fun closeTop(): Boolean {
        when {
            overlay.pad != null -> overlay.pad = null
            overlay.menu != null -> overlay.menu = null
            overlay.guide != null -> closeGuide()
            overlay.controlsUntil > SystemClock.uptimeMillis() -> overlay.controlsUntil = 0
            else -> return false
        }
        overlay.invalidate()
        return true
    }

    private inner class Touch : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent) = true

        override fun onSingleTapUp(e: MotionEvent): Boolean {
            when (val h = overlay.hit(e.x, e.y)) {
                is Overlay.Hit.Button -> button(h.key)
                is Overlay.Hit.PadKey -> padKey(h.key)
                is Overlay.Hit.GuideRowHit -> guidePick(h.index)
                is Overlay.Hit.MenuChange -> menuChange(h.row, h.dir)
                is Overlay.Hit.Outside -> closeTop()
                null -> if (overlay.controlsUntil > SystemClock.uptimeMillis()) {
                    overlay.controlsUntil = 0
                    overlay.bannerUntil = 0
                    overlay.invalidate()
                } else showControls()
            }
            return true
        }

        override fun onScroll(e1: MotionEvent?, e2: MotionEvent, dx: Float, dy: Float): Boolean {
            if (overlay.guide == null) return false
            overlay.guideScroll += dy
            overlay.invalidate()
            return true
        }

        override fun onFling(e1: MotionEvent?, e2: MotionEvent, vx: Float, vy: Float): Boolean {
            if (overlay.guide != null || overlay.menu != null || overlay.pad != null) return false
            if (abs(vy) > abs(vx) && abs(vy) > 900) {
                step(if (vy < 0) 1 else -1)          // swipe up: channel up
                return true
            }
            return false
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean = gestures.onTouchEvent(event) || super.onTouchEvent(event)

    // ================================================================ keys (keyboards, TV remotes, game pads)
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (!::player.isInitialized) return super.onKeyDown(keyCode, event)
        val digit = when (keyCode) {
            in KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9 -> keyCode - KeyEvent.KEYCODE_0
            in KeyEvent.KEYCODE_NUMPAD_0..KeyEvent.KEYCODE_NUMPAD_9 -> keyCode - KeyEvent.KEYCODE_NUMPAD_0
            else -> -1
        }
        if (digit >= 0) {
            digit(digit)
            return true
        }
        val ok = keyCode in listOf(KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_NUMPAD_ENTER,
            KeyEvent.KEYCODE_BUTTON_A)
        val back = keyCode in listOf(KeyEvent.KEYCODE_BACK, KeyEvent.KEYCODE_ESCAPE, KeyEvent.KEYCODE_BUTTON_B)
        val up = keyCode in listOf(KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_CHANNEL_UP, KeyEvent.KEYCODE_PAGE_UP)
        val down = keyCode in listOf(KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_CHANNEL_DOWN, KeyEvent.KEYCODE_PAGE_DOWN)
        val left = keyCode == KeyEvent.KEYCODE_DPAD_LEFT
        val right = keyCode == KeyEvent.KEYCODE_DPAD_RIGHT
        val pad = overlay.pad
        val menu = overlay.menu
        when {
            pad != null -> when {
                back -> closeTop()
                ok -> padKey("OK")
                keyCode == KeyEvent.KEYCODE_DEL -> padKey("⌫")
                else -> return super.onKeyDown(keyCode, event)
            }
            menu != null -> when {
                back || keyCode == KeyEvent.KEYCODE_MENU -> closeTop()
                up -> { overlay.menuSel = Math.floorMod(overlay.menuSel - 1, menu.size); overlay.invalidate() }
                down -> { overlay.menuSel = Math.floorMod(overlay.menuSel + 1, menu.size); overlay.invalidate() }
                left -> menuChange(overlay.menuSel, -1)
                right || ok -> menuChange(overlay.menuSel, 1)
                else -> return super.onKeyDown(keyCode, event)
            }
            overlay.guide != null -> when {
                back || keyCode == KeyEvent.KEYCODE_GUIDE -> closeTop()
                up -> guideMove(-1)
                down -> guideMove(1)
                ok -> guidePick(overlay.guideSel)
                else -> return super.onKeyDown(keyCode, event)
            }
            up -> step(1)
            down -> step(-1)
            keyCode == KeyEvent.KEYCODE_MENU || keyCode == KeyEvent.KEYCODE_M || keyCode == KeyEvent.KEYCODE_BUTTON_START -> openMenu()
            keyCode == KeyEvent.KEYCODE_GUIDE || keyCode == KeyEvent.KEYCODE_G || keyCode == KeyEvent.KEYCODE_BUTTON_Y -> openGuide()
            keyCode == KeyEvent.KEYCODE_LAST_CHANNEL || keyCode == KeyEvent.KEYCODE_L || keyCode == KeyEvent.KEYCODE_BUTTON_X -> tune(prevIndex)
            ok || keyCode == KeyEvent.KEYCODE_INFO || keyCode == KeyEvent.KEYCODE_I ->
                if (overlay.locked) openPad("pin") else showControls()
            back -> {
                if (closeTop()) return true
                val now = SystemClock.uptimeMillis()
                if (now - backAt < 3000) finish() else {
                    backAt = now
                    overlay.showMessage("Press back again to switch off", 3.0)
                }
            }
            else -> return super.onKeyDown(keyCode, event)
        }
        return true
    }

    companion object {
        private const val MAX_DRIFT = 3.0
        private const val END_ERROR = 4          // MPV_END_FILE_REASON_ERROR
        // as the desktop's restricted-channel blur, made from scaling only (always in Android builds of ffmpeg)
        private const val BLUR_VF = "lavfi=[scale=48:-2:flags=area,scale=960:-2:flags=bicubic]"
    }
}
