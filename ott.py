import asyncio
import json
import os
import random
import shutil
import time
from os.path import join
from typing import Optional, Tuple
from pyrogram import filters, enums, Client
from pyrogram.types import Message

import config
from state import (
    app, allowed, LOG,
    user_tasks, user_status, user_ffmpeg_pids, progress_tasks, cancelled_jobs,
    recording_cache,
)
from constants import (
    MAX_CONCURRENT, SLOT_EMOJI,
    OTT_RES_LABEL_TO_FMT, OTT_AUDIO_LANGS,
    _HEIGHT_LABEL, _HEIGHT_FMT, _LANG_CODE_TO_LABEL,
    PROGRESS_FILLED, PROGRESS_EMPTY,
)
from utils import (
    make_job_key, next_job_id, slot_number, runcmd,
    get_duration_ffmpeg, TimeFormatter, _add_history,
)
from keyboards import (
    build_main_keyboard,
    build_ott_resolution_keyboard_dynamic,
    build_ott_audio_keyboard_dynamic,
)
from handlers.cookies import cookies_path, has_cookies


# ── progress callback (shared with record module) ─────────────────────────────

async def progress_for_pyrogram(current, total, ref_message, start, msg, save_dir,
                                 was_cancelled=False, job_id=None):
    now         = time.time()
    diff        = max(now - start, 1)
    percentage  = current * 100 / total
    speed       = current / diff
    uploaded_mb = current / (1024 * 1024)
    total_mb    = total   / (1024 * 1024)
    speed_mb    = speed   / (1024 * 1024)

    filled     = int(10 * percentage // 100)
    bar_filled = "▰" * filled
    bar_empty  = "▱" * (10 - filled)
    bar        = f"[{bar_filled}{bar_empty}]"

    if int(percentage) in {0, 10, 25, 50, 75, 90, 95, 99, 100} or current == total:
        eta    = TimeFormatter(int((total - current) / speed * 1000)) if speed > 0 else "00:00:00"
        n      = slot_number(job_id) if job_id else 1
        slot_e = SLOT_EMOJI[n - 1] if n <= 3 else "📤"
        label  = "Partial " if was_cancelled else ""
        try:
            await msg.edit_text(
                f"{slot_e} **Uploading {label}Recording**\n"
                f"`{bar}` `{percentage:.1f}%`\n"
                f"📊 `{uploaded_mb:.1f} / {total_mb:.1f} MB`\n"
                f"⚡ `{speed_mb:.1f} MB/s`  ⏳ `{eta}`"
            )
        except Exception:
            pass
        if current == total:
            done = "✅ Partial Sent!" if was_cancelled else "✅ Upload Completed!"
            try:
                await msg.edit_text(f"{done}\n🗑️ Cleaning up...")
                await asyncio.sleep(2)
                await msg.edit_text(done)
            except Exception:
                pass


# ── yt-dlp helpers ────────────────────────────────────────────────────────────

async def ytdlp_download(
    url: str, output_path: str,
    cookies_file: Optional[str] = None,
    fmt: Optional[str] = None,
    audio_lang: Optional[str] = None,
) -> Tuple[int, str, str]:
    cmd_parts = [
        "yt-dlp", "--no-playlist", "--merge-output-format", "mkv",
        "-o", output_path,
    ]
    if audio_lang:
        base_fmt = fmt or "bestvideo+bestaudio/best"
        if "bestaudio" in base_fmt:
            lang_fmt      = base_fmt.replace("bestaudio", f"bestaudio[language={audio_lang}]", 1)
            effective_fmt = f"{lang_fmt}/{base_fmt}"
        else:
            effective_fmt = f"bestvideo+bestaudio[language={audio_lang}]/bestvideo+bestaudio/best"
        cmd_parts += ["-f", effective_fmt]
    elif fmt:
        cmd_parts += ["-f", fmt, "--audio-multistreams"]
    else:
        cmd_parts += ["--audio-multistreams"]

    if cookies_file and os.path.exists(cookies_file):
        cmd_parts += ["--cookies", cookies_file]
    cmd_parts.append(url)
    process = await asyncio.create_subprocess_exec(
        *cmd_parts, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(), stderr.decode()


async def detect_ott_formats(url: str, cookies_file: Optional[str] = None) -> dict:
    cmd = ["yt-dlp", "--no-playlist", "-J"]
    if cookies_file and os.path.exists(cookies_file):
        cmd += ["--cookies", cookies_file]
    cmd.append(url)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"title": "", "heights": [], "langs": [], "duration": 0}
        data    = json.loads(stdout.decode())
        title   = data.get("title", "")
        dur     = int(data.get("duration", 0) or 0)
        heights: set = set()
        langs:   set = set()
        for f in data.get("formats", []):
            h = f.get("height")
            if h and f.get("vcodec", "none") not in ("none", None, ""):
                heights.add(int(h))
            lang = (f.get("language") or "").lower()[:3]
            if lang and f.get("acodec", "none") not in ("none", None, ""):
                langs.add(lang)
        return {
            "title":    title,
            "heights":  sorted(heights),
            "langs":    sorted(langs),
            "duration": dur,
        }
    except Exception as e:
        LOG.warning(f"detect_ott_formats error: {e}")
        return {"title": "", "heights": [], "langs": [], "duration": 0}


# ─────────────────────────────────────────────────────────────────────────────
#  /ott_download command
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("ott_download") & allowed)
async def ott_download_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "📌 **Usage:**\n"
            "```\n/ott_download https://youtube.com/... MyFilename\n```\n\n"
            "🍪 Add cookies first with /cookies_add for OTT sites.",
            reply_markup=build_main_keyboard(user_id)
        )
    user_id = message.from_user.id
    if len(user_tasks.get(user_id, {})) >= MAX_CONCURRENT:
        return await message.reply_text(
            f"❌ **All {MAX_CONCURRENT} slots are busy!**\n📊 /status  |  🛑 /cancel",
            reply_markup=build_main_keyboard(user_id)
        )

    from state import user_setup

    params       = " ".join(message.command[1:])
    parts        = params.split(" ", 1)
    url          = parts[0]
    raw_filename = parts[1].strip() if len(parts) > 1 else config.DEFAULT_FILENAME

    detect_msg = await message.reply_text(
        "🔍 **Detecting available qualities...**\n"
        "⏳ _Please wait a few seconds..._"
    )

    cookie_file = cookies_path(user_id) if has_cookies(user_id) else None
    info        = await detect_ott_formats(url, cookie_file)

    res_map: dict = {}
    for h in info["heights"]:
        lbl = _HEIGHT_LABEL.get(h, f"📺 {h}p")
        res_map[lbl] = _HEIGHT_FMT.get(h, f"bestvideo[height<={h}]+bestaudio/best[height<={h}]")
    res_map["🏆 Best"] = "bestvideo+bestaudio/best"

    audio_map: dict = {}
    for lang in info["langs"]:
        lbl = _LANG_CODE_TO_LABEL.get(lang, lang.upper())
        if lbl not in audio_map:
            audio_map[lbl] = lang
    audio_map["🌐 Multi"] = None

    if len(res_map) <= 1:
        res_map   = dict(OTT_RES_LABEL_TO_FMT)
    if not audio_map or list(audio_map.keys()) == ["🌐 Multi"]:
        audio_map = dict(OTT_AUDIO_LANGS)

    user_setup[user_id] = {
        "step": "ott_resolution",
        "url": url,
        "filename": raw_filename,
        "chat_id": message.chat.id,
        "reply_to": message.id,
        "ott_res_label": "",
        "ott_audio_label": "",
        "detected_res_map":   res_map,
        "detected_audio_map": audio_map,
        "detected_title":    info.get("title", ""),
        "detected_duration": info.get("duration", 0),
    }

    title_line  = f"📌 **Title:** `{info['title'][:55]}`\n" if info.get("title") else ""
    dur_line    = f"⏱ **Duration:** `{TimeFormatter(info['duration'] * 1000)}`\n" if info.get("duration") else ""
    res_count   = len(res_map) - 1
    audio_count = len([v for v in audio_map.values() if v is not None])

    try:
        await detect_msg.delete()
    except Exception:
        pass

    await message.reply_text(
        f"🌐 **OTT / YouTube Download**\n\n"
        f"{title_line}{dur_line}"
        f"📁 **File:** `{raw_filename}`\n"
        f"🍪 **Cookies:** `{'✅ Found' if has_cookies(user_id) else '❌ None'}`\n\n"
        f"📺 **{res_count} resolutions detected** · 🎧 **{audio_count} audio tracks**\n\n"
        f"👇 Select resolution:",
        reply_markup=build_ott_resolution_keyboard_dynamic(res_map)
    )


# ─────────────────────────────────────────────────────────────────────────────
#  OTT download task (background)
# ─────────────────────────────────────────────────────────────────────────────

async def ott_download_task(client: Client, ref_message: Message, setup: dict, user_id: int):
    job_id = next_job_id(user_id)
    if not job_id:
        await ref_message.reply_text(f"❌ All {MAX_CONCURRENT} slots full!")
        return

    job_key      = make_job_key(user_id, job_id)
    n            = slot_number(job_id)
    emoji        = SLOT_EMOJI[n - 1]
    raw_filename = setup["filename"]
    url          = setup["url"]
    fmt          = setup.get("ott_format")
    audio_lang   = setup.get("ott_audio_lang")
    res_label    = setup.get("ott_res_label", "Best")
    audio_label  = setup.get("ott_audio_label", "Multi")

    save_dir    = join(config.DOWNLOAD_DIRECTORY, f"{int(time.time())}_{job_id}")
    os.makedirs(save_dir, exist_ok=True)
    output_tmpl = join(save_dir, f"{raw_filename}.%(ext)s")

    msg = await ref_message.reply_text(
        f"{emoji} **Slot {n} — Starting OTT Download...**\n"
        f"📁 `{raw_filename}`\n"
        f"📺 `{res_label}`  🎧 `{audio_label}`\n"
        f"🍪 Cookies: `{'✅ Found' if has_cookies(user_id) else '❌ None'}`",
        reply_markup=build_main_keyboard(user_id)
    )

    user_tasks.setdefault(user_id, {})[job_id] = time.time()
    user_status.setdefault(user_id, {})[job_id] = {
        "id": int(time.time()), "filename": raw_filename,
        "target": "∞", "progress": "00:00:00",
        "save_dir": save_dir, "mode": "ott",
    }
    dl_start = time.time()

    _ott_pulse = [0]

    async def ott_progress():
        while (
            user_id in user_tasks and
            job_id in user_tasks.get(user_id, {}) and
            job_key not in cancelled_jobs
        ):
            elapsed = time.time() - dl_start
            prog    = TimeFormatter(int(elapsed * 1000))
            if job_id in user_status.get(user_id, {}):
                user_status[user_id][job_id]["progress"] = prog
            _ott_pulse[0] = (_ott_pulse[0] + 1) % 10
            p   = _ott_pulse[0]
            bar = PROGRESS_EMPTY * p + PROGRESS_FILLED + PROGRESS_EMPTY * (9 - p)
            try:
                await msg.edit_text(
                    f"{emoji} <b>Slot {n} — Downloading (OTT/YT)</b>\n"
                    f"📁 <code>{raw_filename}</code>\n"
                    f"📺 <code>{res_label}</code>  🎧 <code>{audio_label}</code>\n"
                    f"{bar}\n"
                    f"⏱️ Elapsed: <code>{prog}</code>\n\n🛑 /cancel to stop",
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception:
                pass
            await asyncio.sleep(5)

    prog_task = asyncio.create_task(ott_progress())
    progress_tasks.setdefault(user_id, {})[job_id] = prog_task

    try:
        cookie_file = cookies_path(user_id) if has_cookies(user_id) else None
        retcode, out, err = await ytdlp_download(url, output_tmpl, cookie_file, fmt, audio_lang)
        if job_id in progress_tasks.get(user_id, {}):
            progress_tasks[user_id][job_id].cancel()
        was_cancelled = job_key in cancelled_jobs
        if retcode != 0 and not was_cancelled:
            raise Exception(f"yt-dlp error:\n{err[-2000:]}")

        video_path = None
        for f in os.listdir(save_dir):
            if f.startswith(raw_filename):
                video_path = join(save_dir, f)
                break
        if not video_path or not os.path.exists(video_path):
            raise Exception("Downloaded file not found.")

        thumb_msg  = await ref_message.reply_text(f"{emoji} **Slot {n} — Generating thumbnail...**")
        dur        = await get_duration_ffmpeg(video_path)
        rand_sec   = random.randint(5, max(dur - 5, 6)) if dur > 10 else 1
        thumb_path = join(save_dir, "thumb.jpg")
        await runcmd(f'ffmpeg -y -ss {rand_sec} -i "{video_path}" -vframes 1 -q:v 2 "{thumb_path}"')
        await thumb_msg.delete()

        old_line = (
            "" if was_cancelled else
            "\n_🗑 Video auto-deleted from server in 2 hours._\n_📥 Use /recording_old to get this video again._"
        )
        caption = (
            f"{emoji} **{raw_filename}**\n\n"
            f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
            f"📺 **Resolution:** `{res_label}`\n"
            f"🎧 **Audio:** `{audio_label}`\n"
            f"📥 **Source:** OTT/YouTube\n"
            f"🍪 **Cookies:** `{'✅ Used' if cookie_file else '❌ None'}`\n"
            f"📁 **Format:** MKV\n\n"
            f"{'⚠️ _Partial (cancelled)_' if was_cancelled else '✅ _Downloaded successfully!_'}"
            f"{old_line}"
        )
        size_mb = round(os.path.getsize(video_path) / (1024 * 1024), 2) if os.path.exists(video_path) else 0
        uname   = ref_message.from_user.username or ref_message.from_user.first_name or str(user_id)
        _add_history({
            "type":        "ott",
            "status":      "cancelled" if was_cancelled else "done",
            "user_id":     user_id,
            "username":    uname,
            "filename":    raw_filename,
            "duration_s":  int(dur),
            "size_mb":     size_mb,
            "url":         url[:120],
            "res_label":   res_label,
            "audio_label": audio_label,
        })

        start_time = time.time()
        sent = await ref_message.reply_video(
            video=video_path, caption=caption, duration=dur,
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            progress=progress_for_pyrogram,
            progress_args=(ref_message, start_time, msg, save_dir, was_cancelled, job_id)
        )
        if not was_cancelled and sent:
            recording_cache[user_id] = {
                "msg_id":   sent.id,
                "chat_id":  sent.chat.id,
                "filename": raw_filename,
                "ts":       time.time(),
                "type":     "ott",
            }
        shutil.rmtree(save_dir, ignore_errors=True)

    except Exception as e:
        LOG.error(f"ott_download error [{job_id}]: {e}")
        uname = ref_message.from_user.username or ref_message.from_user.first_name or str(user_id)
        _add_history({
            "type":        "ott",
            "status":      "cancelled" if job_key in cancelled_jobs else "failed",
            "user_id":     user_id,
            "username":    uname,
            "filename":    setup.get("filename", "?"),
            "duration_s":  0,
            "size_mb":     0,
            "url":         setup.get("url", "")[:120],
            "res_label":   setup.get("ott_res_label", ""),
            "audio_label": setup.get("ott_audio_label", ""),
        })
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
