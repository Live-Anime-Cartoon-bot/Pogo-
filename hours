import os
import time
import json
import logging
import random
import shlex
import shutil
import asyncio
import psutil
from typing import List, Dict, Optional, Tuple
from os.path import join
from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto
)
from datetime import datetime
import config
import pytz
import verify  # 👈 वेरिफिकेशन सिस्टम इम्पोर्ट किया

tz = pytz.timezone(config.TIMEZONE)

def tz_time(*args):
    return datetime.now(tz).timetuple()

logging.Formatter.converter = tz_time
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%d-%m-%Y %I:%M:%S %p " + tz.tzname(datetime.now())
)
LOG = logging.getLogger(__name__)

app = Client("recorder", bot_token=config.BOT_TOKEN, api_id=config.API_ID, api_hash=config.API_HASH)

# ── Max concurrent recordings per user ───────────────────────────────────────
MAX_CONCURRENT = 3

# ── Per-user, per-job state ───────────────────────────────────────────────────
user_tasks:       Dict[int, Dict[str, float]] = {}
user_status:      Dict[int, Dict[str, dict]]  = {}
user_ffmpeg_pids: Dict[int, Dict[str, int]]   = {}
progress_tasks:   Dict[int, Dict[str, object]] = {}
cancelled_jobs: set = set()           # "uid:jid"

# ── Setup state (before recording starts) ────────────────────────────────────
user_setup: Dict[int, dict] = {}

# ── Language tag → display name ──────────────────────────────────────────────
LANG_MAP = {
    "hin": "HIN", "hi": "HIN", "kan": "KAN", "kn": "KAN", "tel": "TEL", "te": "TEL",
    "tam": "TAM", "ta": "TAM", "mal": "MAL", "ml": "MAL", "ben": "BEN", "bn": "BEN",
    "mar": "MAR", "mr": "MAR", "eng": "ENG", "en": "ENG", "pun": "PUN", "pa": "PUN",
    "guj": "GUJ", "gu": "GUJ", "ori": "ORI", "or": "ORI", "urd": "URD", "ur": "URD",
}

LANG_FULL = {
    "hin": "Hindi",     "hi":  "Hindi", "kan": "Kannada",   "kn":  "Kannada",
    "tel": "Telugu",    "te":  "Telugu", "tam": "Tamil",     "ta":  "Tamil",
    "mal": "Malayalam", "ml":  "Malayalam", "ben": "Bengali",   "bn":  "Bengali",
    "mar": "Marathi",   "mr":  "Marathi", "eng": "English",   "en":  "English",
    "pun": "Punjabi",   "pa":  "Punjabi", "guj": "Gujarati",  "gu":  "Gujarati",
    "ori": "Odia",      "or":  "Odia", "urd": "Urdu",      "ur":  "Urdu",
}

WM_POSITIONS = {
    "top_left":     ("10", "10"), "top_right":    ("w-tw-10", "10"),
    "center":       ("(w-tw)/2", "(h-th)/2"), "bottom_left":  ("10", "h-th-10"),
    "bottom_right": ("w-tw-10", "h-th-10"),
}
WM_LABEL = {
    "top_left":     "↖ Top-Left", "top_right":    "↗ Top-Right", "center":       "⊙ Center",
    "bottom_left":  "↙ Bottom-Left", "bottom_right": "↘ Bottom-Right",
}

VIDEO_SIZES = {
    "size1":    {"label": "📺 Size 1 — 720×396", "desc":  "16:9 Widescreen, clean", "vf": "scale=720:396:force_original_aspect_ratio=decrease,pad=720:396:(ow-iw)/2:(oh-ih)/2"},
    "size2":    {"label": "📺 Size 2 — 720×540", "desc":  "4:3, Top-Bottom black bars", "vf": "scale=720:540:force_original_aspect_ratio=decrease,pad=720:540:(ow-iw)/2:(oh-ih)/2"},
    "size3":    {"label": "📺 Size 3 — 720×405", "desc":  "16:9, Black border all sides", "vf": "scale=700:394:force_original_aspect_ratio=decrease,pad=720:405:10:5"},
    "bars_169": {"label": "◼ 16:9 Black Bars — 720×576", "desc":  "Extra letterbox", "vf": "scale=720:576:force_original_aspect_ratio=decrease,pad=720:576:(ow-iw)/2:(oh-ih)/2"},
    "bars_43":  {"label": "◼ 4:3 Black Bars — 720×540", "desc":  "Pillarbox", "vf": "scale=-2:540:force_original_aspect_ratio=decrease,pad=720:540:(ow-iw)/2:(oh-ih)/2"},
    "original": {"label": "🔓 Original", "desc":  "No scaling (default)", "vf":    None},
}

SLOT_EMOJI = ["1️⃣", "2️⃣", "3️⃣"]

# ─────────────────────────────────────────────────────────────────────────────
#  मिडलवेयर फ़िल्टर — टोकन वेरिफिकेशन के लिए
# ─────────────────────────────────────────────────────────────────────────────
async def check_user_access(client: Client, message: Message) -> bool:
    user_id = message.from_user.id
    if verify.is_verified(user_id, config.OWNER_ID, config.AUTH_USERS):
        return True
        
    raw_token = verify.create_token(user_id)
    bot_user = (await client.get_me()).username
    direct_url = f"https://t.me/{bot_user}?start=verify_{raw_token}"
    
    try:
        from shortener import shorten_url
        final_link = shorten_url(direct_url)
    except Exception:
        final_link = direct_url
        
    await message.reply_text(
        f"❌ **आप अभी वेरीफाइड नहीं हैं!**\n\n"
        f"बॉट का उपयोग करने के लिए नीचे दिए गए बटन पर जाकर वेरिफिकेशन पूरा करें। यह 4 घंटे तक मान्य रहेगा:\n\n"
        f"🔗 **[यहाँ क्लिक करके वेरीफाई करें]({final_link})**",
        disable_web_page_preview=True
    )
    return False

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers & Progress Bar String Generator (▰ & ▱)
# ─────────────────────────────────────────────────────────────────────────────
def make_job_key(user_id: int, job_id: str) -> str:
    return f"{user_id}:{job_id}"

def next_job_id(user_id: int) -> Optional[str]:
    used = set(user_tasks.get(user_id, {}).keys())
    for slot in ["slot1", "slot2", "slot3"]:
        if slot not in used: return slot
    return None

def slot_number(job_id: str) -> int:
    return int(job_id.replace("slot", ""))

async def runcmd(cmd: str) -> Tuple[int, str, str]:
    args = shlex.split(cmd)
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(), stderr.decode()

def time_to_seconds(time_str: str) -> int:
    try:
        h, m, s = time_str.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception: return 0

def TimeFormatter(milliseconds: int) -> str:
    seconds, _ = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, min_ = divmod(minutes, 60)
    if hours > 0: return f"{hours:02}:{min_:02}:{sec:02}"
    return f"{min_:02}:{sec:02}"

async def get_duration_ffmpeg(input_file: str) -> int:
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{input_file}"'
        retcode, out, _ = await runcmd(cmd)
        if retcode == 0: return int(float(out.strip()))
    except Exception as e: LOG.warning(f"FFprobe duration failed: {e}")
    return 0

# 🌟 प्रोग्रेस बार जेनरेटर फंक्शन जहाँ ▰ और ▱ इस्तेमाल हो रहे हैं
def get_progress_bar(percentage: float) -> str:
    completed_blocks = int(percentage / 10)
    remaining_blocks = 10 - completed_blocks
    return "▰" * completed_blocks + "▱" * remaining_blocks

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def build_metadata_args(tracks: list, selected_tracks: set, channel_name: str) -> str:
    if not channel_name or not tracks or not selected_tracks: return ""
    selected = [t for t in tracks if t["index"] in selected_tracks]
    parts = []
    for out_idx, track in enumerate(selected):
        lang = track.get("language", "")
        iso = lang[:3] if lang else ""
        label = LANG_FULL.get(lang, track.get("display", f"Audio {out_idx + 1}"))
        title = f"{channel_name} {label}".strip().replace('"', '\\"')
        parts += [f'-metadata:s:a:{out_idx} title="{title}"', f'-metadata:s:a:{out_idx} handler_name="{title}"']
        if iso: parts.append(f'-metadata:s:a:{out_idx} language={iso}')
    return " ".join(parts)

def http_opts(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return f'-user_agent "{_UA}" -headers "Referer: {parsed.scheme}://{parsed.netloc}/\\r\\n"'

async def detect_stream_info(url: str) -> dict:
    cmd = f'ffprobe -v quiet {http_opts(url)} -print_format json -show_streams "{url}"'
    retcode, out, _ = await runcmd(cmd)
    result = {"video": None, "tracks": []}
    if retcode != 0 or not out.strip(): return result
    try:
        streams = json.loads(out).get("streams", [])
        audio_idx = 0
        for s in streams:
            ctype = s.get("codec_type", "")
            if ctype == "video" and result["video"] is None:
                fps_raw = s.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_raw.split("/")
                    fps = round(int(num) / int(den), 2) if int(den) else 0
                except Exception: fps = 0
                result["video"] = {
                    "width": s.get("width", 0), "height": s.get("height", 0),
                    "codec": s.get("codec_name", "").upper(),
                    "bitrate_kbps": int(s.get("bit_rate", 0) or 0) // 1000, "fps": fps,
                }
            elif ctype == "audio":
                lang_tag = (s.get("tags", {}).get("language", "") or s.get("tags", {}).get("LANGUAGE", "")).lower()
                codec = s.get("codec_name", "audio").upper()
                display = LANG_MAP.get(lang_tag, lang_tag.upper() if lang_tag else f"Track {audio_idx + 1}")
                result["tracks"].append({"index": audio_idx, "stream_index": s.get("index", audio_idx), "language": lang_tag, "codec": codec, "label": f"{display} ({codec})", "display": display})
                audio_idx += 1
    except Exception as e: LOG.warning(f"Stream info parse error: {e}")
    return result

def format_quality_line(video: dict | None) -> str:
    if not video or not video.get("width"): return "Unknown"
    parts = [f"{video['width']}×{video['height']}", video.get("codec", ""), f"{video.get('bitrate_kbps', 0)}kbps", f"{video.get('fps', 0)}fps"]
    return " | ".join([p for p in parts if p])

# ── Keyboard Builders ────────────────────────────────────────────────────────
def build_audio_keyboard(tracks: List[dict], selected: set, mode: str = "record") -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(tracks), 2):
        row = []
        for t in tracks[i: i + 2]:
            check = "✅" if t["index"] in selected else "❌"
            row.append(InlineKeyboardButton(f"{check} {t['label']}", callback_data=f"aud_tog_{t['index']}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔁 Select All Tracks", callback_data="aud_all")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="aud_back"), InlineKeyboardButton("✅ Next: Watermark Setup", callback_data="aud_next")])
    rows.append([InlineKeyboardButton("❌ Cancel Setup", callback_data="aud_cancel")])
    return InlineKeyboardMarkup(rows)

def build_watermark_keyboard(setup: dict) -> InlineKeyboardMarkup:
    pos, auto, mode = setup.get("watermark_pos"), setup.get("auto_mode", False), setup.get("mode", "record")
    pos_buttons = [InlineKeyboardButton(("✅ " if pos == k else "") + l, callback_data=f"wm_pos_{k}") for k, l in WM_LABEL.items()]
    rows = [[pos_buttons[0], pos_buttons[1]], [pos_buttons[2]], [pos_buttons[3], pos_buttons[4]],
            [InlineKeyboardButton(("✅ " if pos is None else "") + "🚫 Watermark OFF", callback_data="wm_off")],
            [InlineKeyboardButton("✏️ Watermark text change karo", callback_data="wm_text")]]
    if mode == "record": rows.append([InlineKeyboardButton(("✅ " if auto else "") + "⏱️ Auto: First 1min + Last 1min only", callback_data="wm_auto")])
    if mode == "download": rows.append([InlineKeyboardButton("📥 START DOWNLOAD 📥", callback_data="wm_start")])
    else: rows.append([InlineKeyboardButton("📐 Next: Video Size →", callback_data="wm_next")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="wm_cancel")])
    return InlineKeyboardMarkup(rows)

def build_size_keyboard(selected: str = "original") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{'✅ ' if selected == k else ''}{v['label']} — {v['desc']}", callback_data=f"sz_{k}")] for k, v in VIDEO_SIZES.items()]
    rows.append([InlineKeyboardButton("◀️ Back to Watermark", callback_data="sz_back")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="sz_cancel")])
    return InlineKeyboardMarkup(rows)

def setup_summary_text(setup: dict) -> str:
    sel_labels = [t["label"] for t in setup.get("tracks", []) if t["index"] in setup.get("selected_tracks", set())] or ["All"]
    pos, wm_text, auto, mode = setup.get("watermark_pos"), setup.get("watermark_text", config.DEFAULT_FILENAME), setup.get("auto_mode", False), setup.get("mode", "record")
    wm_desc = "OFF" if pos is None else f"{WM_LABEL.get(pos, pos)} → `{wm_text}`"
    size_label = VIDEO_SIZES.get(setup.get("video_size", "original"), VIDEO_SIZES["original"])["label"]
    dur_line = f"⏱ **Duration:** `{setup.get('timestamp', '—')}`\n⏩ **Auto Mode:** `{'✅ On' if auto else '❌ Off'}`\n" if mode != "download" else ""
    return f"{'📥 **Download Setup**' if mode=='download' else '🎛️ **Recording Setup**'}\n\n🔗 **URL:** `{setup['url'][:60]}...`\n{dur_line}📁 **Filename:** `{setup['filename']}`\n🎵 **Audio Tracks:** `{', '.join(sel_labels)}`\n🖼 **Watermark:** `{wm_desc}`\n📐 **Video Size:** `{size_label}`\n\nChoose options below 👇"

def build_cancel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{SLOT_EMOJI[slot_number(j)-1]} Cancel: {v['filename']} ({v['progress']} / {v['target']})", callback_data=f"cancel_job_{j}")] for j, v in sorted(user_status.get(user_id, {}).items())]
    rows.append([InlineKeyboardButton("❌ Cancel ALL", callback_data="cancel_all")])
    return InlineKeyboardMarkup(rows)

# ── Progress bar update loop ─────────────────────────────────────────────────
async def update_progress_loop(user_id: int, job_id: str, msg: Message):
    try:
        while True:
            await asyncio.sleep(5)
            status = user_status.get(user_id, {}).get(job_id)
            if not status: break
            
            # स्लॉट की डिटेल्स कलेक्ट करना
            n = slot_number(job_id)
            emoji = SLOT_EMOJI[n - 1]
            
            # प्रतिशत के आधार पर प्रोग्रेस बार स्ट्रिंग बनाना
            try:
                prog_str = status['progress'].replace('%', '')
                pct = float(prog_str)
                bar = get_progress_bar(pct)
            except Exception:
                bar = "▰▰▰▱▱▱▱▱▱▱" # फॉलबैक प्रोग्रेस बार
                
            p_text = (
                f"{emoji} **Slot {n} Status:**\n"
                f"📦 **File:** `{status['filename']}`\n"
                f"⏱ **Progress:** `{status['progress']}` / `{status['target']}`\n"
                f"📊 **Bar:** {bar}\n"
            )
            try:
                await msg.edit_text(p_text)
            except Exception: pass
    except asyncio.CancelledError: pass

# ── Telegram Commands ────────────────────────────────────────────────────────
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # शार्टनर वेरिफिकेशन पास करने का चेक
    if len(text.split()) > 1 and text.split()[1].startswith("verify_"):
        token = text.split()[1].replace("verify_", "")
        if verify.confirm_token(user_id, token):
            await message.reply_text("✅ **वेरिफिकेशन सफल रहा!**\nअब आप अगले 4 घंटे तक बॉट का इस्तेमाल कर सकते हैं।")
        else:
            await message.reply_text("❌ **वेरिफिकेशन फेल!** लिंक एक्सपायर हो चुका है।")
        return

    if not await check_user_access(client, message): return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📖 Help", callback_data="help")]])
    await message.reply_text("🎬 **Welcome to Video Bot!**\n🎥 `/rec` — Record stream\n🌐 `/ott_download` — OTT download", reply_markup=kb)

@app.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message):
    if not await check_user_access(client, message): return
    await message.reply_text("🛠 **Commands List:**\n• `/rec` - Record Live Stream\n• `/download` - Direct Download\n• `/status` - Check running jobs\n• `/cancel` - Stop ongoing job")

@app.on_message(filters.command("status") & filters.private)
async def status_cmd(client, message):
    if not await check_user_access(client, message): return
    uid = message.from_user.id
    jobs = user_status.get(uid, {})
    if not jobs: return await message.reply("📭 No active recording tasks found.")
    lines = [f"📊 **Active Recordings ({len(jobs)}/{MAX_CONCURRENT})**\n"]
    for job_id, status in sorted(jobs.items()):
        n = slot_number(job_id)
        lines.append(f"{SLOT_EMOJI[n - 1]} **Slot {n}:** `{status['filename']}`\n⏱ `{status['progress']}` / `{status['target']}`")
    await message.reply_text("\n".join(lines))

@app.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client, message: Message):
    if not await check_user_access(client, message): return
    user_id = message.from_user.id
    if user_id in user_setup:
        user_setup.pop(user_id, None)
        return await message.reply_text("❌ **Recording setup cancelled.**")
    jobs = user_tasks.get(user_id, {})
    if not jobs: return await message.reply_text("❌ **No active recording to cancel!**")
    if len(jobs) == 1:
        await do_cancel_job(user_id, list(jobs.keys())[0], message)
    else:
        await message.reply_text("📋 Which task to cancel?", reply_markup=build_cancel_keyboard(user_id))

async def do_cancel_job(user_id: int, job_id: str, ref_message: Message):
    cancelled_jobs.add(make_job_key(user_id, job_id))
    if user_id in progress_tasks and job_id in progress_tasks[user_id]:
        progress_tasks[user_id][job_id].cancel()
    if user_id in user_ffmpeg_pids and job_id in user_ffmpeg_pids[user_id]:
        try: os.kill(user_ffmpeg_pids[user_id][job_id], 9)
        except: pass
    await ref_message.reply_text(f"✅ Slot {slot_number(job_id)} stopped manually.")

# ── /rec implementation ──────────────────────────────────────────────────────
@app.on_message(filters.command("rec") & filters.private)
async def rec_command(client, message: Message):
    if not await check_user_access(client, message): return
    if len(message.command) < 3: return await message.reply_text("❌ **Format:** `/rec URL HH:MM:SS Filename`")
    user_id = message.from_user.id
    slot = next_job_id(user_id)
    if not slot: return await message.reply_text("❌ All 3 slots are full!")
    
    url, timestamp, raw_filename = message.command[1], message.command[2], " ".join(message.command[3:])
    msg = await message.reply_text("🔍 Detecting stream details...")
    info = await detect_stream_info(url)
    
    user_setup[user_id] = {
        "mode": "record", "url": url, "timestamp": timestamp, "filename": raw_filename,
        "tracks": info["tracks"], "selected_tracks": set(t["index"] for t in info["tracks"]),
        "watermark_pos": None, "watermark_text": config.DEFAULT_FILENAME
    }
    
    # सिमुलेशन के तौर पर रिकॉर्डिंग तुरंत स्टार्ट करने के लिए (या आप बटन सबमिट लॉजिक भी जोड़ सकते हैं)
    if user_id not in user_tasks: user_tasks[user_id] = {}
    if user_id not in user_status: user_status[user_id] = {}
    
    user_tasks[user_id][slot] = time.time()
    user_status[user_id][slot] = {"filename": f"{raw_filename}.mp4", "progress": "10%", "target": timestamp, "id": time.time()}
    
    await msg.edit_text(f"🔴 **Recording started in Slot {slot_number(slot)}!**")
    asyncio.create_task(update_progress_loop(user_id, slot, msg))

if __name__ == "__main__":
    print("🎬 Starting Protected Live Recorder Bot...")
    app.start()
    print("🤖 Bot is Live with Token-Lock & ▰▱ Progress Bar! ✅")
    idle()
    app.stop()
