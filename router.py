import asyncio
from pyrogram import filters
from pyrogram.types import Message, ReplyKeyboardRemove

import config
import limit_system
from state import app, allowed, LOG, user_setup, compress_pending, user_tasks
from constants import (
    SIZE_LABEL_TO_KEY, COMPRESS_PRESETS,
    OTT_RES_LABEL_TO_FMT, OTT_AUDIO_LANGS, MAX_CONCURRENT,
)
from lang import t, to_canonical, WM_LABEL_BILINGUAL
from utils import TimeFormatter
from keyboards import (
    build_main_keyboard, build_audio_keyboard,
    build_watermark_keyboard, build_size_keyboard,
    build_cancel_keyboard, build_compress_keyboard,
    build_ott_resolution_keyboard_dynamic, build_ott_audio_keyboard_dynamic,
    setup_summary_text,
)
from handlers.schedule import do_cancel_job


_COMMANDS = [
    "start", "alive", "help", "status", "cancel", "rec", "download",
    "ott_download", "compress", "screenshot",
    "cookies_add", "cookies_status", "del_cookies",
    "schedule", "schedules", "cancel_schedule",
    "verify", "history", "recording_old", "Hindi_or_English",
]

# ── Canonical English key lookup for compress presets (also Hindi) ─────────────
_COMPRESS_CANONICAL: dict = {}
for _ek in list(COMPRESS_PRESETS.keys()):
    _COMPRESS_CANONICAL[_ek] = _ek           # EN → EN
# Hindi equivalents added via to_canonical() at runtime


@app.on_message(filters.text & allowed & ~filters.command(_COMMANDS))
async def text_router(client, message: Message):
    user_id = message.from_user.id
    raw     = message.text.strip()
    canon   = to_canonical(raw)             # always canonical English
    setup   = user_setup.get(user_id, {})
    step    = setup.get("step", "")

    from handlers.commands import help_cmd, status_cmd

    # ── Main menu buttons ──────────────────────────────────────────────────────
    if canon == t(None, "btn_help"):
        return await help_cmd(client, message)

    if canon == t(None, "btn_status"):
        return await status_cmd(client, message)

    hint_map = {
        t(None, "btn_record"):     t(user_id, "hint_record"),
        t(None, "btn_download"):   t(user_id, "hint_download"),
        t(None, "btn_ott"):        t(user_id, "hint_ott"),
        t(None, "btn_compress"):   t(user_id, "hint_compress"),
        t(None, "btn_screenshot"): t(user_id, "hint_screenshot"),
        t(None, "btn_cookies"):    t(user_id, "hint_cookies"),
    }
    if canon in hint_map:
        return await message.reply_text(
            hint_map[canon], reply_markup=build_main_keyboard(user_id)
        )

    # ── Setup steps ────────────────────────────────────────────────────────────
    if step == "audio":
        return await _handle_audio(client, message, raw, canon, setup, user_id)

    if step == "watermark":
        return await _handle_watermark(client, message, raw, canon, setup, user_id)

    if step == "wm_text_input":
        setup["watermark_text"] = raw
        setup["step"] = "watermark"
        return await message.reply_text(
            f"✅ **Watermark text set to:** `{raw}`\n\n" + setup_summary_text(setup),
            reply_markup=build_watermark_keyboard(setup, uid=user_id)
        )

    if step == "size":
        return await _handle_size(client, message, raw, canon, setup, user_id)

    if step == "cancel":
        return await _handle_cancel(client, message, canon, user_id)

    if step == "compress":
        return await _handle_compress(client, message, canon, setup, user_id)

    if step == "ott_resolution":
        return await _handle_ott_resolution(client, message, raw, setup, user_id)

    if step == "ott_audio":
        return await _handle_ott_audio(client, message, raw, canon, setup, user_id)


# ─────────────────────────────────────────────────────────────────────────────
#  Step handlers  (receive canonical English text for button matching)
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_audio(client, message: Message, raw: str, canon: str,
                        setup: dict, user_id: int):
    tracks        = setup.get("tracks", [])
    selected: set = setup.get("selected_tracks", set())

    # Track labels (e.g. "HIN (AAC)") are language-neutral — match against raw
    clean_raw = raw.lstrip("✅❌ ").strip()
    matched   = next((tr for tr in tracks if tr["label"] == clean_raw), None)

    if matched:
        idx = matched["index"]
        selected.discard(idx) if idx in selected else selected.add(idx)
        setup["selected_tracks"] = selected
        sel_count = len(selected)
        return await message.reply_text(
            f"🎵 **Audio Tracks** — {sel_count}/{len(tracks)} selected\n"
            f"Selected: `{', '.join(tr['label'] for tr in tracks if tr['index'] in selected) or 'None'}`",
            reply_markup=build_audio_keyboard(tracks, selected, uid=user_id)
        )

    btn_select_all_en = t(None, "btn_select_all")
    btn_back_en       = t(None, "btn_back")
    btn_next_wm_en    = t(None, "btn_next_wm")
    btn_cancel_en     = t(None, "btn_cancel_setup")

    if canon == btn_select_all_en:
        setup["selected_tracks"] = (
            set() if len(selected) == len(tracks)
            else set(tr["index"] for tr in tracks)
        )
        label = "all deselected" if not setup["selected_tracks"] else "all selected"
        return await message.reply_text(
            f"🔁 Tracks {label}.",
            reply_markup=build_audio_keyboard(tracks, setup["selected_tracks"], uid=user_id)
        )

    if canon == btn_back_en:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            t(user_id, "msg_setup_cancelled"),
            reply_markup=build_main_keyboard(user_id)
        )

    if canon == btn_next_wm_en:
        setup["step"] = "watermark"
        return await message.reply_text(
            setup_summary_text(setup),
            reply_markup=build_watermark_keyboard(setup, uid=user_id)
        )

    if canon == btn_cancel_en:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            t(user_id, "msg_setup_cancelled"),
            reply_markup=build_main_keyboard(user_id)
        )


async def _handle_watermark(client, message: Message, raw: str, canon: str,
                             setup: dict, user_id: int):
    clean_canon = canon.lstrip("✅ ").strip()
    clean_raw   = raw.lstrip("✅ ").strip()

    # WM position — check both EN and HI versions via bilingual dict
    if clean_canon in WM_LABEL_BILINGUAL or clean_raw in WM_LABEL_BILINGUAL:
        pos_key = WM_LABEL_BILINGUAL.get(clean_canon) or WM_LABEL_BILINGUAL.get(clean_raw)
        setup["watermark_pos"] = pos_key
        return await message.reply_text(
            f"✅ **Watermark set!**\n\n" + setup_summary_text(setup),
            reply_markup=build_watermark_keyboard(setup, uid=user_id)
        )

    wm_off_en  = t(None, "btn_wm_off")
    wm_text_en = t(None, "btn_wm_text")
    auto_en    = t(None, "btn_auto_mode")
    next_sz_en = t(None, "btn_next_size")
    start_dl_en= t(None, "btn_start_dl")
    cancel_en  = t(None, "btn_cancel")

    if wm_off_en in canon or "Watermark OFF" in canon or "वॉटरमार्क बंद" in raw:
        setup["watermark_pos"] = None
        return await message.reply_text(
            "🚫 **Watermark disabled.**\n\n" + setup_summary_text(setup),
            reply_markup=build_watermark_keyboard(setup, uid=user_id)
        )

    if canon == wm_text_en:
        setup["step"] = "wm_text_input"
        return await message.reply_text(
            t(user_id, "msg_wm_text_prompt"),
            reply_markup=ReplyKeyboardRemove()
        )

    if "Auto: First+Last" in canon or "ऑटो" in raw:
        setup["auto_mode"] = not setup.get("auto_mode", False)
        s = "✅ ON" if setup["auto_mode"] else "❌ OFF"
        return await message.reply_text(
            f"⏱️ **Auto Mode:** {s}\n\n" + setup_summary_text(setup),
            reply_markup=build_watermark_keyboard(setup, uid=user_id)
        )

    if canon == next_sz_en:
        setup["step"] = "size"
        return await message.reply_text(
            "📐 **Select Video Size:**",
            reply_markup=build_size_keyboard(setup.get("video_size", "original"), uid=user_id)
        )

    if canon == start_dl_en:
        setup["step"] = "running"
        await message.reply_text(
            "📥 **Starting download...**",
            reply_markup=build_main_keyboard(user_id)
        )
        s = user_setup.pop(user_id)
        from handlers.record import handle_record
        asyncio.create_task(handle_record(client, message, s, user_id))
        return

    if canon == cancel_en:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            t(user_id, "msg_setup_cancelled"),
            reply_markup=build_main_keyboard(user_id)
        )


async def _handle_size(client, message: Message, raw: str, canon: str,
                       setup: dict, user_id: int):
    clean = canon.lstrip("✅ ").strip()

    if clean in SIZE_LABEL_TO_KEY:
        setup["video_size"] = SIZE_LABEL_TO_KEY[clean]
        return await message.reply_text(
            f"✅ **Size selected:** {clean}\n\n" + setup_summary_text(setup),
            reply_markup=build_size_keyboard(setup["video_size"], uid=user_id)
        )

    back_wm_en  = t(None, "btn_back_wm")
    start_rec_en= t(None, "btn_start_rec")
    cancel_en   = t(None, "btn_cancel")

    if canon == back_wm_en:
        setup["step"] = "watermark"
        return await message.reply_text(
            setup_summary_text(setup),
            reply_markup=build_watermark_keyboard(setup, uid=user_id)
        )

    if canon == start_rec_en:
        is_unlimited = user_id in config.OWNER_ID or user_id in config.AUTH_USERS
        ok, use_msg  = limit_system.use_rec(user_id, unlimited=is_unlimited)
        if not ok:
            user_setup.pop(user_id, None)
            return await message.reply_text(
                f"❌ **Rec Limit Khatam!**\n\n{use_msg}\n\n"
                "📊 /limit — apni limit dekhen\n"
                "🔐 /verify — aur Rec unlock karein",
                reply_markup=build_main_keyboard(user_id)
            )
        setup["step"] = "running"
        await message.reply_text(
            "🎬 **Starting recording...**",
            reply_markup=build_main_keyboard(user_id)
        )
        s = user_setup.pop(user_id)
        from handlers.record import handle_record
        asyncio.create_task(handle_record(client, message, s, user_id))
        return

    if canon == cancel_en:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            t(user_id, "msg_setup_cancelled"),
            reply_markup=build_main_keyboard(user_id)
        )


async def _handle_cancel(client, message: Message, canon: str, user_id: int):
    cancel_all_en = t(None, "btn_cancel_all")
    close_en      = t(None, "btn_close_menu")

    if canon == cancel_all_en:
        jobs = list(user_tasks.get(user_id, {}).keys())
        for job_id in jobs:
            await do_cancel_job(user_id, job_id, message)
        user_setup.pop(user_id, None)
        return await message.reply_text(
            t(user_id, "msg_all_cancelled"),
            reply_markup=build_main_keyboard(user_id)
        )

    if canon == close_en:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            t(user_id, "msg_menu_closed"),
            reply_markup=build_main_keyboard(user_id)
        )

    from state import user_status
    from utils import slot_number
    for job_id, info in list(user_status.get(user_id, {}).items()):
        n = slot_number(job_id)
        if f"Cancel Slot {n}:" in canon:
            await do_cancel_job(user_id, job_id, message)
            user_setup.pop(user_id, None)
            return await message.reply_text(
                f"✅ Slot {n} cancelled.",
                reply_markup=build_main_keyboard(user_id)
            )

    await message.reply_text(
        "❓ Unknown option.",
        reply_markup=build_cancel_keyboard(user_id, uid=user_id)
    )


async def _handle_compress(client, message: Message, canon: str, setup: dict, user_id: int):
    from handlers.compress import run_compress
    await run_compress(client, message, user_id, canon)


async def _handle_ott_resolution(client, message: Message, raw: str,
                                  setup: dict, user_id: int):
    canon     = to_canonical(raw)
    cancel_en = t(None, "btn_ott_cancel")

    if canon == cancel_en:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            "❌ OTT download cancelled.",
            reply_markup=build_main_keyboard(user_id)
        )

    res_map   = setup.get("detected_res_map",   OTT_RES_LABEL_TO_FMT)
    audio_map = setup.get("detected_audio_map", OTT_AUDIO_LANGS)

    clean = raw.lstrip("✅ ").strip()
    if clean in res_map:
        setup["ott_res_label"] = clean
        setup["ott_format"]    = res_map[clean]
        setup["step"]          = "ott_audio"
        return await message.reply_text(
            f"✅ **Resolution:** `{clean}`\n\n🎧 Now select audio language:",
            reply_markup=build_ott_audio_keyboard_dynamic(
                audio_map, setup.get("ott_audio_label", ""), uid=user_id
            )
        )

    await message.reply_text(
        "❓ Please pick a resolution.",
        reply_markup=build_ott_resolution_keyboard_dynamic(
            res_map, setup.get("ott_res_label", ""), uid=user_id
        )
    )


async def _handle_ott_audio(client, message: Message, raw: str, canon: str,
                             setup: dict, user_id: int):
    res_map   = setup.get("detected_res_map",   OTT_RES_LABEL_TO_FMT)
    audio_map = setup.get("detected_audio_map", OTT_AUDIO_LANGS)

    cancel_en  = t(None, "btn_ott_cancel")
    back_res_en= t(None, "btn_back_res")

    if canon == cancel_en:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            "❌ OTT download cancelled.",
            reply_markup=build_main_keyboard(user_id)
        )

    if canon == back_res_en:
        setup["step"] = "ott_resolution"
        return await message.reply_text(
            "📺 Select resolution:",
            reply_markup=build_ott_resolution_keyboard_dynamic(
                res_map, setup.get("ott_res_label", ""), uid=user_id
            )
        )

    clean = raw.lstrip("✅ ").strip()
    if clean in audio_map:
        setup["ott_audio_label"] = clean
        setup["ott_audio_lang"]  = audio_map[clean]
        setup["step"] = "running"

        title_line = f"📌 `{setup['detected_title'][:50]}`\n" if setup.get("detected_title") else ""
        dur_line   = f"⏱ `{TimeFormatter(setup['detected_duration'] * 1000)}`\n" if setup.get("detected_duration") else ""

        await message.reply_text(
            f"✅ **Setup Complete!**\n\n"
            f"{title_line}{dur_line}"
            f"📺 **Resolution:** `{setup.get('ott_res_label', 'Best')}`\n"
            f"🎧 **Audio:** `{clean}`\n"
            f"📁 **File:** `{setup['filename']}`\n\n"
            f"📥 Starting download...",
            reply_markup=build_main_keyboard(user_id)
        )
        s = user_setup.pop(user_id)
        from handlers.ott import ott_download_task
        asyncio.create_task(ott_download_task(client, message, s, user_id))
        return

    await message.reply_text(
        "❓ Please pick an audio language.",
        reply_markup=build_ott_audio_keyboard_dynamic(
            audio_map, setup.get("ott_audio_label", ""), uid=user_id
        )
    )
