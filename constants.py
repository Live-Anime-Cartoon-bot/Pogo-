from typing import Dict, Optional

MAX_CONCURRENT = 3

PROGRESS_FILLED = '<emoji id="5915540975987462465">▰</emoji>'
PROGRESS_EMPTY  = '<emoji id="6217587660634989068">▱</emoji>'

LANG_MAP: Dict[str, str] = {
    "hin": "HIN", "hi": "HIN",
    "kan": "KAN", "kn": "KAN",
    "tel": "TEL", "te": "TEL",
    "tam": "TAM", "ta": "TAM",
    "mal": "MAL", "ml": "MAL",
    "ben": "BEN", "bn": "BEN",
    "mar": "MAR", "mr": "MAR",
    "eng": "ENG", "en": "ENG",
    "pun": "PUN", "pa": "PUN",
    "guj": "GUJ", "gu": "GUJ",
    "ori": "ORI", "or": "ORI",
    "urd": "URD", "ur": "URD",
}

LANG_FULL: Dict[str, str] = {
    "hin": "Hindi",     "hi":  "Hindi",
    "kan": "Kannada",   "kn":  "Kannada",
    "tel": "Telugu",    "te":  "Telugu",
    "tam": "Tamil",     "ta":  "Tamil",
    "mal": "Malayalam", "ml":  "Malayalam",
    "ben": "Bengali",   "bn":  "Bengali",
    "mar": "Marathi",   "mr":  "Marathi",
    "eng": "English",   "en":  "English",
    "pun": "Punjabi",   "pa":  "Punjabi",
    "guj": "Gujarati",  "gu":  "Gujarati",
    "ori": "Odia",      "or":  "Odia",
    "urd": "Urdu",      "ur":  "Urdu",
}

WM_POSITIONS: Dict[str, tuple] = {
    "top_left":     ("10", "10"),
    "top_right":    ("w-tw-10", "10"),
    "center":       ("(w-tw)/2", "(h-th)/2"),
    "bottom_left":  ("10", "h-th-10"),
    "bottom_right": ("w-tw-10", "h-th-10"),
}

WM_LABEL: Dict[str, str] = {
    "top_left":     "↖ Top-Left",
    "top_right":    "↗ Top-Right",
    "center":       "⊙ Center",
    "bottom_left":  "↙ Bottom-Left",
    "bottom_right": "↘ Bottom-Right",
}

WM_LABEL_TO_KEY: Dict[str, str] = {v: k for k, v in WM_LABEL.items()}

VIDEO_SIZES: Dict[str, dict] = {
    "size1": {
        "label": "📺 Size 1 — 720×396",
        "desc":  "16:9 Widescreen",
        "vf":    "scale=720:396:force_original_aspect_ratio=decrease,pad=720:396:(ow-iw)/2:(oh-ih)/2",
    },
    "size2": {
        "label": "📺 Size 2 — 720×540",
        "desc":  "4:3 Black bars",
        "vf":    "scale=720:540:force_original_aspect_ratio=decrease,pad=720:540:(ow-iw)/2:(oh-ih)/2",
    },
    "size3": {
        "label": "📺 Size 3 — 720×405",
        "desc":  "16:9 Border all sides",
        "vf":    "scale=700:394:force_original_aspect_ratio=decrease,pad=720:405:10:5",
    },
    "bars_169": {
        "label": "◼ 16:9 Bars — 720×576",
        "desc":  "Letterbox",
        "vf":    "scale=720:576:force_original_aspect_ratio=decrease,pad=720:576:(ow-iw)/2:(oh-ih)/2",
    },
    "bars_43": {
        "label": "◼ 4:3 Bars — 720×540",
        "desc":  "Pillarbox",
        "vf":    "scale=-2:540:force_original_aspect_ratio=decrease,pad=720:540:(ow-iw)/2:(oh-ih)/2",
    },
    "480p": {
        "label": "📺 480p — 854×480",
        "desc":  "Standard 480p (channel default)",
        "vf":    "scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2:black",
    },
    "original": {
        "label": "🔓 Original Size",
        "desc":  "No scaling",
        "vf":    None,
    },
}

SIZE_LABEL_TO_KEY: Dict[str, str] = {v["label"]: k for k, v in VIDEO_SIZES.items()}

SLOT_EMOJI = ["1️⃣", "2️⃣", "3️⃣"]

COMPRESS_PRESETS: Dict[str, tuple] = {
    "🔵 High Quality":   ("-c:v libx264 -crf 23 -preset fast -c:a aac -b:a 128k", "High (good quality, moderate size)"),
    "🟡 Medium Quality": ("-c:v libx264 -crf 28 -preset fast -c:a aac -b:a 96k",  "Medium (balanced)"),
    "🔴 Low (Smallest)": ("-c:v libx264 -crf 32 -preset fast -c:a aac -b:a 64k",  "Low (small size, lower quality)"),
}

OTT_RES_LABEL_TO_FMT: Dict[str, str] = {}

OTT_AUDIO_LANGS: Dict[str, Optional[str]] = {"🌐 Multi": None}

_HEIGHT_LABEL: Dict[int, str] = {
    144: "📺 140p",  240: "📺 240p",  360: "📺 360p",
    480: "📺 480p",  576: "📺 576p",  640: "📺 640p",
    720: "📺 720p",  1080: "🔵 1080p", 1440: "🔶 2K",
    2160: "🔶 4K",
}

_HEIGHT_FMT: Dict[int, str] = {
    h: f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
    for h in [144, 240, 360, 480, 576, 640, 720, 1080, 1440, 2160]
}

_LANG_CODE_TO_LABEL: Dict[str, str] = {
    "hin": "🇮🇳 Hindi",    "tam": "🎬 Tamil",
    "tel": "🎭 Telugu",    "mal": "🌴 Malayalam",
    "kan": "🌸 Kannada",   "mar": "🎪 Marathi",
    "ben": "🇧🇩 Bengali",  "pun": "🎵 Punjabi",
    "eng": "🇬🇧 English",  "urd": "🕌 Urdu",
    "guj": "🎶 Gujarati",  "ori": "🌸 Odia",
}

MAX_HISTORY = 500
