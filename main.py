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
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from datetime import datetime
import config
import pytz

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
# user_tasks      : {user_id: {job_id: start_time}}
# user_status     : {user_id: {job_id: {id, filename, target, progress, save_dir}}}
# user_ffmpeg_pids: {user_id: {job_id: pid}}
# progress_tasks  : {user_id: {job_id: asyncio.Task}}
# cancelled_jobs  : set of "user_id:job_id" strings
user_tasks:       Dict[int, Dict[str, float]] = {}
user_status:      Dict[int, Dict[str, dict]]  = {}
user_ffmpeg_pids: Dict[int, Dict[str, int]]   = {}
progress_tasks:   Dict[int, Dict[str, object]] = {}
cancelled_jobs: set = set()           # "uid:jid"

# ── Setup state (before recording starts) ────────────────────────────────────
user_setup: Dict[int, dict] = {}

# ── Language tag → display name ──────────────────────────────────────────────
LANG_MAP = {
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

# ── Full language names for metadata tags ─────────────────────────────────────
LANG_FULL = {
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

# ── Watermark positions ───────────────────────────────────────────────────────
WM_POSITIONS = {
    "top_left":     ("10", "10"),
    "top_right":    ("w-tw-10", "10"),
    "center":       ("(w-tw)/2", "(h-th)/2"),
    "bottom_left":  ("10", "h-th-10"),
    "bottom_right": ("w-tw-10", "h-th-10"),
}
WM_LABEL = {
    "top_left":     "↖ Top-Left",
    "top_right":    "↗ Top-Right",
    "center":       "⊙ Center",
    "bottom_left":  "↙ Bottom-Left",
    "bottom_right": "↘ Bottom-Right",
}

# ── Video size presets ────────────────────────────────────────────────────────
VIDEO_SIZES = {
    "size1":    {
        "label": "📺 Size 1 — 720×396",
        "desc":  "16:9 Widescreen, clean",
        "vf":    "scale=720:396:force_original_aspect_ratio=decrease,pad=720:396:(ow-iw)/2:(oh-ih)/2",
    },
    "size2":    {
        "label": "📺 Size 2 — 720×540",
        "desc":  "4:3, Top-Bottom black bars",
        "vf":    "scale=720:540:force_original_aspect_ratio=decrease,pad=720:540:(ow-iw)/2:(oh-ih)/2",
    },
    "size3":    {
        "label": "📺 Size 3 — 720×405",
        "desc":  "16:9, Black border all sides",
        "vf":    "scale=700:394:force_original_aspect_ratio=decrease,pad=720:405:10:5",
    },
    "bars_169": {
        "label": "◼ 16:9 Black Bars — 720×576",
        "desc":  "Extra letterbox",
        "vf":    "scale=720:576:force_original_aspect_ratio=decrease,pad=720:576:(ow-iw)/2:(oh-ih)/2",
    },
    "bars_43":  {
        "label": "◼ 4:3 Black Bars — 720×540",
        "desc":  "Pillarbox",
        "vf":    "scale=-2:540:force_original_aspect_ratio=decrease,pad=720:540:(ow-iw)/2:(oh-ih)/2",
    },
    "original": {
        "label": "🔓 Original",
        "desc":  "No scaling (default)",
        "vf":    None,
    },
}

# ── Slot emojis for job numbering ─────────────────────────────────────────────
SLOT_EMOJI = ["1️⃣", "2️⃣", "3️⃣"]


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_job_key(user_id: int, job_id: str) -> str:
    return f"{user_id}:{job_id}"


def next_job_id(user_id: int) -> Optional[str]:
    """Return next available slot name (slot1/slot2/slot3) or None if full."""
    used = set(user_tasks.get(user_id, {}).keys())
    for slot in ["slot1", "slot2", "slot3"]:
        if slot not in used:
            return slot
    return None


def slot_number(job_id: str) -> int:
    return int(job_id.replace("slot", ""))


async def runcmd(cmd: str) -> Tuple[int, str, str]:
    args = shlex.split(cmd)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(), stderr.decode()


def time_to_seconds(time_str: str) -> int:
    try:
        h, m, s = time_str.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return 0


def TimeFormatter(milliseconds: int) -> str:
    seconds, _ = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, min_ = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02}:{min_:02}:{sec:02}"
    return f"{min_:02}:{sec:02}"


async def get_duration_ffmpeg(input_file: str) -> int:
    try:
        cmd = (
            f'ffprobe -v error -show_entries format=duration '
            f'-of default=noprint_wrappers=1:nokey=1 "{input_file}"'
        )
        retcode, out, _ = await runcmd(cmd)
        if retcode == 0:
            return int(float(out.strip()))
    except Exception as e:
        LOG.warning(f"FFprobe duration failed: {e}")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
#  Browser headers for HLS streams that require them
# ─────────────────────────────────────────────────────────────────────────────

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def build_metadata_args(tracks: list, selected_tracks: set, channel_name: str) -> str:
    """Return -metadata:s:a:N ffmpeg args for each selected audio track."""
    if not channel_name or not tracks or not selected_tracks:
        return ""
    selected = [t for t in tracks if t["index"] in selected_tracks]
    parts = []
    for out_idx, track in enumerate(selected):
        lang  = track.get("language", "")
        # 3-char ISO code (e.g. "hin") preferred for the language tag
        iso   = lang[:3] if lang else ""
        label = LANG_FULL.get(lang, track.get("display", f"Audio {out_idx + 1}"))
        title = f"{channel_name} {label}".strip()
        safe  = title.replace('"', '\\"')
        parts += [
            f'-metadata:s:a:{out_idx} title="{safe}"',
            f'-metadata:s:a:{out_idx} handler_name="{safe}"',
        ]
        if iso:
            parts.append(f'-metadata:s:a:{out_idx} language={iso}')
    return " ".join(parts)


def http_opts(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    return (
        f'-user_agent "{_UA}" '
        f'-headers "Referer: {origin}\\r\\n"'
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Audio track detection
# ─────────────────────────────────────────────────────────────────────────────

async def detect_audio_tracks(url: str) -> List[dict]:
    cmd = (
        f'ffprobe -v quiet {http_opts(url)} -print_format json -show_streams '
        f'-select_streams a "{url}"'
    )
    retcode, out, _ = await runcmd(cmd)
    tracks = []
    if retcode != 0 or not out.strip():
        return tracks
    try:
        data = json.loads(out)
        for idx, stream in enumerate(data.get("streams", [])):
            lang_tag = (
                stream.get("tags", {}).get("language", "")
                or stream.get("tags", {}).get("LANGUAGE", "")
            ).lower()
            codec = stream.get("codec_name", "audio").upper()
            display = LANG_MAP.get(lang_tag, lang_tag.upper() if lang_tag else f"Track {idx + 1}")
            tracks.append({
                "index": idx,
                "stream_index": stream.get("index", idx),
                "language": lang_tag,
                "codec": codec,
                "label": f"{display} ({codec})",
                "display": display,
            })
    except Exception as e:
        LOG.warning(f"Track parse error: {e}")
    return tracks


async def detect_stream_info(url: str) -> dict:
    """One ffprobe call — returns video quality dict + audio tracks list."""
    cmd = (
        f'ffprobe -v quiet {http_opts(url)} -print_format json '
        f'-show_streams "{url}"'
    )
    retcode, out, _ = await runcmd(cmd)
    result = {"video": None, "tracks": []}
    if retcode != 0 or not out.strip():
        return result
    try:
        streams = json.loads(out).get("streams", [])
        audio_idx = 0
        for s in streams:
            ctype = s.get("codec_type", "")
            if ctype == "video" and result["video"] is None:
                w   = s.get("width",  0)
                h   = s.get("height", 0)
                fps_raw = s.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_raw.split("/")
                    fps = round(int(num) / int(den), 2) if int(den) else 0
                except Exception:
                    fps = 0
                br  = int(s.get("bit_rate", 0) or 0) // 1000   # kbps
                result["video"] = {
                    "width":  w,
                    "height": h,
                    "codec":  s.get("codec_name", "").upper(),
                    "bitrate_kbps": br,
                    "fps":    fps,
                }
            elif ctype == "audio":
                lang_tag = (
                    s.get("tags", {}).get("language", "")
                    or s.get("tags", {}).get("LANGUAGE", "")
                ).lower()
                codec   = s.get("codec_name", "audio").upper()
                display = LANG_MAP.get(lang_tag, lang_tag.upper() if lang_tag else f"Track {audio_idx + 1}")
                result["tracks"].append({
                    "index":        audio_idx,
                    "stream_index": s.get("index", audio_idx),
                    "language":     lang_tag,
                    "codec":        codec,
                    "label":        f"{display} ({codec})",
                    "display":      display,
                })
                audio_idx += 1
    except Exception as e:
        LOG.warning(f"Stream info parse error: {e}")
    return result


def format_quality_line(video: dict | None) -> str:
    """Human-readable quality string, e.g. '1920×1080 | H.264 | 2500kbps | 25fps'"""
    if not video or not video.get("width"):
        return "Unknown"
    parts = [f"{video['width']}×{video['height']}"]
    if video.get("codec"):
        parts.append(video["codec"])
    if video.get("bitrate_kbps"):
        parts.append(f"{video['bitrate_kbps']}kbps")
    if video.get("fps"):
        parts.append(f"{video['fps']}fps")
    return " | ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
#  Keyboard builders
# ─────────────────────────────────────────────────────────────────────────────

def build_audio_keyboard(tracks: List[dict], selected: set, mode: str = "record") -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(tracks), 2):
        row = []
        for t in tracks[i: i + 2]:
            check = "✅" if t["index"] in selected else "❌"
            row.append(InlineKeyboardButton(
                f"{check} {t['label']}", callback_data=f"aud_tog_{t['index']}"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔁 Select All Tracks", callback_data="aud_all")])
    next_label = "✅ Next: Watermark Setup" if mode == "download" else "✅ Next: Watermark Setup"
    rows.append([
        InlineKeyboardButton("◀️ Back", callback_data="aud_back"),
        InlineKeyboardButton(next_label, callback_data="aud_next"),
    ])
    rows.append([InlineKeyboardButton("❌ Cancel Setup", callback_data="aud_cancel")])
    return InlineKeyboardMarkup(rows)


def build_watermark_keyboard(setup: dict) -> InlineKeyboardMarkup:
    pos  = setup.get("watermark_pos")
    auto = setup.get("auto_mode", False)
    mode = setup.get("mode", "record")
    pos_buttons = [
        InlineKeyboardButton(
            ("✅ " if pos == key else "") + label,
            callback_data=f"wm_pos_{key}"
        )
        for key, label in WM_LABEL.items()
    ]
    rows = [
        [pos_buttons[0], pos_buttons[1]],
        [pos_buttons[2]],
        [pos_buttons[3], pos_buttons[4]],
        [InlineKeyboardButton(("✅ " if pos is None else "") + "🚫 Watermark OFF", callback_data="wm_off")],
        [InlineKeyboardButton("✏️ Watermark text change karo", callback_data="wm_text")],
    ]
    # Auto mode only available for /rec, not /download
    if mode == "record":
        rows.append([InlineKeyboardButton(
            ("✅ " if auto else "") + "⏱️ Auto: First 1min + Last 1min only",
            callback_data="wm_auto"
        )])
    if mode == "download":
        rows.append([InlineKeyboardButton("📥 START DOWNLOAD 📥", callback_data="wm_start")])
    else:
        rows.append([InlineKeyboardButton("📐 Next: Video Size →", callback_data="wm_next")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="wm_cancel")])
    return InlineKeyboardMarkup(rows)


def build_size_keyboard(selected: str = "original") -> InlineKeyboardMarkup:
    rows = []
    for key, val in VIDEO_SIZES.items():
        check = "✅ " if selected == key else ""
        rows.append([InlineKeyboardButton(
            f"{check}{val['label']} — {val['desc']}",
            callback_data=f"sz_{key}"
        )])
    rows.append([InlineKeyboardButton("◀️ Back to Watermark", callback_data="sz_back")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="sz_cancel")])
    return InlineKeyboardMarkup(rows)


def setup_summary_text(setup: dict) -> str:
    tracks = setup.get("tracks", [])
    selected = setup.get("selected_tracks", set())
    sel_labels = [t["label"] for t in tracks if t["index"] in selected] or ["All"]
    pos = setup.get("watermark_pos")
    wm_text = setup.get("watermark_text", config.DEFAULT_FILENAME)
    auto = setup.get("auto_mode", False)
    mode = setup.get("mode", "record")
    wm_desc = "OFF" if pos is None else f"{WM_LABEL.get(pos, pos)} → `{wm_text}`"
    size_key = setup.get("video_size", "original")
    size_label = VIDEO_SIZES.get(size_key, VIDEO_SIZES["original"])["label"]

    if mode == "download":
        header = "📥 **Download Setup**"
        duration_line = ""
    else:
        header = "🎛️ **Recording Setup**"
        duration_line = f"⏱ **Duration:** `{setup.get('timestamp', '—')}`\n"
        duration_line += f"⏩ **Auto Mode:** `{'✅ First+Last 1min' if auto else '❌ Off'}`\n"

    return (
        f"{header}\n\n"
        f"🔗 **URL:** `{setup['url'][:60]}...`\n"
        f"{duration_line}"
        f"📁 **Filename:** `{setup['filename']}`\n"
        f"🎵 **Audio Tracks:** `{', '.join(sel_labels)}`\n"
        f"🖼 **Watermark:** `{wm_desc}`\n"
        f"📐 **Video Size:** `{size_label}`\n\n"
        f"Choose options below 👇"
    )


def build_cancel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Keyboard to pick which recording to cancel."""
    jobs = user_status.get(user_id, {})
    rows = []
    for job_id, info in sorted(jobs.items()):
        n = slot_number(job_id)
        emoji = SLOT_EMOJI[n - 1]
        rows.append([InlineKeyboardButton(
            f"{emoji} Cancel: {info['filename']} ({info['progress']} / {info['target']})",
            callback_data=f"cancel_job_{job_id}"
        )])
    rows.append([InlineKeyboardButton("❌ Cancel ALL", callback_data="cancel_all")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.user(config.AUTH_USERS))
async def start(client, message):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Help", callback_data="help")],
        [InlineKeyboardButton("💠 Plans", callback_data="plan")],
        [InlineKeyboardButton("📢 Channel", url="https://t.me/LittleSinghamChannel")]
    ])
    await message.reply_text(
        "🎬 **Welcome to Video Bot!**\n\n"
        "🎥 `/rec` — Record stream\n"
        "**Example 1**\n"
        "`/rec http://link 00:00:00 Filename`\n"
        "**Example 2**\n"
        "`http://link 00:00:00 Filename`\n\n"
        "🌐 `/ott_download` — OTT/YouTube download\n"
        "🗜 `/compress` — Compress video _(reply to video)_\n"
        "📸 `/screenshot` — Extract screenshots _(reply to video)_\n"
        "🍪 `/cookies_add` — Add OTT cookies\n\n"
        "📚 Use /help for full instructions.",
        reply_markup=kb
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /alive
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("alive"))
async def alive_cmd(client, message):
    await message.reply_text("✅ **Bot working, you can use it!**")


# ─────────────────────────────────────────────────────────────────────────────
#  /help
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("help") & filters.user(config.AUTH_USERS))
async def help_cmd(client, message):
    await message.reply_text(
        "🛠 **Bot Help Menu**\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎥 **RECORDING**\n"
        "```\n/rec http://link 00:00:00 Filename\n```\n"
        "📥 **STREAM DOWNLOAD**\n"
        "```\n/download http://link Filename\n```\n"
        "🌐 **OTT / YouTube DOWNLOAD**\n"
        "```\n/ott_download https://youtube.com/... Filename\n```\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ **All Commands:**\n"
        "• 🎥 `/rec` — Record stream with duration\n"
        "• 📥 `/download` — Download stream (full)\n"
        "• 🌐 `/ott_download` — OTT/YouTube download\n"
        "• 🗜 `/compress` — Compress video _(reply to video)_\n"
        "• 📸 `/screenshot [1-30]` — Screenshots _(reply to video)_\n"
        "• 🛑 `/cancel` — Stop active task\n"
        "• 📊 `/status` — All active tasks\n\n"
        "🍪 **Cookies (for OTT sites):**\n"
        "• `/cookies_add` — Upload cookies.txt\n"
        "• `/cookies_status` — Check cookie info\n"
        "• `/del_cookies` — Delete cookies\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🆕 **Features:**\n"
        "• 🎵 Audio track selection (HIN, KAN, TEL, TAM, MAL, BEN, MAR)\n"
        "• 🖼 Watermark (5 positions + custom text)\n"
        "• ⏩ Auto mode: First 1min + Last 1min _(rec only)_\n"
        "• 🔢 Up to **3 simultaneous** tasks\n\n"
        "📝 **Notes:**\n"
        "🔸 Stream must be DRM-free\n"
        "🔸 `/rec` timestamp format: `HH:MM:SS`\n"
        "🔸 `/screenshot` max 30 per video\n"
        f"🔸 Default filename: `{config.DEFAULT_FILENAME}`\n\n"
        "👨‍💻 _Bot maintained by @LS_Owner_bot",
        disable_web_page_preview=True
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /status
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("status") & filters.user(config.AUTH_USERS))
async def status_cmd(client, message):
    uid = message.from_user.id
    jobs = user_status.get(uid, {})
    if not jobs:
        return await message.reply("📭 No active recording tasks found.")

    lines = [f"📊 **Active Recordings ({len(jobs)}/{MAX_CONCURRENT})**\n"]
    for job_id, status in sorted(jobs.items()):
        n = slot_number(job_id)
        emoji = SLOT_EMOJI[n - 1]
        start_dt = datetime.fromtimestamp(status["id"], tz=tz).strftime("%I:%M:%S %p")
        target_s = time_to_seconds(status["target"])
        progress_s = time_to_seconds(status["progress"])
        remaining = max(target_s - progress_s, 0)
        eta = TimeFormatter(remaining * 1000)
        ffmpeg_ok = "✅" if uid in user_ffmpeg_pids and job_id in user_ffmpeg_pids[uid] else "❌"
        lines.append(
            f"{emoji} **Slot {n}**\n"
            f"  📁 `{status['filename']}`\n"
            f"  ⏱ `{status['progress']}` / `{status['target']}`\n"
            f"  ⏳ ETA: `{eta}`  🕒 Started: `{start_dt}`\n"
            f"  🔧 FFmpeg: {ffmpeg_ok}\n"
        )
    lines.append("🛑 Use /cancel to stop a recording")
    await message.reply_text("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
#  /cancel
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("cancel") & filters.user(config.AUTH_USERS))
async def cancel_command(client, message: Message):
    user_id = message.from_user.id

    # Cancel setup phase
    if user_id in user_setup:
        user_setup.pop(user_id, None)
        return await message.reply_text("❌ **Recording setup cancelled.**")

    jobs = user_tasks.get(user_id, {})
    if not jobs:
        return await message.reply_text("❌ **No active recording to cancel!**")

    if len(jobs) == 1:
        job_id = list(jobs.keys())[0]
        await do_cancel_job(user_id, job_id, message)
    else:
        await message.reply_text(
            f"📋 **You have {len(jobs)} active recordings.**\nWhich one do you want to cancel?",
            reply_markup=build_cancel_keyboard(user_id)
        )


async def do_cancel_job(user_id: int, job_id: str, ref_message: Message):
    job_key = make_job_key(user_id, job_id)
    cancelled_jobs.add(job_key)

    # Cancel progress task
    if user_id in progress_tasks and job_id in progress_tasks[user_id]:
        progress_tasks[user_id][job_id].cancel()
        del progress_tasks[user_id][job_id]

    # Kill FFmpeg
    if user_id in user_ffmpeg_pids and job_id in user_ffmpeg_pids[user_id]:
        pid = user_ffmpeg_pids[user_id][job_id]
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except Exception:
                    pass
            parent.kill()
            psutil.wait_procs([parent] + children, timeout=3)
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            LOG.error(f"Kill FFmpeg error: {e}")
        del user_ffmpeg_pids[user_id][job_id]

    info = user_status.get(user_id, {}).get(job_id, {})
    filename = info.get("filename", "Unknown")
    n = slot_number(job_id)
    emoji = SLOT_EMOJI[n - 1]

    await ref_message.reply_text(
        f"✅ **Recording Cancelled!**\n\n"
        f"{emoji} **Slot {n}:** `{filename}`\n"
        f"🛑 Stopped — uploading recorded portion..."
    )


@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_job_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data

    if data == "cancel_all":
        jobs = list(user_tasks.get(user_id, {}).keys())
        for job_id in jobs:
            await do_cancel_job(user_id, job_id, query.message)
        await query.edit_message_text("✅ **All recordings cancelled.**")
        await query.answer()
    elif data.startswith("cancel_job_"):
        job_id = data[len("cancel_job_"):]
        if job_id in user_tasks.get(user_id, {}):
            await do_cancel_job(user_id, job_id, query.message)
            await query.edit_message_text(f"✅ **Slot {slot_number(job_id)} cancelled.**")
        else:
            await query.answer("Already done.", show_alert=True)
        await query.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  /rec — detect tracks → show audio selection
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("rec") & filters.user(config.AUTH_USERS))
async def rec_command(client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "📌 **Usage:**\n"
            "```\n/rec http://link 00:00:00 filename\n```"
        )

    user_id = message.from_user.id
    active = len(user_tasks.get(user_id, {}))
    if active >= MAX_CONCURRENT:
        return await message.reply_text(
            f"❌ **Maximum {MAX_CONCURRENT} simultaneous recordings reached!**\n\n"
            f"📊 Check progress with /status\n"
            f"🛑 Stop one with /cancel to free a slot."
        )

    params = " ".join(message.command[1:])
    parts = params.split(" ", 2)
    url = parts[0]
    timestamp = parts[1]
    raw_filename = parts[2].strip() if len(parts) > 2 else config.DEFAULT_FILENAME

    msg = await message.reply_text("🔍 **Detecting Quality... Please wait.**")
    info     = await detect_stream_info(url)
    tracks   = info["tracks"]
    video    = info["video"]
    selected = set(t["index"] for t in tracks)

    user_setup[user_id] = {
        "mode": "record",
        "url": url,
        "timestamp": timestamp,
        "filename": raw_filename,
        "tracks": tracks,
        "selected_tracks": selected,
        "watermark_pos": None,
        "watermark_text": config.DEFAULT_FILENAME,
        "auto_mode": False,
        "chat_id": message.chat.id,
        "reply_to": message.id,
        "video_info": video,
    }

    quality_line = format_quality_line(video)
    audio_line   = ", ".join(t["label"] for t in tracks) if tracks else "No audio tracks detected"

    if tracks:
        text = (
            f"✅ **Stream Detected!**\n\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio:** `{audio_line}`\n"
            f"⏱ **Duration:** `{timestamp}`\n"
            f"📁 **File:** `{raw_filename}`\n\n"
            f"👇 Select audio tracks to include:"
        )
        kb = build_audio_keyboard(tracks, selected, mode="record")
    else:
        text = (
            f"✅ **Stream Detected!**\n\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio:** No tracks found — will auto-select\n"
            f"⏱ **Duration:** `{timestamp}`\n"
            f"📁 **File:** `{raw_filename}`\n\n"
        ) + setup_summary_text(user_setup[user_id])
        kb = build_watermark_keyboard(user_setup[user_id])

    try:
        await msg.edit_text(text, reply_markup=kb)
    except Exception:
        await message.reply_text(text, reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
#  /download — detect tracks → audio selection → watermark → download
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("download") & filters.user(config.AUTH_USERS))
async def download_command(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "📌 **Usage:**\n"
            "```\n/download http://link filename\n```\n"
            "💡 **Example:**\n"
            "`/download https://example.com/video.m3u8 MyVideo`"
        )

    user_id = message.from_user.id
    active = len(user_tasks.get(user_id, {}))
    if active >= MAX_CONCURRENT:
        return await message.reply_text(
            f"❌ **Maximum {MAX_CONCURRENT} simultaneous tasks reached!**\n\n"
            f"📊 Check progress with /status\n"
            f"🛑 Stop one with /cancel to free a slot."
        )

    params = " ".join(message.command[1:])
    parts = params.split(" ", 1)
    url = parts[0]
    raw_filename = parts[1].strip() if len(parts) > 1 else config.DEFAULT_FILENAME

    msg = await message.reply_text("🔍 **Detecting Quality... Please wait.**")
    info     = await detect_stream_info(url)
    tracks   = info["tracks"]
    video    = info["video"]
    selected = set(t["index"] for t in tracks)

    user_setup[user_id] = {
        "mode": "download",
        "url": url,
        "timestamp": None,
        "filename": raw_filename,
        "tracks": tracks,
        "selected_tracks": selected,
        "watermark_pos": None,
        "watermark_text": config.DEFAULT_FILENAME,
        "auto_mode": False,
        "chat_id": message.chat.id,
        "reply_to": message.id,
        "video_info": video,
    }

    quality_line = format_quality_line(video)
    audio_line   = ", ".join(t["label"] for t in tracks) if tracks else "No audio tracks detected"

    if tracks:
        text = (
            f"✅ **Stream Detected!**\n\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio:** `{audio_line}`\n"
            f"📁 **File:** `{raw_filename}`\n\n"
            f"👇 Select audio tracks to include:"
        )
        kb = build_audio_keyboard(tracks, selected, mode="download")
    else:
        text = (
            f"✅ **Stream Detected!**\n\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio:** No tracks found — will auto-select\n"
            f"📁 **File:** `{raw_filename}`\n\n"
        ) + setup_summary_text(user_setup[user_id])
        kb = build_watermark_keyboard(user_setup[user_id])

    try:
        await msg.edit_text(text, reply_markup=kb)
    except Exception:
        await message.reply_text(text, reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
#  Callback: Audio track selection
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^aud_"))
async def audio_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    setup = user_setup.get(user_id)
    if not setup:
        return await query.answer("❌ Session expired. Use /rec again.", show_alert=True)

    data = query.data
    tracks = setup["tracks"]
    selected: set = setup["selected_tracks"]

    mode = setup.get("mode", "record")

    if data.startswith("aud_tog_"):
        idx = int(data.split("_")[-1])
        selected.discard(idx) if idx in selected else selected.add(idx)
        setup["selected_tracks"] = selected
        await query.edit_message_reply_markup(build_audio_keyboard(tracks, selected, mode=mode))
        await query.answer()

    elif data == "aud_all":
        setup["selected_tracks"] = set() if len(selected) == len(tracks) else set(t["index"] for t in tracks)
        await query.edit_message_reply_markup(build_audio_keyboard(tracks, setup["selected_tracks"], mode=mode))
        await query.answer("🔁 Toggled all tracks")

    elif data == "aud_back":
        await query.answer("Already at video selection.")

    elif data == "aud_next":
        if not setup["selected_tracks"] and tracks:
            return await query.answer("⚠️ Select at least one audio track!", show_alert=True)
        await query.edit_message_text(setup_summary_text(setup), reply_markup=build_watermark_keyboard(setup))
        await query.answer()

    elif data == "aud_cancel":
        user_setup.pop(user_id, None)
        label = "Download" if mode == "download" else "Recording"
        await query.edit_message_text(f"❌ **{label} setup cancelled.**")
        await query.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  Callback: Watermark & recording options
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^wm_"))
async def watermark_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    setup = user_setup.get(user_id)
    if not setup:
        return await query.answer("❌ Session expired. Use /rec again.", show_alert=True)

    data = query.data

    if data.startswith("wm_pos_"):
        setup["watermark_pos"] = data[len("wm_pos_"):]
        await query.edit_message_text(setup_summary_text(setup), reply_markup=build_watermark_keyboard(setup))
        await query.answer(f"✅ {WM_LABEL.get(setup['watermark_pos'])}")

    elif data == "wm_off":
        setup["watermark_pos"] = None
        await query.edit_message_text(setup_summary_text(setup), reply_markup=build_watermark_keyboard(setup))
        await query.answer("🚫 Watermark disabled")

    elif data == "wm_text":
        setup["awaiting_wm_text"] = True
        await query.answer("✏️ Send your watermark text as a message now.", show_alert=True)

    elif data == "wm_auto":
        setup["auto_mode"] = not setup.get("auto_mode", False)
        await query.edit_message_text(setup_summary_text(setup), reply_markup=build_watermark_keyboard(setup))
        state = "✅ Enabled" if setup["auto_mode"] else "❌ Disabled"
        await query.answer(f"⏩ Auto mode {state}")

    elif data == "wm_next":
        current_size = setup.get("video_size", "original")
        size_info = VIDEO_SIZES[current_size]
        await query.edit_message_text(
            f"📐 **Video Size Selection**\n\n"
            f"Choose output size for **{setup['filename']}**:\n\n"
            f"Currently selected: `{size_info['label']}`\n\n"
            f"Select a size below 👇",
            reply_markup=build_size_keyboard(current_size)
        )
        await query.answer()

    elif data == "wm_start":
        active = len(user_tasks.get(user_id, {}))
        if active >= MAX_CONCURRENT:
            return await query.answer(
                f"❌ All {MAX_CONCURRENT} slots are busy! Cancel one first.", show_alert=True
            )
        mode = setup.get("mode", "record")
        init_text = "📥 **Starting download...**" if mode == "download" else "⚡ **Starting recording...**"
        answer_text = "📥 Download started!" if mode == "download" else "🎬 Recording started!"
        await query.edit_message_text(init_text)
        setup_copy = dict(setup)
        user_setup.pop(user_id, None)
        await query.answer(answer_text)
        asyncio.create_task(handle_record(client, query.message, setup_copy, user_id))

    elif data == "wm_cancel":
        mode = setup.get("mode", "record")
        user_setup.pop(user_id, None)
        label = "Download" if mode == "download" else "Recording"
        await query.edit_message_text(f"❌ **{label} setup cancelled.**")
        await query.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  Callback: Video size selection
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^sz_"))
async def size_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    setup = user_setup.get(user_id)
    if not setup:
        return await query.answer("❌ Session expired. Use /rec again.", show_alert=True)

    data = query.data

    if data == "sz_cancel":
        user_setup.pop(user_id, None)
        await query.edit_message_text("❌ **Recording setup cancelled.**")
        await query.answer()
        return

    if data == "sz_back":
        await query.edit_message_text(setup_summary_text(setup), reply_markup=build_watermark_keyboard(setup))
        await query.answer()
        return

    size_key = data[len("sz_"):]
    if size_key not in VIDEO_SIZES:
        return await query.answer("❌ Invalid size option.", show_alert=True)

    setup["video_size"] = size_key
    size_info = VIDEO_SIZES[size_key]

    active = len(user_tasks.get(user_id, {}))
    if active >= MAX_CONCURRENT:
        return await query.answer(
            f"❌ All {MAX_CONCURRENT} slots are busy! Cancel one first.", show_alert=True
        )

    await query.edit_message_text(
        f"⚡ **Starting recording...**\n\n"
        f"📐 **Size:** `{size_info['label']}`\n"
        f"📁 **File:** `{setup['filename']}`"
    )
    setup_copy = dict(setup)
    user_setup.pop(user_id, None)
    await query.answer("🎬 Recording started!")
    asyncio.create_task(handle_record(client, query.message, setup_copy, user_id))


# ─────────────────────────────────────────────────────────────────────────────
#  Watermark text input
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.text & filters.user(config.AUTH_USERS) & ~filters.command(
    ["start", "help", "rec", "cancel", "status", "download", "ott_download",
     "compress", "screenshot", "cookies_add", "cookies_status", "del_cookies", "alive"]
))
async def text_handler(client, message: Message):
    user_id = message.from_user.id
    setup = user_setup.get(user_id)
    if setup and setup.get("awaiting_wm_text"):
        new_text = message.text.strip()
        setup["watermark_text"] = new_text
        setup["awaiting_wm_text"] = False
        await message.reply_text(
            f"✅ Watermark text set to: **`{new_text}`**\n\n"
            f"Now tap **⚡ START RECORDING ⚡** in the setup message."
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Inline button callbacks: help / plan
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^help$"))
async def cb_help(client, query: CallbackQuery):
    await query.answer()
    await query.message.reply_text("📖 Use /help to see all commands.", disable_web_page_preview=True)

@app.on_callback_query(filters.regex("^plan$"))
async def cb_plan(client, query: CallbackQuery):
    await query.answer("💠 Contact @LS_Owner_bot for plans.", show_alert=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Upload progress callback
# ─────────────────────────────────────────────────────────────────────────────

async def progress_for_pyrogram(current, total, ref_message, start, msg, save_dir=None, was_cancelled=False, job_id=""):
    now = time.time()
    diff = max(now - start, 1)
    percentage = current * 100 / total
    speed = current / diff
    uploaded_mb = current / (1024 * 1024)
    total_mb = total / (1024 * 1024)
    speed_mb = speed / (1024 * 1024)
    bar = "■" * int(15 * percentage // 100) + "□" * (15 - int(15 * percentage // 100))
    update_points = {0, 10, 25, 50, 75, 90, 95, 99, 100}

    if int(percentage) in update_points or current == total:
        eta = TimeFormatter(int((total - current) / speed * 1000)) if speed > 0 else "00:00:00"
        n = slot_number(job_id) if job_id else 1
        emoji = SLOT_EMOJI[n - 1] if n <= 3 else "📤"
        prefix = f"{emoji} **Uploading {'Partial ' if was_cancelled else ''}Recording**"
        try:
            await msg.edit_text(
                f"{prefix}\n"
                f"`[{bar}]` {percentage:.1f}%\n"
                f"📊 `{uploaded_mb:.1f} / {total_mb:.1f} MB`\n"
                f"⚡ `{speed_mb:.1f} MB/s`  ⏳ `{eta}`"
            )
        except Exception:
            pass

        if current == total:
            done = f"{'✅ Partial Recording Sent!' if was_cancelled else '✅ Upload Completed!'}"
            try:
                await msg.edit_text(f"{done}\n🗑️ Cleaning up temporary files...")
                await asyncio.sleep(2)
                await msg.edit_text(f"{done}\n🗑️ Done!")
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
#  Core recording logic
# ─────────────────────────────────────────────────────────────────────────────

async def handle_record(client: Client, ref_message: Message, setup: dict, user_id: int):
    job_id = next_job_id(user_id)
    if job_id is None:
        await ref_message.reply_text(f"❌ All {MAX_CONCURRENT} recording slots are busy!")
        return

    job_key = make_job_key(user_id, job_id)
    n = slot_number(job_id)
    emoji = SLOT_EMOJI[n - 1]

    mode          = setup.get("mode", "record")
    url           = setup["url"]
    timestamp     = setup.get("timestamp")
    raw_filename  = setup["filename"]
    tracks        = setup.get("tracks", [])
    selected_tracks = setup.get("selected_tracks", set())
    watermark_pos = setup.get("watermark_pos")
    watermark_text = setup.get("watermark_text", config.DEFAULT_FILENAME)
    auto_mode     = setup.get("auto_mode", False) if mode == "record" else False

    is_download = (mode == "download")
    action_label = "Downloading" if is_download else "Recording"

    filename = f"{raw_filename}.mkv"
    save_dir = join(config.DOWNLOAD_DIRECTORY, f"{int(time.time())}_{job_id}")
    os.makedirs(save_dir, exist_ok=True)
    video_path = join(save_dir, filename)

    msg = await ref_message.reply_text(
        f"{emoji} **Slot {n} — Initializing {action_label.lower()}...**\n📁 `{raw_filename}`"
    )

    try:
        # ── Register job ─────────────────────────────────────────────────────
        user_tasks.setdefault(user_id, {})[job_id] = time.time()
        duration = time_to_seconds(timestamp) if timestamp else 0
        user_status.setdefault(user_id, {})[job_id] = {
            "id": int(time.time()),
            "filename": raw_filename,
            "target": timestamp or "∞",
            "progress": "00:00:00",
            "save_dir": save_dir,
            "mode": mode,
        }

        recording_start = time.time()

        # ── Stream map args ───────────────────────────────────────────────────
        # Always use -map 0:V? (capital V = video only, no data/attachments)
        # + explicit audio maps. The ? suffix makes each map optional so
        # FFmpeg won't crash if a stream is missing. This avoids the MKV
        # "Only audio, video, and subtitles are supported" error caused by
        # -map 0 selecting data streams.
        if tracks and selected_tracks:
            video_map = "-map 0:V?"
            audio_maps = " ".join(f"-map 0:a:{t['index']}?" for t in tracks if t["index"] in selected_tracks)
        else:
            video_map = "-map 0:V?"
            audio_maps = "-map 0:a?"

        meta_args = build_metadata_args(tracks, selected_tracks, config.CHANNEL_NAME)

        # ── Video size filter ─────────────────────────────────────────────────
        video_size_key = setup.get("video_size", "original")
        size_vf = VIDEO_SIZES.get(video_size_key, VIDEO_SIZES["original"])["vf"]

        # ── Watermark filter ─────────────────────────────────────────────────
        filters_chain = []
        if size_vf:
            filters_chain.append(size_vf)
        if watermark_pos and watermark_text:
            x, y = WM_POSITIONS[watermark_pos]
            safe_text = watermark_text.replace("'", "\\'").replace(":", "\\:")
            filters_chain.append(
                f"drawtext=text='{safe_text}':"
                f"fontsize=28:fontcolor=white@0.85:"
                f"x={x}:y={y}:box=1:boxcolor=black@0.45:boxborderw=6"
            )

        if filters_chain:
            vf = f'-vf "{",".join(filters_chain)}"'
            video_codec = "-c:v libx264 -preset veryfast -crf 22"
        else:
            vf = ""
            video_codec = "-c:v copy"

        # ── Progress tracker ─────────────────────────────────────────────────
        async def update_progress():
            while (
                user_id in user_tasks and
                job_id in user_tasks.get(user_id, {}) and
                job_key not in cancelled_jobs
            ):
                elapsed = time.time() - recording_start
                prog = TimeFormatter(int(elapsed * 1000))
                if job_id in user_status.get(user_id, {}):
                    user_status[user_id][job_id]["progress"] = prog
                speed_mb = random.uniform(2.0, 8.0)
                try:
                    if is_download:
                        # For download, no total duration known — show elapsed only
                        await msg.edit_text(
                            f"{emoji} **Slot {n} — Downloading**\n"
                            f"📁 `{raw_filename}`\n"
                            f"⏱️ Elapsed: `{prog}`\n"
                            f"⚡ `{speed_mb:.1f} MB/s`\n\n"
                            f"🛑 /cancel to stop"
                        )
                    else:
                        pct = min((elapsed / duration) * 100, 100) if duration > 0 else 0
                        eta = ((duration - elapsed) / (pct / 100)) if pct > 0 else 0
                        bar = "♥️" * int(20 * pct // 100) + "░" * (20 - int(20 * pct // 100))
                        await msg.edit_text(
                            f"{emoji} **Slot {n} — Recording**\n"
                            f"📁 `{raw_filename}`\n"
                            f"`[{bar}]` {pct:.1f}%\n"
                            f"⏱️ `{prog}` / `{TimeFormatter(duration * 1000)}`\n"
                            f"⚡ `{speed_mb:.1f} MB/s`  ⏳ `{TimeFormatter(int(eta * 1000))}`\n\n"
                            f"🛑 /cancel to stop"
                        )
                except Exception:
                    pass
                await asyncio.sleep(5)

        prog_task = asyncio.create_task(update_progress())
        progress_tasks.setdefault(user_id, {})[job_id] = prog_task

        # ── AUTO MODE ────────────────────────────────────────────────────────
        if auto_mode:
            await msg.edit_text(f"{emoji} **Slot {n} — Auto Mode: Recording first 1 min...**")
            part1 = join(save_dir, "part1.mkv")
            part2 = join(save_dir, "part2.mkv")
            concat_list = join(save_dir, "concat.txt")

            cmd1 = (
                f'ffmpeg -y {http_opts(url)} -probesize 10000000 -analyzeduration 15000000 '
                f'-i "{url}" {video_map} {audio_maps} {vf} '
                f'{video_codec} -c:a copy {meta_args} -t 00:01:00 "{part1}"'
            )
            proc1 = await asyncio.create_subprocess_exec(
                *shlex.split(cmd1),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            user_ffmpeg_pids.setdefault(user_id, {})[job_id] = proc1.pid
            await proc1.communicate()
            user_ffmpeg_pids.get(user_id, {}).pop(job_id, None)

            if job_key not in cancelled_jobs:
                seek_to = max(duration - 60, 61)
                await msg.edit_text(f"{emoji} **Slot {n} — Auto Mode: Recording last 1 min...**")
                cmd2 = (
                    f'ffmpeg -y {http_opts(url)} -probesize 10000000 -analyzeduration 15000000 '
                    f'-ss {seek_to} -i "{url}" {video_map} {audio_maps} {vf} '
                    f'{video_codec} -c:a copy {meta_args} -t 00:01:00 "{part2}"'
                )
                proc2 = await asyncio.create_subprocess_exec(
                    *shlex.split(cmd2),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                user_ffmpeg_pids.setdefault(user_id, {})[job_id] = proc2.pid
                await proc2.communicate()
                user_ffmpeg_pids.get(user_id, {}).pop(job_id, None)

                await msg.edit_text(f"{emoji} **Slot {n} — Joining parts...**")
                with open(concat_list, "w") as f:
                    f.write(f"file '{part1}'\n")
                    if os.path.exists(part2) and os.path.getsize(part2) > 0:
                        f.write(f"file '{part2}'\n")
                rc, _, _ = await runcmd(
                    f'ffmpeg -y -f concat -safe 0 -i "{concat_list}" -c copy "{video_path}"'
                )
                if rc != 0 or not os.path.exists(video_path):
                    video_path = part1

        else:
            # ── NORMAL RECORDING / DOWNLOAD ───────────────────────────────────
            time_arg = f"-t {timestamp}" if timestamp else ""
            ffmpeg_cmd = (
                f'ffmpeg -y {http_opts(url)} -probesize 10000000 -analyzeduration 15000000 '
                f'-i "{url}" {video_map} {audio_maps} {vf} '
                f'{video_codec} -c:a copy {meta_args} {time_arg} "{video_path}"'
            )
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(ffmpeg_cmd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            user_ffmpeg_pids.setdefault(user_id, {})[job_id] = proc.pid
            LOG.info(f"FFmpeg PID {proc.pid} | user {user_id} | {job_id}")
            _, stderr_bytes = await proc.communicate()
            user_ffmpeg_pids.get(user_id, {}).pop(job_id, None)

            was_cancelled = job_key in cancelled_jobs
            if proc.returncode != 0 and not was_cancelled:
                raise Exception(f"FFmpeg Error:\n{stderr_bytes.decode()[-2000:]}")

        # ── Stop progress task ────────────────────────────────────────────────
        if job_id in progress_tasks.get(user_id, {}):
            progress_tasks[user_id][job_id].cancel()
            del progress_tasks[user_id][job_id]

        was_cancelled = job_key in cancelled_jobs

        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            if was_cancelled:
                await msg.edit_text(f"{emoji} **Slot {n} — Cancelled. No video {action_label.lower()}.**")
                return
            raise Exception("Video file missing or empty.")

        # ── Thumbnail ─────────────────────────────────────────────────────────
        thumb_msg = await ref_message.reply_text(f"{emoji} **Slot {n} — Generating thumbnail...**")
        dur = await get_duration_ffmpeg(video_path) or (time_to_seconds(timestamp) if timestamp else 0)

        fixed_path = join(save_dir, f"fixed_{filename}")
        rc, _, _ = await runcmd(
            f'ffmpeg -y -i "{video_path}" -map 0 -c copy '
            f'-metadata creation_time="{time.strftime("%Y-%m-%dT%H:%M:%S")}" "{fixed_path}"'
        )
        if rc == 0:
            os.replace(fixed_path, video_path)

        rand_sec = random.randint(5, max(dur - 5, 6))
        thumb_path = join(save_dir, "thumb.jpg")
        await runcmd(f'ffmpeg -y -ss {rand_sec} -i "{video_path}" -vframes 1 -q:v 2 "{thumb_path}"')
        await thumb_msg.delete()

        # ── Caption ───────────────────────────────────────────────────────────
        sel_labels = [t["label"] for t in tracks if t["index"] in selected_tracks] or ["All"]
        wm_desc = "OFF" if not watermark_pos else f"{WM_LABEL.get(watermark_pos)} → {watermark_text}"

        size_label = VIDEO_SIZES.get(video_size_key, VIDEO_SIZES["original"])["label"]

        if is_download:
            status_line = "⚠️ _Partial download (cancelled)_" if was_cancelled else "✅ _Downloaded successfully!_"
            caption = (
                f"{emoji} **{raw_filename}**\n\n"
                f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
                f"🎵 **Audio:** `{', '.join(sel_labels)}`\n"
                f"🖼 **Watermark:** `{wm_desc}`\n"
                f"📁 **Format:** MKV\n\n"
                f"{status_line}"
            )
        else:
            auto_desc = "✅ First+Last 1min" if auto_mode else "❌"
            status_line = "⚠️ _Partial recording (cancelled)_" if was_cancelled else "✅ _Recorded successfully!_"
            caption = (
                f"{emoji} **{raw_filename}**\n\n"
                f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
                f"🎵 **Audio:** `{', '.join(sel_labels)}`\n"
                f"🖼 **Watermark:** `{wm_desc}`\n"
                f"📐 **Size:** `{size_label}`\n"
                f"⏩ **Auto:** `{auto_desc}`\n"
                f"📁 **Format:** MKV\n\n"
                f"{status_line}"
            )

        start_time = time.time()
        await ref_message.reply_video(
            video=video_path,
            caption=caption,
            duration=dur,
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            progress=progress_for_pyrogram,
            progress_args=(ref_message, start_time, msg, save_dir, was_cancelled, job_id)
        )

        shutil.rmtree(save_dir, ignore_errors=True)

    except Exception as e:
        LOG.error(f"handle_record [{job_id}] error: {e}")
        if job_key not in cancelled_jobs:
            try:
                await msg.edit(f"{emoji} **Slot {n} — Recording Failed!**\n\n`{str(e)[:3000]}`")
            except Exception:
                pass
        shutil.rmtree(save_dir, ignore_errors=True)

    finally:
        user_tasks.get(user_id, {}).pop(job_id, None)
        user_status.get(user_id, {}).pop(job_id, None)
        user_ffmpeg_pids.get(user_id, {}).pop(job_id, None)
        progress_tasks.get(user_id, {}).pop(job_id, None)
        cancelled_jobs.discard(job_key)
        # Clean empty dicts
        for d in [user_tasks, user_status, user_ffmpeg_pids, progress_tasks]:
            if user_id in d and not d[user_id]:
                del d[user_id]


# ─────────────────────────────────────────────────────────────────────────────
#  Cookies helpers
# ─────────────────────────────────────────────────────────────────────────────

def cookies_dir() -> str:
    path = join(config.DOWNLOAD_DIRECTORY, "cookies")
    os.makedirs(path, exist_ok=True)
    return path

def cookies_path(user_id: int) -> str:
    return join(cookies_dir(), f"{user_id}_cookies.txt")

def has_cookies(user_id: int) -> bool:
    return os.path.exists(cookies_path(user_id))


# ─────────────────────────────────────────────────────────────────────────────
#  /cookies_add — Upload cookies.txt file
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("cookies_add") & filters.user(config.AUTH_USERS))
async def cookies_add_cmd(client: Client, message: Message):
    await message.reply_text(
        "🍪 **Add Cookies**\n\n"
        "📎 **Reply to this message with your `cookies.txt` file.**\n\n"
        "📝 How to get cookies:\n"
        "• Install **EditThisCookie** or **Get cookies.txt** browser extension\n"
        "• Login to OTT platform (Hotstar, Netflix, etc.)\n"
        "• Export cookies as `cookies.txt` (Netscape format)\n"
        "• Send that file here as a reply\n\n"
        "⚠️ _Cookies are stored privately per user._"
    )
    # Set awaiting flag in user_setup area
    user_setup.setdefault(message.from_user.id, {})["awaiting_cookies"] = True


@app.on_message(filters.document & filters.user(config.AUTH_USERS))
async def document_handler(client: Client, message: Message):
    user_id = message.from_user.id
    setup = user_setup.get(user_id, {})

    if not setup.get("awaiting_cookies"):
        return

    doc = message.document
    if not (doc.file_name or "").lower().endswith(".txt"):
        return await message.reply_text("❌ Please send a `.txt` file (cookies.txt).")

    msg = await message.reply_text("⏳ **Saving cookies...**")
    try:
        dest = cookies_path(user_id)
        await client.download_media(message, file_name=dest)
        setup.pop("awaiting_cookies", None)
        size_kb = os.path.getsize(dest) / 1024
        await msg.edit_text(
            f"✅ **Cookies saved!**\n\n"
            f"📄 **File:** `cookies.txt`\n"
            f"📦 **Size:** `{size_kb:.1f} KB`\n\n"
            f"Now use /download with OTT URLs — cookies will be applied automatically. 🍪"
        )
    except Exception as e:
        LOG.error(f"cookies_add error: {e}")
        await msg.edit_text(f"❌ **Failed to save cookies:** `{e}`")


# ─────────────────────────────────────────────────────────────────────────────
#  /cookies_status — Show cookie file info
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("cookies_status") & filters.user(config.AUTH_USERS))
async def cookies_status_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    path = cookies_path(user_id)
    if not os.path.exists(path):
        return await message.reply_text(
            "❌ **No cookies found!**\n\nUse /cookies_add to upload your cookies.txt file."
        )
    size_kb = os.path.getsize(path) / 1024
    created = datetime.fromtimestamp(os.path.getctime(path), tz=tz).strftime("%d-%m-%Y %I:%M:%S %p")
    modified = datetime.fromtimestamp(os.path.getmtime(path), tz=tz).strftime("%d-%m-%Y %I:%M:%S %p")
    # Count lines (each cookie = 1 line roughly)
    with open(path, "r", errors="ignore") as f:
        lines = [l for l in f.readlines() if l.strip() and not l.startswith("#")]
    await message.reply_text(
        f"🍪 **Cookies Status**\n\n"
        f"✅ **Status:** Active\n"
        f"📦 **Size:** `{size_kb:.1f} KB`\n"
        f"🔢 **Cookie entries:** `{len(lines)}`\n"
        f"🕒 **Uploaded:** `{created}`\n"
        f"🔄 **Modified:** `{modified}`\n\n"
        f"🗑 Use /del_cookies to remove"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /del_cookies — Delete cookie file
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("del_cookies") & filters.user(config.AUTH_USERS))
async def del_cookies_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    path = cookies_path(user_id)
    if not os.path.exists(path):
        return await message.reply_text("❌ **No cookies to delete!**")
    os.remove(path)
    await message.reply_text("🗑 **Cookies deleted successfully!**\n\nUse /cookies_add to upload new ones.")


# ─────────────────────────────────────────────────────────────────────────────
#  yt-dlp helper (OTT / YouTube download)
# ─────────────────────────────────────────────────────────────────────────────

async def ytdlp_download(url: str, output_path: str, cookies_file: Optional[str] = None) -> Tuple[int, str, str]:
    cmd_parts = [
        "yt-dlp",
        "--no-playlist",
        "--merge-output-format", "mkv",
        "--impersonate", "chrome",
        "-o", output_path,
    ]
    if cookies_file and os.path.exists(cookies_file):
        cmd_parts += ["--cookies", cookies_file]
    cmd_parts.append(url)

    process = await asyncio.create_subprocess_exec(
        *cmd_parts,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(), stderr.decode()


# ─────────────────────────────────────────────────────────────────────────────
#  Override /download to use yt-dlp for OTT/YouTube
# ─────────────────────────────────────────────────────────────────────────────
#  The existing /download → audio_selection → watermark flow uses ffmpeg.
#  For OTT/YouTube URLs we bypass that flow and use yt-dlp directly.
#  Heuristic: if URL contains youtube/youtu.be/hotstar/netflix/primevideo/jiocinema etc.

OTT_DOMAINS = (
    "youtube.com", "youtu.be",
    "Jiohotstar.com", "disneyplus.com",
    "netflix.com", "primevideo.com",
    "zee5.com", "sonyliv.com",
    "jiocinema.com", "voot.com",
    "mxplayer.in", "erosnow.com",
    "altbalaji.com", "ullu.app",
)

def is_ott_url(url: str) -> bool:
    url_lower = url.lower()
    return any(d in url_lower for d in OTT_DOMAINS)


@app.on_message(filters.command("ott_download") & filters.user(config.AUTH_USERS))
async def ott_download_cmd(client: Client, message: Message):
    """
    /ott_download URL [filename]
    Direct OTT/YouTube download using yt-dlp with user cookies.
    """
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "📌 **Usage:**\n"
            "```\n/ott_download https://youtube.com/... MyFilename\n```\n\n"
            "🍪 Add cookies first with /cookies_add for OTT sites."
        )

    user_id = message.from_user.id
    active = len(user_tasks.get(user_id, {}))
    if active >= MAX_CONCURRENT:
        return await message.reply_text(
            f"❌ **All {MAX_CONCURRENT} slots are busy!**\n📊 /status  |  🛑 /cancel"
        )

    job_id = next_job_id(user_id)
    if not job_id:
        return await message.reply_text(f"❌ All {MAX_CONCURRENT} slots full!")

    params = " ".join(message.command[1:])
    parts = params.split(" ", 1)
    url = parts[0]
    raw_filename = parts[1].strip() if len(parts) > 1 else config.DEFAULT_FILENAME

    n = slot_number(job_id)
    emoji = SLOT_EMOJI[n - 1]
    job_key = make_job_key(user_id, job_id)

    save_dir = join(config.DOWNLOAD_DIRECTORY, f"{int(time.time())}_{job_id}")
    os.makedirs(save_dir, exist_ok=True)
    output_tmpl = join(save_dir, f"{raw_filename}.%(ext)s")

    msg = await message.reply_text(
        f"{emoji} **Slot {n} — Starting OTT Download...**\n"
        f"📁 `{raw_filename}`\n"
        f"🍪 Cookies: `{'✅ Found' if has_cookies(user_id) else '❌ None (may fail for OTT)'}`"
    )

    user_tasks.setdefault(user_id, {})[job_id] = time.time()
    user_status.setdefault(user_id, {})[job_id] = {
        "id": int(time.time()),
        "filename": raw_filename,
        "target": "∞",
        "progress": "00:00:00",
        "save_dir": save_dir,
        "mode": "ott",
    }

    dl_start = time.time()

    async def ott_progress():
        while user_id in user_tasks and job_id in user_tasks.get(user_id, {}) and job_key not in cancelled_jobs:
            elapsed = time.time() - dl_start
            prog = TimeFormatter(int(elapsed * 1000))
            if job_id in user_status.get(user_id, {}):
                user_status[user_id][job_id]["progress"] = prog
            try:
                await msg.edit_text(
                    f"{emoji} **Slot {n} — Downloading (OTT/YT)**\n"
                    f"📁 `{raw_filename}`\n"
                    f"⏱️ Elapsed: `{prog}`\n\n"
                    f"🛑 /cancel to stop"
                )
            except Exception:
                pass
            await asyncio.sleep(5)

    prog_task = asyncio.create_task(ott_progress())
    progress_tasks.setdefault(user_id, {})[job_id] = prog_task

    try:
        cookie_file = cookies_path(user_id) if has_cookies(user_id) else None
        retcode, out, err = await ytdlp_download(url, output_tmpl, cookie_file)

        if job_id in progress_tasks.get(user_id, {}):
            progress_tasks[user_id][job_id].cancel()

        was_cancelled = job_key in cancelled_jobs
        if retcode != 0 and not was_cancelled:
            raise Exception(f"yt-dlp error:\n{err[-2000:]}")

        # Find downloaded file
        video_path = None
        for f in os.listdir(save_dir):
            if f.startswith(raw_filename):
                video_path = join(save_dir, f)
                break

        if not video_path or not os.path.exists(video_path):
            raise Exception("Downloaded file not found.")

        # Thumbnail
        thumb_msg = await message.reply_text(f"{emoji} **Slot {n} — Generating thumbnail...**")
        dur = await get_duration_ffmpeg(video_path)
        rand_sec = random.randint(5, max(dur - 5, 6)) if dur > 10 else 1
        thumb_path = join(save_dir, "thumb.jpg")
        await runcmd(f'ffmpeg -y -ss {rand_sec} -i "{video_path}" -vframes 1 -q:v 2 "{thumb_path}"')
        await thumb_msg.delete()

        caption = (
            f"{emoji} **{raw_filename}**\n\n"
            f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
            f"📥 **Source:** OTT/YouTube\n"
            f"🍪 **Cookies:** `{'✅ Used' if cookie_file else '❌ None'}`\n"
            f"📁 **Format:** MKV\n\n"
            f"{'⚠️ _Partial (cancelled)_' if was_cancelled else '✅ _Downloaded successfully!_'}"
        )

        start_time = time.time()
        await message.reply_video(
            video=video_path,
            caption=caption,
            duration=dur,
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            progress=progress_for_pyrogram,
            progress_args=(message, start_time, msg, save_dir, was_cancelled, job_id)
        )
        shutil.rmtree(save_dir, ignore_errors=True)

    except Exception as e:
        LOG.error(f"ott_download error [{job_id}]: {e}")
        if job_key not in cancelled_jobs:
            try:
                await msg.edit(f"{emoji} **Slot {n} — Download Failed!**\n\n`{str(e)[:3000]}`")
            except Exception:
                pass
        shutil.rmtree(save_dir, ignore_errors=True)
    finally:
        user_tasks.get(user_id, {}).pop(job_id, None)
        user_status.get(user_id, {}).pop(job_id, None)
        user_ffmpeg_pids.get(user_id, {}).pop(job_id, None)
        progress_tasks.get(user_id, {}).pop(job_id, None)
        cancelled_jobs.discard(job_key)
        for d in [user_tasks, user_status, user_ffmpeg_pids, progress_tasks]:
            if user_id in d and not d[user_id]:
                del d[user_id]


# ─────────────────────────────────────────────────────────────────────────────
#  /compress — Compress a replied video
# ─────────────────────────────────────────────────────────────────────────────

COMPRESS_PRESETS = {
    "high":   ("-c:v libx264 -crf 23 -preset fast -c:a aac -b:a 128k", "High (good quality, moderate size)"),
    "medium": ("-c:v libx264 -crf 28 -preset fast -c:a aac -b:a 96k",  "Medium (balanced)"),
    "low":    ("-c:v libx264 -crf 32 -preset fast -c:a aac -b:a 64k",  "Low (small size, lower quality)"),
}

def build_compress_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 High Quality",   callback_data="cmp_high")],
        [InlineKeyboardButton("🟡 Medium Quality", callback_data="cmp_medium")],
        [InlineKeyboardButton("🔴 Low (Smallest)", callback_data="cmp_low")],
        [InlineKeyboardButton("❌ Cancel",          callback_data="cmp_cancel")],
    ])

# Store pending compress requests: {user_id: message_id_of_video_reply}
compress_pending: Dict[int, int] = {}

def get_video_media(msg):
    """Return the video/document media object from a message, or None."""
    if not msg:
        return None
    return msg.video or msg.document or None


@app.on_message(filters.command("compress") & filters.user(config.AUTH_USERS))
async def compress_cmd(client: Client, message: Message):
    if not message.reply_to_message or not get_video_media(message.reply_to_message):
        return await message.reply_text(
            "❌ **Reply to a video message with /compress**\n\n"
            "💡 Example: Reply to a video and send `/compress`"
        )
    compress_pending[message.from_user.id] = message.reply_to_message.id
    await message.reply_text(
        "🗜 **Video Compress**\n\nSelect compression quality:",
        reply_markup=build_compress_keyboard()
    )


@app.on_callback_query(filters.regex(r"^cmp_"))
async def compress_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data

    if data == "cmp_cancel":
        compress_pending.pop(user_id, None)
        await query.edit_message_text("❌ **Compression cancelled.**")
        return await query.answer()

    preset = data[len("cmp_"):]
    if preset not in COMPRESS_PRESETS:
        return await query.answer("Invalid preset.", show_alert=True)

    video_msg_id = compress_pending.pop(user_id, None)
    if not video_msg_id:
        return await query.answer("Session expired. Reply to video again.", show_alert=True)

    ffmpeg_args, quality_desc = COMPRESS_PRESETS[preset]
    await query.edit_message_text(f"⏳ **Compressing... ({quality_desc})**\nPlease wait...")
    await query.answer()

    active = len(user_tasks.get(user_id, {}))
    if active >= MAX_CONCURRENT:
        await query.message.edit_text(f"❌ All {MAX_CONCURRENT} slots busy. Cancel one first.")
        return

    job_id = next_job_id(user_id)
    if not job_id:
        return
    job_key = make_job_key(user_id, job_id)
    n = slot_number(job_id)
    emoji_slot = SLOT_EMOJI[n - 1]

    save_dir = join(config.DOWNLOAD_DIRECTORY, f"{int(time.time())}_{job_id}_compress")
    os.makedirs(save_dir, exist_ok=True)

    user_tasks.setdefault(user_id, {})[job_id] = time.time()
    user_status.setdefault(user_id, {})[job_id] = {
        "id": int(time.time()),
        "filename": "Compressed Video",
        "target": "∞",
        "progress": "00:00:00",
        "save_dir": save_dir,
        "mode": "compress",
    }

    async def do_compress():
        try:
            msg = query.message
            # Download original video
            await msg.edit_text(f"{emoji_slot} **Slot {n} — Downloading original video...**")
            orig_path = join(save_dir, "original.mkv")
            # Get the video message from chat
            chat_id = query.message.chat.id
            video_message = await client.get_messages(chat_id, video_msg_id)
            if not video_message or not get_video_media(video_message):
                raise Exception("Original video message not found.")
            await client.download_media(video_message, file_name=orig_path)

            if not os.path.exists(orig_path) or os.path.getsize(orig_path) == 0:
                raise Exception("Download failed or file is empty.")

            orig_size_mb = os.path.getsize(orig_path) / (1024 * 1024)
            await msg.edit_text(
                f"{emoji_slot} **Slot {n} — Compressing...**\n"
                f"📦 Original: `{orig_size_mb:.1f} MB`\n"
                f"🎛 Quality: `{quality_desc}`"
            )

            out_path = join(save_dir, "compressed.mkv")
            compress_cmd_str = f'ffmpeg -y -i "{orig_path}" {ffmpeg_args} "{out_path}"'
            rc, _, err = await runcmd(compress_cmd_str)
            if rc != 0:
                raise Exception(f"FFmpeg compress error:\n{err[-1500:]}")

            new_size_mb = os.path.getsize(out_path) / (1024 * 1024)
            reduction = max(0, (1 - new_size_mb / orig_size_mb) * 100)

            # Thumbnail
            dur = await get_duration_ffmpeg(out_path)
            rand_sec = random.randint(5, max(dur - 5, 6)) if dur > 10 else 1
            thumb_path = join(save_dir, "thumb.jpg")
            await runcmd(f'ffmpeg -y -ss {rand_sec} -i "{out_path}" -vframes 1 -q:v 2 "{thumb_path}"')

            caption = (
                f"🗜 **Compressed Video**\n\n"
                f"📦 **Original:** `{orig_size_mb:.1f} MB`\n"
                f"📉 **Compressed:** `{new_size_mb:.1f} MB`\n"
                f"✂️ **Reduction:** `{reduction:.1f}%`\n"
                f"🎛 **Quality:** `{quality_desc}`\n\n"
                f"✅ _Compression completed!_"
            )
            start_time = time.time()
            await msg.reply_video(
                video=out_path,
                caption=caption,
                duration=dur,
                thumb=thumb_path if os.path.exists(thumb_path) else None,
                progress=progress_for_pyrogram,
                progress_args=(msg, start_time, msg, save_dir, False, job_id)
            )
            shutil.rmtree(save_dir, ignore_errors=True)

        except Exception as e:
            LOG.error(f"compress error [{job_id}]: {e}")
            try:
                await query.message.edit_text(f"{emoji_slot} **Compression Failed!**\n\n`{str(e)[:2000]}`")
            except Exception:
                pass
            shutil.rmtree(save_dir, ignore_errors=True)
        finally:
            user_tasks.get(user_id, {}).pop(job_id, None)
            user_status.get(user_id, {}).pop(job_id, None)
            cancelled_jobs.discard(job_key)
            for d in [user_tasks, user_status]:
                if user_id in d and not d[user_id]:
                    del d[user_id]

    asyncio.create_task(do_compress())


# ─────────────────────────────────────────────────────────────────────────────
#  /screenshot [count] — Extract screenshots from a replied video
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("screenshot") & filters.user(config.AUTH_USERS))
async def screenshot_cmd(client: Client, message: Message):
    if not message.reply_to_message or not get_video_media(message.reply_to_message):
        return await message.reply_text(
            "❌ **Reply to a video message with /screenshot [count]**\n\n"
            "💡 Example:\n"
            "`/screenshot` → 1 screenshot\n"
            "`/screenshot 10` → 10 screenshots\n"
            "`/screenshot 30` → 30 screenshots (max)"
        )

    # Parse count
    try:
        count = int(message.command[1]) if len(message.command) > 1 else 1
        count = max(1, min(count, 30))
    except (ValueError, IndexError):
        count = 1

    user_id = message.from_user.id
    video_message = message.reply_to_message

    msg = await message.reply_text(
        f"📸 **Extracting {count} screenshot{'s' if count > 1 else ''}...**\nPlease wait."
    )

    save_dir = join(config.DOWNLOAD_DIRECTORY, f"{int(time.time())}_ss_{user_id}")
    os.makedirs(save_dir, exist_ok=True)

    try:
        # Download video
        await msg.edit_text("📥 **Downloading video...**")
        orig_path = join(save_dir, "video.mkv")
        await client.download_media(video_message, file_name=orig_path)

        if not os.path.exists(orig_path) or os.path.getsize(orig_path) == 0:
            raise Exception("Video download failed or file is empty.")

        dur = await get_duration_ffmpeg(orig_path)
        if dur < 2:
            raise Exception("Video too short for screenshots.")

        await msg.edit_text(f"📸 **Extracting {count} screenshot{'s' if count > 1 else ''}...**")

        # Calculate evenly spaced timestamps
        # Avoid first and last 2 seconds
        usable_dur = max(dur - 4, 1)
        if count == 1:
            timestamps = [dur // 2]
        else:
            step = usable_dur / (count - 1) if count > 1 else usable_dur
            timestamps = [2 + int(i * step) for i in range(count)]

        # Extract screenshots
        screenshot_paths = []
        for i, ts in enumerate(timestamps):
            ss_path = join(save_dir, f"ss_{i + 1:02d}.jpg")
            rc, _, _ = await runcmd(
                f'ffmpeg -y -ss {ts} -i "{orig_path}" -vframes 1 -q:v 2 "{ss_path}"'
            )
            if rc == 0 and os.path.exists(ss_path) and os.path.getsize(ss_path) > 0:
                screenshot_paths.append(ss_path)

        if not screenshot_paths:
            raise Exception("No screenshots could be extracted.")

        await msg.edit_text(f"📤 **Uploading {len(screenshot_paths)} screenshot{'s' if len(screenshot_paths) > 1 else ''}...**")

        # Send as media group (max 10 per group)
        from pyrogram.types import InputMediaPhoto
        caption_main = (
            f"📸 **{len(screenshot_paths)} Screenshot{'s' if len(screenshot_paths) > 1 else ''}**\n"
            f"⏱ **Video Duration:** `{TimeFormatter(dur * 1000)}`"
        )

        for batch_start in range(0, len(screenshot_paths), 10):
            batch = screenshot_paths[batch_start: batch_start + 10]
            media_group = []
            for idx, sp in enumerate(batch):
                cap = caption_main if (batch_start == 0 and idx == 0) else ""
                media_group.append(InputMediaPhoto(sp, caption=cap))
            await message.reply_media_group(media_group)

        await msg.edit_text(f"✅ **{len(screenshot_paths)} screenshot{'s' if len(screenshot_paths) > 1 else ''} sent!**")
        shutil.rmtree(save_dir, ignore_errors=True)

    except Exception as e:
        LOG.error(f"screenshot error: {e}")
        try:
            await msg.edit_text(f"❌ **Screenshot failed!**\n\n`{str(e)[:2000]}`")
        except Exception:
            pass
        shutil.rmtree(save_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🎬 Starting Video Recorder Bot...")
    print(f"⚡ Max concurrent recordings per user: {MAX_CONCURRENT}")
    print("✅ Bot is now running!")
    
    app.start()
    print("🤖 OTT Recorder Bot is Live with Auto-Crop, Compress & Custom SS!")
    idle()
