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
user_tasks = {}
user_status = {}
user_ffmpeg_pids = {}
progress_tasks = {}

# ─────────────────────────────────────────────────────────────────────────────
#  मिडलवेयर फ़िल्टर — यह चेक करेगा कि यूजर वेरीफाइड है या नहीं
# ─────────────────────────────────────────────────────────────────────────────
async def check_user_access(client: Client, message: Message) -> bool:
    user_id = message.from_user.id
    
    # ओनर, ऑथ यूजर या पहले से वेरीफाइड यूजर के लिए हमेशा खुला रहेगा
    if verify.is_verified(user_id, config.OWNER_ID, config.AUTH_USERS):
        return True
        
    # अगर वेरीफाइड नहीं है तो नया टोकन जनरेट करेगा
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
        f"बॉट का उपयोग करने के लिए नीचे दिए गए बटन पर जाकर वेरिफिकेशन पूरा करें। यह 4 घंटे तक काम करेगा:\n\n"
        f"🔗 **[यहाँ क्लिक करके वेरीफाई करें]({final_link})**",
        disable_web_page_preview=True
    )
    return False

def build_metadata_args(track_languages: list) -> list:
    channel_name = getattr(config, "CHANNEL_NAME", "@LittleSinghamChannel")
    args = []
    lang_codes = {
        "hindi": "hin", "telugu": "tel", "tamil": "tam",
        "kannada": "kan", "malayalam": "mal", "marathi": "mar",
        "bengali": "ben", "english": "eng", "punjabi": "pan",
        "gujarati": "guj", "odia": "ori", "urdu": "urd"
    }
    for idx, lang in enumerate(track_languages):
        lang_lower = lang.lower().strip()
        code = lang_codes.get(lang_lower, "und")
        title_str = f"{channel_name} {lang.capitalize()}"
        args.extend([
            f"-metadata:s:a:{idx}", f"title={title_str}",
            f"-metadata:s:a:{idx}", f"handler_name={title_str}",
            f"-metadata:s:a:{idx}", f"language={code}"
        ])
    return args

async def runcmd(cmd: List[str]) -> Tuple[int, str, str]:
    LOG.info(f"Running command: {shlex.join(cmd)}")
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await p.communicate()
    return p.returncode, stdout.decode().strip(), stderr.decode().strip()

def format_bytes(b: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} TB"

def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

async def safe_delete_message(msg: Message):
    try:
        await msg.delete()
    except Exception:
        pass

# ── Progress bar update loop ─────────────────────────────────────────────────
async def update_progress_loop(user_id: int, job_id: str, msg: Message):
    try:
        while True:
            await asyncio.sleep(5)
            status = user_status.get(user_id, {}).get(job_id)
            if not status:
                break
            
            elapsed = time.time() - user_tasks[user_id][job_id]
            elapsed_str = TimeFormatter(elapsed * 1000)
            
            p_text = f"⚙️ **Status:** `{status['id']}`\n"
            p_text += f"📦 **File:** `{status['filename']}`\n"
            p_text += f"🎯 **Target:** `{status['target']}`\n"
            p_text += f"⏱ **Elapsed:** `{elapsed_str}`\n"
            
            if status["id"] == "Recording":
                if os.path.exists(status["save_dir"]):
                    total_bytes = 0
                    for root, dirs, files in os.walk(status["save_dir"]):
                        for f in files:
                            total_bytes += os.path.getsize(join(root, f))
                    p_text += f"📁 **Size:** `{format_bytes(total_bytes)}`"
            else:
                p_text += f"📊 **Progress:** `{status['progress']}`"

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🛑 Stop Task", callback_data=f"stop_{job_id}")
            ]])
            try:
                await msg.edit_text(p_text, reply_markup=kb)
            except Exception:
                pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        LOG.error(f"progress loop error: {e}")

# ── Callback handler ─────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^stop_"))
async def stop_callback(client: Client, cb: CallbackQuery):
    user_id = cb.from_user.id
    job_id = cb.data.split("_", 1)[1]
    
    if user_id not in user_tasks or job_id not in user_tasks[user_id]:
        await cb.answer("❌ Task not found or already completed.", show_alert=True)
        return
        
    pid = user_ffmpeg_pids.get(user_id, {}).get(job_id)
    if pid:
        try:
            p = psutil.Process(pid)
            p.terminate()
            await cb.answer("🛑 Stopping ffmpeg process...")
        except Exception as e:
            await cb.answer(f"⚠️ Error stopping process: {e}", show_alert=True)
    else:
        if user_id in progress_tasks and job_id in progress_tasks[user_id]:
            progress_tasks[user_id][job_id].cancel()
        
        status = user_status.get(user_id, {}).get(job_id)
        if status and os.path.exists(status["save_dir"]):
            shutil.rmtree(status["save_dir"], ignore_errors=True)
            
        user_tasks[user_id].pop(job_id, None)
        user_status[user_id].pop(job_id, None)
        await cb.message.edit_text("🛑 **Task stopped manually by user.**")
        await cb.answer("Task removed.")

# ── /start command ───────────────────────────────────────────────────────────
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # अगर यूजर शॉर्टनर लिंक से पास होकर वापस बॉट में आया है
    if len(text.split()) > 1 and text.split()[1].startswith("verify_"):
        token = text.split()[1].replace("verify_", "")
        if verify.confirm_token(user_id, token):
            await message.reply_text("✅ **वेरिफिकेशन सफल रहा!**\nअब आप अगले 4 घंटे तक बॉट का इस्तेमाल कर सकते हैं।")
        else:
            await message.reply_text("❌ **वेरिफिकेशन封 हो गया!**\nलिंक एक्सपायर हो चुका है या गलत है। कृपया दोबारा कोशिश करें।")
        return

    await message.reply_text(
        "🎬 **Welcome to Live Recorder Bot!**\n\n"
        "💡 **Commands Available:**\n"
        "• `/rec` — Record a live stream\n"
        "• `/ott_download` — Download videos from links\n"
        "• `/compress` — Compress a video\n"
        "• `/screenshot` — Generate screenshots from a video\n"
        "• `/cookies_add` — Upload new cookies file\n\n"
        "⚠️ Non-auth users must complete verification when requested."
    )

# ── /cookies_add ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("cookies_add") & filters.private)
async def cookies_add_cmd(client: Client, message: Message):
    if not await check_user_access(client, message):
        return
    await message.reply_text("📂 **Please send your `cookies.txt` file now.**")

@app.on_message(filters.document & filters.private)
async def handle_document(client: Client, message: Message):
    if not await check_user_access(client, message):
        return
    if message.document.file_name == "cookies.txt":
        wp = os.getcwd()
        cp = join(wp, "cookies.txt")
        await message.download(file_name=cp)
        await message.reply_text("✅ **`cookies.txt` successfully updated!**")

# ── /rec (Live Recording Handler) ────────────────────────────────────────────
@app.on_message(filters.command("rec") & filters.private)
async def rec_cmd(client: Client, message: Message):
    if not await check_user_access(client, message):
        return
        
    user_id = message.from_user.id
    if len(user_tasks.get(user_id, {})) >= MAX_CONCURRENT:
        await message.reply_text(f"❌ You can only run up to {MAX_CONCURRENT} tasks concurrently.")
        return

    args = message.text.split(maxsplit=4)
    if len(args) < 4:
        em = (
            "❌ **Usage:** `/rec [url] [duration HH:MM:SS] [filename]`\n\n"
            "💡 **Optional Tracks Mode:**\n"
            "`/rec [url] [duration] [filename] hindi,telugu,tamil`\n"
            "*(comma separated, no spaces)*"
        )
        await message.reply_text(em)
        return

    stream_url = args[1]
    duration_str = args[2]
    filename_raw = args[3]
    tracks_input = args[4] if len(args) > 4 else None

    try:
        parts = list(map(int, duration_str.split(":")))
        if len(parts) == 3:
            duration_seconds = parts[0]*3600 + parts[1]*60 + parts[2]
        elif len(parts) == 2:
            duration_seconds = parts[0]*60 + parts[1]
        else:
            duration_seconds = int(parts[0])
    except Exception:
        await message.reply_text("❌ Invalid duration format. Use `HH:MM:SS` or `MM:SS` or total seconds.")
        return

    job_id = secrets_token() if 'secrets_token' in globals() else str(random.randint(100000, 999999))
    if user_id not in user_tasks:
        user_tasks[user_id] = {}
    if user_id not in user_status:
        user_status[user_id] = {}
    if user_id not in user_ffmpeg_pids:
        user_ffmpeg_pids[user_id] = {}

    user_tasks[user_id][job_id] = time.time()
    
    clean_name = "".join(c for c in filename_raw if c.isalnum() or c in "._-").strip()
    if not clean_name:
        clean_name = f"Live_{job_id}"
    out_filename = f"{clean_name}.mp4"
    
    save_dir = join(config.DOWNLOAD_DIRECTORY, f"rec_{user_id}_{job_id}")
    os.makedirs(save_dir, exist_ok=True)
    out_path = join(save_dir, out_filename)

    user_status[user_id][job_id] = {
        "id": "Recording",
        "filename": out_filename,
        "target": duration_str,
        "progress": "0%",
        "save_dir": save_dir
    }

    msg = await message.reply_text("🔴 **Preparing stream recording...**")
    
    ploop = asyncio.create_task(update_progress_loop(user_id, job_id, msg))
    if user_id not in progress_tasks:
        progress_tasks[user_id] = {}
    progress_tasks[user_id][job_id] = ploop

    asyncio.create_task(execute_rec(user_id, job_id, stream_url, duration_seconds, tracks_input, out_path, save_dir, msg))

async def execute_rec(user_id, job_id, stream_url, duration_seconds, tracks_input, out_path, save_dir, msg):
    try:
        cmd = ['ffmpeg', '-y', '-headers', 'User-Agent: Mozilla/5.0\r\n', '-i', stream_url, '-t', str(duration_seconds)]
        
        if tracks_input:
            langs = [t.strip().lower() for t in tracks_input.split(",") if t.strip()]
            cmd.extend(['-map', '0:v'])
            for i in range(len(langs)):
                cmd.extend(['-map', f'0:a:{i}?'])
            
            # फालतू डेटा ट्रैक्स हटाना
            cmd.extend(['-map', '-0:d'])
            
            cmd.extend([
                '-vf', 'scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2:black',
                '-c:v', 'libx264', '-preset', 'slow', '-b:v', '330k', '-c:a', 'aac', '-b:a', '48k'
            ])
            cmd.extend(build_metadata_args(langs))
        else:
            cmd.extend([
                '-map', '0:v', '-map', '0:a?', '-map', '-0:d',
                '-vf', 'scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2:black',
                '-c:v', 'libx264', '-preset', 'slow', '-b:v', '330k', '-c:a', 'aac', '-b:a', '48k'
            ])
            
        # टेलीग्राम के लिए आधा वीडियो अटकना फिक्स करने वाले झंडे
        cmd.extend(['-fflags', '+genpts+discardcorrupt', '-movflags', '+faststart', '-f', 'mp4', out_path])

        LOG.info(f"FFmpeg command: {shlex.join(cmd)}")
        p = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        
        user_ffmpeg_pids[user_id][job_id] = p.pid
        stdout, stderr = await p.communicate()
        
        user_ffmpeg_pids[user_id].pop(job_id, None)
        if user_id in progress_tasks and job_id in progress_tasks[user_id]:
            progress_tasks[user_id][job_id].cancel()

        if p.returncode != 0:
            err = stderr.decode().strip()
            LOG.error(f"ffmpeg error: {err}")
            await msg.edit_text(f"❌ **FFmpeg Error:**\n\n`{err[:2000]}`")
            shutil.rmtree(save_dir, ignore_errors=True)
            return

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            await msg.edit_text("❌ **Recording failed. Output file is empty.**")
            shutil.rmtree(save_dir, ignore_errors=True)
            return

        await msg.edit_text("⚡ **Uploading recorded file to Telegram...**")
        await app.send_video(
            chat_id=user_id,
            video=out_path,
            caption=f"🎬 **File Name:** `{os.path.basename(out_path)}`"
        )
        await safe_delete_message(msg)

    except Exception as e:
        LOG.error(f"execute_rec global error: {e}")
        try:
            await msg.edit_text(f"❌ **Recording Crashed:**\n\n`{str(e)}`")
        except Exception:
            pass
    finally:
        shutil.rmtree(save_dir, ignore_errors=True)
        user_tasks.get(user_id, {}).pop(job_id, None)
        user_status.get(user_id, {}).get(job_id, None)

# ── /ott_download (Yt-dlp + FFmpeg Downloader) ───────────────────────────────
@app.on_message(filters.command("ott_download") & filters.private)
async def ott_download_cmd(client: Client, message: Message):
    if not await check_user_access(client, message):
        return
        
    user_id = message.from_user.id
    if len(user_tasks.get(user_id, {})) >= MAX_CONCURRENT:
        await message.reply_text(f"❌ You can only run up to {MAX_CONCURRENT} tasks concurrently.")
        return

    args = message.text.split(maxsplit=3)
    if len(args) < 3:
        await message.reply_text("❌ **Usage:** `/ott_download [url] [filename]`")
        return

    url = args[1]
    filename_raw = args[2]

    job_id = str(random.randint(100000, 999999))
    if user_id not in user_tasks:
        user_tasks[user_id] = {}
    if user_id not in user_status:
        user_status[user_id] = {}

    user_tasks[user_id][job_id] = time.time()
    
    clean_name = "".join(c for c in filename_raw if c.isalnum() or c in "._-").strip()
    if not clean_name:
        clean_name = f"OTT_{job_id}"
    out_filename = f"{clean_name}.mp4"
    
    save_dir = join(config.DOWNLOAD_DIRECTORY, f"ott_{user_id}_{job_id}")
    os.makedirs(save_dir, exist_ok=True)
    out_path = join(save_dir, out_filename)

    user_status[user_id][job_id] = {
        "id": "Downloading",
        "filename": out_filename,
        "target": "Direct URL / Stream",
        "progress": "0%",
        "save_dir": save_dir
    }

    msg = await message.reply_text("📥 **Initializing downloader engine...**")
    
    ploop = asyncio.create_task(update_progress_loop(user_id, job_id, msg))
    if user_id not in progress_tasks:
        progress_tasks[user_id] = {}
    progress_tasks[user_id][job_id] = ploop

    asyncio.create_task(execute_ott(user_id, job_id, url, out_path, save_dir, msg))

async def execute_ott(user_id, job_id, url, out_path, save_dir, msg):
    try:
        cookies_arg = []
        if os.path.exists("cookies.txt"):
            cookies_arg = ["--cookies", "cookies.txt"]

        # yt-dlp से डायरेक्ट बेस्ट वीडियो लिंक निकालना
        cmd_json = ["yt-dlp", "-J", url] + cookies_arg
        rc, stdout, stderr = await runcmd(cmd_json)
        
        if rc != 0:
            # अगर yt-dlp काम न करे, तो लिंक को सीधे ही FFmpeg को फीड कर देना
            video_url = url
        else:
            try:
                info = json.loads(stdout)
                video_url = info.get("url") or url
            except Exception:
                video_url = url

        user_status[user_id][job_id]["id"] = "Processing Engine"
        
        cmd_ff = [
            "ffmpeg", "-y", "-headers", "User-Agent: Mozilla/5.0\r\n", 
            "-i", video_url, "-map", "0:v", "-map", "0:a?", "-map", "-0:d",
            "-vf", "scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2:black",
            "-c:v", "libx264", "-preset", "slow", "-b:v", "330k", "-c:a", "aac", "-b:a", "48k",
            "-fflags", "+genpts+discardcorrupt", "-movflags", "+faststart", "-f", "mp4", out_path
        ]

        p = await asyncio.create_subprocess_exec(
            *cmd_ff, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        if user_id not in user_ffmpeg_pids:
            user_ffmpeg_pids[user_id] = {}
        user_ffmpeg_pids[user_id][job_id] = p.pid
        
        stdout, stderr = await p.communicate()
        user_ffmpeg_pids[user_id].pop(job_id, None)
        
        if user_id in progress_tasks and job_id in progress_tasks[user_id]:
            progress_tasks[user_id][job_id].cancel()

        if p.returncode != 0:
            await msg.edit_text(f"❌ **Processing Engine Failed:**\n\n`{stderr.decode()[:2000]}`")
            return

        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            await msg.edit_text("⚡ **Uploading file to Telegram...**")
            await app.send_video(chat_id=user_id, video=out_path, caption=f"📥 **Downloaded:** `{os.path.basename(out_path)}`")
            await safe_delete_message(msg)
        else:
            await msg.edit_text("❌ **Download Output empty or missing.**")

    except Exception as e:
        LOG.error(f"ott global error: {e}")
        try:
            await msg.edit_text(f"❌ **OTT Downloader Error:**\n\n`{str(e)}`")
        except Exception:
            pass
    finally:
        shutil.rmtree(save_dir, ignore_errors=True)
        user_tasks.get(user_id, {}).pop(job_id, None)
        user_status.get(user_id, {}).pop(job_id, None)

# ── /compress (Compression Handler) ──────────────────────────────────────────
@app.on_message(filters.command("compress") & filters.private)
async def compress_cmd(client: Client, message: Message):
    if not await check_user_access(client, message):
        return
        
    user_id = message.from_user.id
    rep = message.reply_to_message
    if not rep or not rep.video:
        await message.reply_text("🗜 **Please reply to a Telegram video file using `/compress`**")
        return

    if len(user_tasks.get(user_id, {})) >= MAX_CONCURRENT:
        await messag
