package app.neoncable42.box

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.SurfaceHolder
import dev.jdtech.mpv.MPVLib
import java.util.Locale

/** libmpv on a SurfaceView: the same player as the desktop app. One file at a time (only the current
 *  channel decodes); mpv's events come back on the main thread. */
class Player(ctx: Context, private val listener: Listener) : SurfaceHolder.Callback, MPVLib.EventObserver {

    interface Listener {
        fun onPlaybackRestart()
        fun onEndFile(reason: Int, error: Int)
    }

    private val main = Handler(Looper.getMainLooper())
    private val mpv: MPVLib = MPVLib.create(ctx) ?: throw IllegalStateException("the video player (libmpv) did not load")
    private var hwdec = HWDEC

    init {
        val o = mapOf(
            "vo" to "gpu", "gpu-context" to "android", "opengl-es" to "yes",
            "hwdec" to HWDEC, "hwdec-codecs" to "h264,hevc,mpeg4,mpeg2video,vp8,vp9,av1",
            "ao" to "audiotrack,opensles", "idle" to "yes", "force-window" to "no", "keep-open" to "yes",
            "input-default-bindings" to "no", "image-display-duration" to "inf",
            // streaming from the PC: read well ahead so Wi-Fi hiccups don't show (as the desktop box does)
            "cache" to "yes", "demuxer-max-bytes" to "96MiB", "demuxer-max-back-bytes" to "16MiB",
            "demuxer-readahead-secs" to "30", "cache-secs" to "30", "network-timeout" to "20",
            "sub-scale" to "1.2", "save-position-on-quit" to "no",
        )
        for ((k, v) in o) mpv.setOptionString(k, v)
        mpv.init()
        mpv.addObserver(this)
    }

    // ---------------------------------------------------------------- surface
    override fun surfaceCreated(holder: SurfaceHolder) {
        mpv.attachSurface(holder.surface)
        mpv.setOptionString("force-window", "yes")
        mpv.setPropertyString("vo", "gpu")
    }

    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
        mpv.setPropertyString("android-surface-size", "${width}x$height")
    }

    override fun surfaceDestroyed(holder: SurfaceHolder) {
        mpv.setPropertyString("vo", "null")
        mpv.setOptionString("force-window", "no")
        mpv.detachSurface()
    }

    // ---------------------------------------------------------------- playback
    fun load(url: String, start: Double?, end: Double?, loop: Boolean) {
        mpv.setPropertyString("start", start?.let { fmt(it) } ?: "none")
        mpv.setPropertyString("end", end?.let { fmt(it) } ?: "none")
        mpv.setPropertyString("loop-file", if (loop) "inf" else "no")
        mpv.setPropertyBoolean("pause", false)
        stopped = false
        mpv.command(arrayOf("loadfile", url, "replace"))
    }

    private var stopped = true            // stopped on purpose (guide, off air): not a failure

    fun stop() {
        stopped = true
        mpv.command(arrayOf("stop"))
    }

    fun seek(seconds: Double) = mpv.command(arrayOf("seek", fmt(seconds), "absolute"))

    val position: Double? get() = mpv.getPropertyDouble("time-pos")

    fun setVolume(v: Double) = mpv.setPropertyDouble("volume", v)

    fun setMute(on: Boolean) = mpv.setPropertyBoolean("mute", on)

    /** Video filters (the restricted-channel blur). Filters need frames in memory: copy-back decoding.
     *  Returns false if mpv refused the filter (Android builds of ffmpeg carry few filters). */
    fun setFilter(vf: String): Boolean {
        val want = if (vf.isEmpty()) HWDEC else "mediacodec-copy"
        if (want != hwdec) {
            mpv.setPropertyString("hwdec", want)
            hwdec = want
        }
        try {
            mpv.setPropertyString("vf", vf)
        } catch (e: Exception) {
            Log.w(TAG, "vf $vf: $e")
        }
        return vf.isEmpty() || !mpv.getPropertyString("vf").isNullOrEmpty()
    }

    /** Picture shape, as the desktop's PICTURE menu: normal, zoom (fill, no bars), stretch. */
    fun setZoom(mode: String) {
        mpv.setPropertyDouble("panscan", if (mode == "zoom") 1.0 else 0.0)
        mpv.setPropertyBoolean("keepaspect", mode != "stretch")
    }

    data class Track(val type: String, val id: Int, val lang: String, val title: String, val codec: String,
                     val selected: Boolean)

    fun tracks(): List<Track> {
        val n = mpv.getPropertyInt("track-list/count") ?: 0
        return (0 until n).mapNotNull { i ->
            val type = mpv.getPropertyString("track-list/$i/type") ?: return@mapNotNull null
            Track(type, mpv.getPropertyInt("track-list/$i/id") ?: 0,
                mpv.getPropertyString("track-list/$i/lang") ?: "",
                mpv.getPropertyString("track-list/$i/title") ?: "",
                mpv.getPropertyString("track-list/$i/codec") ?: "",
                mpv.getPropertyBoolean("track-list/$i/selected") ?: false)
        }
    }

    fun setTrack(type: String, id: Int?) =
        mpv.setPropertyString(if (type == "audio") "aid" else "sid", id?.toString() ?: "no")

    private var destroyed = false

    fun destroy() {
        destroyed = true
        mpv.removeObserver(this)
        mpv.destroy()
    }

    // ---------------------------------------------------------------- events (mpv's thread)
    override fun event(eventId: Int) {
        when (eventId) {
            MPVLib.MpvEvent.MPV_EVENT_PLAYBACK_RESTART -> main.post { listener.onPlaybackRestart() }
            // this libmpv doesn't say why a file ended; a channel change starts the next file at once, so
            // a player still idle a moment later means the file couldn't be played (PC gone, bad file)
            MPVLib.MpvEvent.MPV_EVENT_END_FILE -> main.postDelayed({
                if (!destroyed && !stopped && mpv.getPropertyBoolean("idle-active") == true) listener.onEndFile(END_ERROR, 0)
            }, 1500)
        }
    }

    override fun eventProperty(property: String) {}
    override fun eventProperty(property: String, value: Long) {}
    override fun eventProperty(property: String, value: Double) {}
    override fun eventProperty(property: String, value: Boolean) {}
    override fun eventProperty(property: String, value: String) {}

    companion object {
        private const val TAG = "CableBox"
        const val HWDEC = "mediacodec,mediacodec-copy"
        const val END_ERROR = 4                  // as MPV_END_FILE_REASON_ERROR
        private fun fmt(v: Double) = String.format(Locale.US, "%.3f", v)
    }
}
