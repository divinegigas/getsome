package app.neoncable42.box

import android.app.Activity
import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors

/** First run (or "pair again"): find the PC that shares the channels and pair with the 6-digit code
 *  it shows in Settings → Home network → "Pair a cable box…". The same steps as a desktop cable box. */
class PairActivity : Activity() {

    private lateinit var prefs: Prefs
    private lateinit var found: LinearLayout
    private lateinit var addr: EditText
    private lateinit var code: EditText
    private lateinit var status: TextView
    private val ui = Handler(Looper.getMainLooper())
    private val bg = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        Brand.load(this)
        val pad = dp(24)
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        val scroll = ScrollView(this).apply {
            setBackgroundColor(0xFF07020F.toInt().let { if (Brand.XCABLE) 0xFF0B0504.toInt() else it })
            addView(col)
        }
        setContentView(scroll)

        col.addView(TextView(this).apply {
            text = Brand.APP_NAME
            typeface = Brand.numbers
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 40f)
            setTextColor(Brand.ACCENT)
            setShadowLayer(18f, 0f, 0f, Brand.ACCENT)
        })
        col.addView(label("CABLE BOX", Brand.SECOND, 15f).apply { letterSpacing = 0.3f })
        intent.getStringExtra(NOTE)?.let { col.addView(label(it, Brand.HILITE, 16f).apply { setPadding(0, dp(16), 0, 0) }) }
        col.addView(label("Watch the channels your ${Brand.APP_NAME} PC shares on your home network.\n\n" +
            "1. On the PC: Settings → Home network → tick “Share my channels…”, then press “Pair a cable box…”.\n" +
            "2. Pick the PC below (or type its address), enter the 6-digit code it shows, and press Pair.",
            0xFFE6E0F5.toInt(), 16f).apply { setPadding(0, dp(16), 0, dp(8)) })

        col.addView(label("PCs SHARING CHANNELS", Brand.DIM, 13f).apply { setPadding(0, dp(12), 0, dp(4)) })
        found = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(found)
        col.addView(button("Search again", false) { search() })

        col.addView(label("ADDRESS", Brand.DIM, 13f).apply { setPadding(0, dp(16), 0, dp(4)) })
        addr = field(prefs.host, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        col.addView(addr)
        col.addView(label("CODE", Brand.DIM, 13f).apply { setPadding(0, dp(12), 0, dp(4)) })
        code = field("", InputType.TYPE_CLASS_NUMBER).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 28f)
            letterSpacing = 0.3f
        }
        col.addView(code)
        col.addView(button("Pair", true) { pair() })
        status = label("", 0xFFFFFFFF.toInt(), 16f).apply { setPadding(0, dp(12), 0, 0) }
        col.addView(status)
        if (intent.getBooleanExtra(FROM_TV, false) && prefs.paired) col.addView(button("Cancel", false) { finish() })
        search()
    }

    override fun onDestroy() {
        bg.shutdownNow()
        super.onDestroy()
    }

    private fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun label(s: String, color: Int, sp: Float) = TextView(this).apply {
        text = s
        typeface = Brand.text
        setTextColor(color)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
    }

    private fun field(value: String, type: Int) = EditText(this).apply {
        setText(value)
        inputType = type
        typeface = Brand.text
        setTextColor(0xFFFFFFFF.toInt())
        setHintTextColor(Brand.DIM)
        background = GradientDrawable().apply {
            setColor(0x22FFFFFF)
            setStroke(dp(1), Brand.SECOND)
            cornerRadius = dp(8).toFloat()
        }
        setPadding(dp(14), dp(12), dp(14), dp(12))
        isSingleLine = true
    }

    private fun button(s: String, primary: Boolean, click: () -> Unit) = Button(this).apply {
        text = s
        isAllCaps = false
        typeface = Brand.text
        setTextColor(0xFFFFFFFF.toInt())
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
        background = GradientDrawable().apply {
            setColor(if (primary) Brand.SELECT else 0x18FFFFFF)
            setStroke(dp(2), if (primary) Brand.ACCENT else Brand.DIM)
            cornerRadius = dp(10).toFloat()
        }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(10) }
        setOnClickListener { click() }
    }

    private fun search() {
        found.removeAllViews()
        found.addView(label("Looking…", Brand.DIM, 15f))
        bg.execute {
            // some phones drop broadcast replies unless asked to keep them
            val wifi = applicationContext.getSystemService(WIFI_SERVICE) as? WifiManager
            val lock = wifi?.createMulticastLock("cablebox")?.apply { setReferenceCounted(false) }
            val list = try {
                lock?.acquire()
                HeadEnd.discover()
            } catch (e: Exception) {
                emptyList()
            } finally {
                runCatching { lock?.release() }
            }
            ui.post { if (!isDestroyed) showFound(list) }
        }
    }

    private fun showFound(list: List<HeadEnd.Found>) {
        found.removeAllViews()
        if (list.isEmpty()) {
            found.addView(label("None found. Check the PC is on and sharing, and that this device is on the same " +
                "Wi-Fi. You can also type the PC's address below.", Brand.DIM, 15f))
            return
        }
        for (f in list) {
            found.addView(button("${f.name}   ·   ${f.app.ifEmpty { Brand.APP_NAME }}" +
                if (f.pairing) "   ·   ready to pair" else "", f.pairing) {
                addr.setText(f.host)
                addr.tag = f
                code.requestFocus()
            })
        }
        if (list.size == 1 && addr.text.isEmpty()) {
            addr.setText(list[0].host)
            addr.tag = list[0]
        }
    }

    private fun pair() {
        val raw = addr.text.toString().trim()
        val digits = code.text.toString().trim()
        if (raw.isEmpty()) return say("Type the PC's address, or pick it above.")
        if (digits.length != 6) return say("The code has 6 digits: it's shown on the PC after pressing “Pair a cable box…”.")
        val host = raw.substringBefore(':')
        val port = raw.substringAfter(':', "").toIntOrNull() ?: (addr.tag as? HeadEnd.Found)?.takeIf { it.host == host }?.port
            ?: Brand.HTTP_PORT
        say("Pairing…")
        bg.execute {
            val r = runCatching {
                val hello = HeadEnd.hello(host, port)
                val token = HeadEnd.pair(host, port, digits)
                hello to token
            }
            ui.post {
                if (isDestroyed) return@post
                r.onSuccess { (hello, token) ->
                    prefs.host = host
                    prefs.port = port
                    prefs.token = token
                    prefs.pcName = hello.name
                    startActivity(Intent(this, TvActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                    finish()
                }.onFailure { e ->
                    say(when (e) {
                        is java.net.ConnectException, is java.net.SocketTimeoutException, is java.net.UnknownHostException ->
                            "Can't reach $host. Is the PC on, with “Share my channels…” ticked, and this device on the same Wi-Fi?"
                        else -> e.message ?: "That didn't work."
                    })
                }
            }
        }
    }

    private fun say(s: String) {
        status.text = s
        status.visibility = View.VISIBLE
        status.gravity = Gravity.START
    }

    companion object {
        const val NOTE = "note"
        const val FROM_TV = "from_tv"
    }
}
