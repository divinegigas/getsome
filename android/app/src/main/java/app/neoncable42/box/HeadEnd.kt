package app.neoncable42.box

import android.content.Context
import android.net.Uri
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.net.URL
import java.time.Duration
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter

/** Talks to the PC that shares the channels (getsome/headend.py), exactly like a desktop cable box:
 *  UDP discovery, pairing with the 6-digit code from the PC's Settings, schedule questions through
 *  /api/call, and files streamed from /f/<token>/<path> with seeking. Home network only. */
class HeadEnd(val host: String, val port: Int, private val token: String) {

    class NotPaired : IOException("not paired")

    fun call(fn: String, vararg args: Any?): Any? {
        val body = JSONObject().put("fn", fn).put("args", JSONArray(args.map { it ?: JSONObject.NULL }))
        val c = URL("http://$host:$port/api/call").openConnection() as HttpURLConnection
        try {
            c.requestMethod = "POST"
            c.connectTimeout = 5000
            c.readTimeout = 20000
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.setRequestProperty("X-NC42-Token", token)
            c.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = c.responseCode
            val txt = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
            if (code == 403) throw NotPaired()
            if (code >= 400) throw IOException(runCatching { JSONObject(txt).optString("error") }.getOrNull() ?: "HTTP $code")
            val r = JSONObject(txt)
            return if (r.isNull("result")) null else r.get("result")
        } finally {
            c.disconnect()
        }
    }

    fun obj(fn: String, vararg args: Any?): JSONObject? = call(fn, *args) as? JSONObject
    fun arr(fn: String, vararg args: Any?): JSONArray? = call(fn, *args) as? JSONArray

    /** The URL mpv streams a file from (the PC only serves files that belong to its channels). */
    fun fileUrl(path: String): String = "http://$host:$port/f/$token/" + Uri.encode(path)

    data class Found(val host: String, val port: Int, val name: String, val app: String, val pairing: Boolean)

    companion object {
        private val ISO: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSSSS")

        /** Times go to the PC as its own local time, like a desktop box (both clocks are the house's). */
        fun iso(t: LocalDateTime): String = t.format(ISO)

        fun parse(s: String): LocalDateTime = try {
            LocalDateTime.parse(s)
        } catch (e: RuntimeException) {
            OffsetDateTime.parse(s).toLocalDateTime()
        }

        fun secondsBetween(a: LocalDateTime, b: LocalDateTime): Double = Duration.between(a, b).toMillis() / 1000.0

        fun hello(host: String, port: Int): Found {
            val c = URL("http://$host:$port/api/hello").openConnection() as HttpURLConnection
            c.connectTimeout = 4000
            c.readTimeout = 6000
            try {
                val j = JSONObject(c.inputStream.bufferedReader().use { it.readText() })
                return Found(host, j.optInt("port", port), j.optString("name", host), j.optString("app"),
                    j.optBoolean("pairing"))
            } finally {
                c.disconnect()
            }
        }

        /** Returns the token, or throws IOException with the PC's own message ("That code is wrong…"). */
        fun deviceName(): String = "${Build.MANUFACTURER} ${Build.MODEL}".trim().ifEmpty { "Android" }

        fun pair(host: String, port: Int, code: String, box: String = deviceName()): String {
            val c = URL("http://$host:$port/api/pair").openConnection() as HttpURLConnection
            try {
                c.requestMethod = "POST"
                c.connectTimeout = 5000
                c.readTimeout = 10000
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(JSONObject().put("code", code).put("box", box).toString().toByteArray()) }
                val ok = c.responseCode < 400
                val txt = (if (ok) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: "{}"
                val j = JSONObject(txt)
                if (!ok) throw IOException(j.optString("error", "The PC said no."))
                return j.getString("token")
            } finally {
                c.disconnect()
            }
        }

        /** Ask every PC on the home network "who shares channels?" (b"NC42?" on the discovery port). */
        fun discover(timeoutMs: Int = 2500): List<Found> {
            val out = LinkedHashMap<String, Found>()
            DatagramSocket().use { s ->
                s.broadcast = true
                s.soTimeout = 400
                val ask = "NC42?".toByteArray()
                repeat(2) {
                    try {
                        s.send(DatagramPacket(ask, ask.size, InetAddress.getByName("255.255.255.255"), Brand.DISCOVERY_PORT))
                    } catch (e: IOException) {
                        // no Wi-Fi: nothing to find
                    }
                }
                val buf = ByteArray(4096)
                val until = System.currentTimeMillis() + timeoutMs
                while (System.currentTimeMillis() < until) {
                    val p = DatagramPacket(buf, buf.size)
                    try {
                        s.receive(p)
                    } catch (e: SocketTimeoutException) {
                        continue
                    } catch (e: IOException) {
                        break
                    }
                    try {
                        val j = JSONObject(String(p.data, 0, p.length))
                        val host = p.address.hostAddress ?: continue
                        out[host] = Found(host, j.optInt("port", Brand.HTTP_PORT), j.optString("name", host),
                            j.optString("app"), j.optBoolean("pairing"))
                    } catch (e: Exception) {
                        // not one of ours
                    }
                }
            }
            return out.values.toList()
        }
    }
}

/** What the phone remembers: which PC, its key, and a few viewing choices. */
class Prefs(ctx: Context) {
    private val p = ctx.getSharedPreferences("cablebox", Context.MODE_PRIVATE)

    val paired: Boolean get() = token.isNotEmpty() && host.isNotEmpty()
    var host: String
        get() = p.getString("host", "") ?: ""
        set(v) = p.edit().putString("host", v).apply()
    var port: Int
        get() = p.getInt("port", Brand.HTTP_PORT)
        set(v) = p.edit().putInt("port", v).apply()
    var token: String
        get() = p.getString("token", "") ?: ""
        set(v) = p.edit().putString("token", v).apply()
    var pcName: String
        get() = p.getString("pc_name", "") ?: ""
        set(v) = p.edit().putString("pc_name", v).apply()
    var lastChannel: Int
        get() = p.getInt("last_channel", -1)
        set(v) = p.edit().putInt("last_channel", v).apply()
    /** Commercials play at this percent of the volume, as on the desktop (Settings: ad_volume). */
    var adVolume: Int
        get() = p.getInt("ad_volume", 50)
        set(v) = p.edit().putInt("ad_volume", v).apply()
    var zoom: String
        get() = p.getString("zoom", "normal") ?: "normal"
        set(v) = p.edit().putString("zoom", v).apply()
    var audioLang: String
        get() = p.getString("audio_lang", "") ?: ""
        set(v) = p.edit().putString("audio_lang", v).apply()
    var subLang: String
        get() = p.getString("sub_lang", "") ?: ""
        set(v) = p.edit().putString("sub_lang", v).apply()

    fun forget() {
        p.edit().remove("token").apply()
    }

    fun headEnd(): HeadEnd = HeadEnd(host, port, token)
}
