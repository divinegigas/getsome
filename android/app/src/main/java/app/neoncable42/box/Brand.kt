package app.neoncable42.box

import android.content.Context
import android.graphics.Typeface

/** The look of each edition, as on the desktop (getsome/brand.py): Neon Cable 42's synthwave neon, or
 *  XCable's black, gold and oxblood. Fonts are the same bundled SIL OFL files (assets/fonts). */
object Brand {
    val XCABLE = BuildConfig.EDITION == "xcable"
    val APP_NAME = if (XCABLE) "XCable" else "Neon Cable 42"

    // the PC's sharing service: editions never mix (getsome/headend.py PORT, DISCOVERY_PORT)
    val HTTP_PORT = if (XCABLE) 4252 else 4242
    val DISCOVERY_PORT = if (XCABLE) 4253 else 4243

    val ACCENT = if (XCABLE) 0xFFE2B451.toInt() else 0xFFFF2BD6.toInt()     // pink / gold
    val SECOND = if (XCABLE) 0xFFF0E2C4.toInt() else 0xFF00F0FF.toInt()     // cyan / champagne
    val HILITE = if (XCABLE) 0xFFFFC94D.toInt() else 0xFFFFE600.toInt()     // yellow / amber
    val DIM = if (XCABLE) 0xFF9A8470.toInt() else 0xFF968CD2.toInt()        // quiet labels
    val PANEL_TOP = if (XCABLE) 0xF2190A07.toInt() else 0xF21A063A.toInt()
    val PANEL_BOTTOM = if (XCABLE) 0xF00A0403.toInt() else 0xF0080218.toInt()
    val SELECT = if (XCABLE) 0x55E2B451 else 0x3CFF2BD6

    lateinit var numbers: Typeface
    lateinit var text: Typeface

    fun load(ctx: Context) {
        if (::numbers.isInitialized) return
        numbers = font(ctx, if (XCABLE) "Limelight-Regular.ttf" else "Monoton-Regular.ttf", Typeface.DEFAULT_BOLD)
        text = font(ctx, if (XCABLE) "PlayfairDisplay.ttf" else "TiltNeon.ttf", Typeface.DEFAULT)
    }

    private fun font(ctx: Context, name: String, fallback: Typeface): Typeface =
        try {
            Typeface.createFromAsset(ctx.assets, "fonts/$name")
        } catch (e: RuntimeException) {
            fallback
        }
}
