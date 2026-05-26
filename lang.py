"""
Bilingual string system — English & Hindi.

Usage:
    from lang import t, set_lang, get_lang, to_canonical

    t(user_id, "btn_help")          → "📖 Help"  or  "📖 मदद"
    to_canonical("📖 मदद")          → "📖 Help"   (always English)
    set_lang(user_id, "hi")
    get_lang(user_id)               → "hi"
"""
from typing import Dict

# ── Per-user language preference ──────────────────────────────────────────────
user_lang: Dict[int, str] = {}


def get_lang(uid) -> str:
    return user_lang.get(uid, "en")


def set_lang(uid, lang: str):
    user_lang[uid] = lang


# ── All bilingual strings ──────────────────────────────────────────────────────
STRINGS: Dict[str, Dict[str, str]] = {
    # ── Main keyboard ──────────────────────────────────────────────────────────
    "btn_record":       {"en": "🎥 Record",          "hi": "🎥 रिकॉर्ड"},
    "btn_download":     {"en": "📥 Download",         "hi": "📥 डाउनलोड"},
    "btn_ott":          {"en": "🌐 OTT Download",     "hi": "🌐 OTT डाउनलोड"},
    "btn_status":       {"en": "📊 Status",           "hi": "📊 स्टेटस"},
    "btn_compress":     {"en": "🗜 Compress",          "hi": "🗜 कंप्रेस"},
    "btn_screenshot":   {"en": "📸 Screenshot",       "hi": "📸 स्क्रीनशॉट"},
    "btn_cookies":      {"en": "🍪 Cookies",          "hi": "🍪 कुकीज़"},
    "btn_help":         {"en": "📖 Help",              "hi": "📖 मदद"},

    # ── Audio step ─────────────────────────────────────────────────────────────
    "btn_select_all":   {"en": "🔁 Select All Tracks",  "hi": "🔁 सभी ट्रैक चुनें"},
    "btn_back":         {"en": "◀️ Back",               "hi": "◀️ वापस"},
    "btn_next_wm":      {"en": "✅ Next: Watermark",    "hi": "✅ आगे: वॉटरमार्क"},
    "btn_cancel_setup": {"en": "❌ Cancel Setup",        "hi": "❌ सेटअप रद्द"},

    # ── Watermark step ─────────────────────────────────────────────────────────
    "btn_wm_off":       {"en": "🚫 Watermark OFF",           "hi": "🚫 वॉटरमार्क बंद"},
    "btn_wm_text":      {"en": "✏️ Change Watermark Text",   "hi": "✏️ वॉटरमार्क टेक्स्ट बदलें"},
    "btn_auto_mode":    {"en": "⏱️ Auto: First+Last 1min",   "hi": "⏱️ ऑटो: पहले+आखिरी 1min"},
    "btn_next_size":    {"en": "📐 Next: Video Size →",      "hi": "📐 आगे: वीडियो साइज →"},
    "btn_start_dl":     {"en": "📥 START DOWNLOAD",          "hi": "📥 डाउनलोड शुरू"},
    "btn_cancel":       {"en": "❌ Cancel",                   "hi": "❌ रद्द करें"},

    # Watermark positions
    "wm_top_left":      {"en": "↖ Top-Left",     "hi": "↖ ऊपर-बाएं"},
    "wm_top_right":     {"en": "↗ Top-Right",    "hi": "↗ ऊपर-दाएं"},
    "wm_center":        {"en": "⊙ Center",        "hi": "⊙ बीच में"},
    "wm_bottom_left":   {"en": "↙ Bottom-Left",  "hi": "↙ नीचे-बाएं"},
    "wm_bottom_right":  {"en": "↘ Bottom-Right", "hi": "↘ नीचे-दाएं"},

    # ── Video size step ────────────────────────────────────────────────────────
    "btn_back_wm":      {"en": "◀️ Back to Watermark",  "hi": "◀️ वॉटरमार्क पर वापस"},
    "btn_start_rec":    {"en": "▶️ Start Recording",    "hi": "▶️ रिकॉर्डिंग शुरू"},

    # ── Cancel menu ────────────────────────────────────────────────────────────
    "btn_cancel_all":   {"en": "❌ Cancel ALL",    "hi": "❌ सब रद्द करें"},
    "btn_close_menu":   {"en": "◀️ Close Menu",    "hi": "◀️ मेनू बंद"},

    # ── Compress ───────────────────────────────────────────────────────────────
    "btn_cmp_high":     {"en": "🔵 High Quality",   "hi": "🔵 उच्च गुणवत्ता"},
    "btn_cmp_med":      {"en": "🟡 Medium Quality", "hi": "🟡 मध्यम गुणवत्ता"},
    "btn_cmp_low":      {"en": "🔴 Low (Smallest)", "hi": "🔴 कम (सबसे छोटा)"},
    "btn_cmp_cancel":   {"en": "❌ Cancel Compress", "hi": "❌ कंप्रेस रद्द"},

    # ── OTT ────────────────────────────────────────────────────────────────────
    "btn_ott_cancel":   {"en": "❌ Cancel OTT",          "hi": "❌ OTT रद्द"},
    "btn_back_res":     {"en": "◀️ Back to Resolution",  "hi": "◀️ रिज़ॉल्यूशन पर वापस"},

    # ── Messages ───────────────────────────────────────────────────────────────
    "msg_setup_cancelled": {
        "en": "❌ Setup cancelled.",
        "hi": "❌ सेटअप रद्द कर दिया गया।",
    },
    "msg_cancel_cancelled": {
        "en": "❌ Cancelled.",
        "hi": "❌ रद्द कर दिया गया।",
    },
    "hint_record": {
        "en": "📌 Usage:\n`/rec http://link 00:00:00 Filename`",
        "hi": "📌 तरीका:\n`/rec http://link 00:00:00 Filename`",
    },
    "hint_download": {
        "en": "📌 Usage:\n`/download http://link Filename`",
        "hi": "📌 तरीका:\n`/download http://link Filename`",
    },
    "hint_ott": {
        "en": "📌 Usage:\n`/ott_download https://youtube.com/... Filename`",
        "hi": "📌 तरीका:\n`/ott_download https://youtube.com/... Filename`",
    },
    "hint_compress": {
        "en": "📌 Reply to a video and send `/compress`",
        "hi": "📌 किसी वीडियो को reply करके `/compress` भेजें",
    },
    "hint_screenshot": {
        "en": "📌 Reply to a video and send `/screenshot [1-30]`",
        "hi": "📌 किसी वीडियो को reply करके `/screenshot [1-30]` भेजें",
    },
    "hint_cookies": {
        "en": "📌 Use `/cookies_add` to upload, `/cookies_status` to check, `/del_cookies` to remove",
        "hi": "📌 `/cookies_add` से upload करें, `/cookies_status` से check करें, `/del_cookies` से हटाएं",
    },
    "msg_no_active": {
        "en": "❌ **No active recording to cancel!**",
        "hi": "❌ **कोई active recording नहीं है रद्द करने के लिए!**",
    },
    "msg_all_cancelled": {
        "en": "✅ **All recordings cancelled.**",
        "hi": "✅ **सभी रिकॉर्डिंग रद्द कर दी गई।**",
    },
    "msg_menu_closed": {
        "en": "↩️ Menu closed.",
        "hi": "↩️ मेनू बंद कर दिया।",
    },
    "msg_wm_text_prompt": {
        "en": "✏️ **Type the new watermark text and send it:**",
        "hi": "✏️ **नया वॉटरमार्क टेक्स्ट टाइप करके भेजें:**",
    },
    "msg_lang_set_en": {
        "en": "🇬🇧 **Language changed to English!**\n\nAll buttons and messages are now in English.",
        "hi": "🇬🇧 **भाषा अंग्रेज़ी में बदल दी गई!**\n\nसभी बटन और संदेश अब अंग्रेज़ी में हैं।",
    },
    "msg_lang_set_hi": {
        "en": "🇮🇳 **Language changed to Hindi!**\n\nसभी बटन और संदेश अब हिंदी में हैं।",
        "hi": "🇮🇳 **भाषा हिंदी में बदल दी गई!**\n\nसभी बटन और संदेश अब हिंदी में हैं।",
    },
    "msg_lang_choose": {
        "en": "🌐 **Choose Language / भाषा चुनें:**",
        "hi": "🌐 **भाषा चुनें / Choose Language:**",
    },
}


def t(uid, key: str) -> str:
    lang    = get_lang(uid)
    entry   = STRINGS.get(key, {})
    return entry.get(lang) or entry.get("en") or key


# ── Reverse lookup: any text → canonical English equivalent ───────────────────
# Built once at import time so lookups are O(1)
_CANONICAL: Dict[str, str] = {}

for _key, _langs in STRINGS.items():
    _en_val = _langs.get("en", "")
    for _lang_val in _langs.values():
        if _lang_val:
            _CANONICAL[_lang_val] = _en_val   # HI → EN  (EN → EN also stored)


def to_canonical(text: str) -> str:
    """
    Convert any button text (in any language) to its canonical English version.
    If not found, returns the text unchanged.
    """
    # Try exact match first
    if text in _CANONICAL:
        return _CANONICAL[text]
    # Try stripped of leading checkmark/emoji state prefix (e.g. "✅ ↖ ऊपर-बाएं")
    stripped = text.lstrip("✅ ")
    return _CANONICAL.get(stripped, text)


# ── WM position label → key (bilingual) ───────────────────────────────────────
WM_LABEL_BILINGUAL: Dict[str, str] = {}
_WM_KEYS = {
    "wm_top_left":     "top_left",
    "wm_top_right":    "top_right",
    "wm_center":       "center",
    "wm_bottom_left":  "bottom_left",
    "wm_bottom_right": "bottom_right",
}
for _str_key, _pos_key in _WM_KEYS.items():
    for _lv in STRINGS[_str_key].values():
        WM_LABEL_BILINGUAL[_lv] = _pos_key
