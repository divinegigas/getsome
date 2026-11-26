package app.neoncable42.box

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlin.math.PI
import kotlin.math.sin
import kotlin.random.Random

/** Sounds made in code, as on the desktop: the static hiss between channels and the 1 kHz tone
 *  under the colour bars when a channel is off the air. */
class Tones {
    private val rate = 22050
    private val hiss = loop(volume = 0.30f) { _, r -> (r.nextFloat() * 2f - 1f) * 0.55f }
    private val tone = loop(volume = 0.18f) { i, _ -> sin(2.0 * PI * 1000.0 * i / rate).toFloat() * 0.6f }
    private var hissOn = false
    private var toneOn = false

    private fun loop(volume: Float, sample: (Int, Random) -> Float): AudioTrack {
        val frames = rate                              // one second, looped
        val pcm = ShortArray(frames)
        val r = Random(42)
        for (i in 0 until frames) pcm[i] = (sample(i, r).coerceIn(-1f, 1f) * Short.MAX_VALUE).toInt().toShort()
        val t = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
            .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(rate)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(frames * 2)
            .build()
        t.write(pcm, 0, frames)
        t.setLoopPoints(0, frames, -1)
        t.setVolume(volume)
        return t
    }

    private fun set(t: AudioTrack, on: Boolean, was: Boolean): Boolean {
        if (on == was) return on
        try {
            if (on) {
                t.reloadStaticData()
                t.setLoopPoints(0, rate, -1)
                t.play()
            } else {
                t.pause()
                t.flush()
                t.stop()
            }
        } catch (e: IllegalStateException) {
            return false
        }
        return on
    }

    fun hiss(on: Boolean) {
        hissOn = set(hiss, on, hissOn)
    }

    fun tone(on: Boolean) {
        toneOn = set(tone, on, toneOn)
    }

    fun release() {
        hiss(false)
        tone(false)
        hiss.release()
        tone.release()
    }
}
