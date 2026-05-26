from typing import List, Optional
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton

import config
from constants import VIDEO_SIZES, SLOT_EMOJI
from lang import t, STRINGS, WM_LABEL_BILINGUAL
from utils import slot_number
from state import user_status


def build_main_keyboard(uid=None) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(t(uid, "btn_record")),     KeyboardButton(t(uid, "btn_download"))],
            [KeyboardButton(t(uid, "btn_ott")),        KeyboardButton(t(uid, "btn_status"))],
            [KeyboardButton(t(uid, "btn_compress")),   KeyboardButton(t(uid, "btn_screenshot"))],
            [KeyboardButton(t(uid, "btn_cookies")),    KeyboardButton(t(uid, "btn_help"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def build_audio_keyboard(tracks: List[dict], selected: set, uid=None) -> ReplyKeyboardMarkup:
    rows = []
    for i in range(0, len(tracks), 2):
        row = []
        for track in tracks[i: i + 2]:
            check = "✅" if track["index"] in selected else "❌"
            row.append(KeyboardButton(f"{check} {track['label']}"))
        rows.append(row)
    rows.append([KeyboardButton(t(uid, "btn_select_all"))])
    rows.append([KeyboardButton(t(uid, "btn_back")), KeyboardButton(t(uid, "btn_next_wm"))])
    rows.append([KeyboardButton(t(uid, "btn_cancel_setup"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _wm_label(key: str, uid=None) -> str:
    str_key_map = {
        "top_left":     "wm_top_left",
        "top_right":    "wm_top_right",
        "center":       "wm_center",
        "bottom_left":  "wm_bottom_left",
        "bottom_right": "wm_bottom_right",
    }
    return t(uid, str_key_map[key])


def build_watermark_keyboard(setup: dict, uid=None) -> ReplyKeyboardMarkup:
    pos  = setup.get("watermark_pos")
    auto = setup.get("auto_mode", False)
    mode = setup.get("mode", "record")

    def lbl(key):
        base = _wm_label(key, uid)
        return ("✅ " if pos == key else "") + base

    wm_off_text  = ("✅ " if pos is None else "") + t(uid, "btn_wm_off")
    auto_text    = ("✅ " if auto else "") + t(uid, "btn_auto_mode")

    rows = [
        [KeyboardButton(lbl("top_left")),    KeyboardButton(lbl("top_right"))],
        [KeyboardButton(lbl("center"))],
        [KeyboardButton(lbl("bottom_left")), KeyboardButton(lbl("bottom_right"))],
        [KeyboardButton(wm_off_text)],
        [KeyboardButton(t(uid, "btn_wm_text"))],
    ]
    if mode == "record":
        rows.append([KeyboardButton(auto_text)])
    if mode == "download":
        rows.append([KeyboardButton(t(uid, "btn_start_dl"))])
    else:
        rows.append([KeyboardButton(t(uid, "btn_next_size"))])
    rows.append([KeyboardButton(t(uid, "btn_cancel"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def build_size_keyboard(selected: str = "original", uid=None) -> ReplyKeyboardMarkup:
    rows = []
    for key, val in VIDEO_SIZES.items():
        check = "✅ " if selected == key else ""
        rows.append([KeyboardButton(f"{check}{val['label']}")])
    rows.append([KeyboardButton(t(uid, "btn_back_wm"))])
    rows.append([KeyboardButton(t(uid, "btn_start_rec")), KeyboardButton(t(uid, "btn_cancel"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def build_cancel_keyboard(user_id: int, uid=None) -> ReplyKeyboardMarkup:
    uid  = uid or user_id
    jobs = user_status.get(user_id, {})
    rows = []
    for job_id, info in sorted(jobs.items()):
        n     = slot_number(job_id)
        emoji = SLOT_EMOJI[n - 1]
        rows.append([KeyboardButton(f"{emoji} Cancel Slot {n}: {info['filename']}")])
    rows.append([KeyboardButton(t(uid, "btn_cancel_all"))])
    rows.append([KeyboardButton(t(uid, "btn_close_menu"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def build_compress_keyboard(uid=None) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(t(uid, "btn_cmp_high"))],
            [KeyboardButton(t(uid, "btn_cmp_med"))],
            [KeyboardButton(t(uid, "btn_cmp_low"))],
            [KeyboardButton(t(uid, "btn_cmp_cancel"))],
        ],
        resize_keyboard=True,
    )


def build_ott_resolution_keyboard_dynamic(res_map: dict, selected: str = "", uid=None) -> ReplyKeyboardMarkup:
    labels = list(res_map.keys())
    rows = []
    for i in range(0, len(labels), 3):
        row = []
        for lbl in labels[i: i + 3]:
            check = "✅ " if selected == lbl else ""
            row.append(KeyboardButton(f"{check}{lbl}"))
        rows.append(row)
    rows.append([KeyboardButton(t(uid, "btn_ott_cancel"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def build_ott_audio_keyboard_dynamic(audio_map: dict, selected: str = "", uid=None) -> ReplyKeyboardMarkup:
    rows = []
    for lbl in audio_map:
        check = "✅ " if selected == lbl else ""
        rows.append([KeyboardButton(f"{check}{lbl}")])
    rows.append([KeyboardButton(t(uid, "btn_back_res"))])
    rows.append([KeyboardButton(t(uid, "btn_ott_cancel"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def setup_summary_text(setup: dict) -> str:
    tracks   = setup.get("tracks", [])
    selected = setup.get("selected_tracks", set())
    sel_labels = [tr["label"] for tr in tracks if tr["index"] in selected] or ["All"]
    pos      = setup.get("watermark_pos")
    wm_text  = setup.get("watermark_text", config.DEFAULT_FILENAME)
    auto     = setup.get("auto_mode", False)
    mode     = setup.get("mode", "record")

    # Use English WM label for summary (no uid needed here)
    _WM_EN = {
        "top_left": "↖ Top-Left", "top_right": "↗ Top-Right",
        "center": "⊙ Center", "bottom_left": "↙ Bottom-Left",
        "bottom_right": "↘ Bottom-Right",
    }
    wm_desc    = "OFF" if pos is None else f"{_WM_EN.get(pos, pos)} → `{wm_text}`"
    size_key   = setup.get("video_size", "original")
    size_lbl   = VIDEO_SIZES.get(size_key, VIDEO_SIZES["original"])["label"]

    if mode == "download":
        header        = "📥 **Download Setup**"
        duration_line = ""
    else:
        header        = "🎛️ **Recording Setup**"
        duration_line = f"⏱ **Duration:** `{setup.get('timestamp', '—')}`\n"
        duration_line += f"⏩ **Auto Mode:** `{'✅ First+Last 1min' if auto else '❌ Off'}`\n"

    return (
        f"{header}\n\n"
        f"🔗 **URL:** `{setup['url'][:60]}...`\n"
        f"{duration_line}"
        f"📁 **Filename:** `{setup['filename']}`\n"
        f"🎵 **Audio:** `{', '.join(sel_labels)}`\n"
        f"🖼 **Watermark:** `{wm_desc}`\n"
        f"📐 **Size:** `{size_lbl}`\n\n"
        f"👇 Choose an option:"
    )
