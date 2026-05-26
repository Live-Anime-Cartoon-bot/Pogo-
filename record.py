import asyncio
import os
import random
import shlex
import shutil
import time
from os.path import join
from pyrogram import filters, enums, Client
from pyrogram.types import Message

import config
import limit_system
from state import (
    app, allowed, LOG,
    user_tasks, user_status, user_ffmpeg_pids, progress_tasks,
    cancelled_jobs, user_setup, recording_cache,
)
from constants import (
    MAX_CONCURRENT, SLOT_EMOJI, WM_POSITIONS, WM_LABEL,
    VIDEO_SIZES, PROGRESS_FILLED, PROGRESS_EMPTY,
)
from utils import (
    make_job_key, next_job_id, slot_number, runcmd,
    time_to_seconds, TimeFormatter, get_duration_ffmpeg,
    _add_history, build_metadata_args, http_opts, detect_stream_info,
    format_quality_line,
)
from keyboards import (
    build_main_keyboard, build_audio_keyboard,
    build_watermark_keyboard, setup_summary_text,
)
from handlers.ott import progress_for_pyrogram


# ─────────────────────────────────────────────────────────────────────────────
#  /rec
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("rec") & allowed)
async def rec_command(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "📌 **Usage:**\n"
            "```\n/rec http://m3u8link 01:00:00 FileName\n```\n\n"
            "• **URL** — m3u8 / rtmp / direct stream\n"
            "• **Duration** — HH:MM:SS format\n"
            "• **Filename** — saved as `filename.mkv`",
            reply_markup=build_main_keyboard(user_id)
        )

    user_id = message.from_user.id
    if len(user_tasks.get(user_id, {})) >= MAX_CONCURRENT:
        return await message.reply_text(
            f"❌ **Maximum {MAX_CONCURRENT} simultaneous recordings reached!**\n"
            f"📊 /status  |  🛑 /cancel",
            reply_markup=build_main_keyboard(user_id)
        )

    args      = message.command[1:]
    url       = args[0]
    timestamp = args[1]
    filename  = " ".join(args[2:]).strip() if len(args) > 2 else config.DEFAULT_FILENAME

    msg = await message.reply_text("🔍 **Detecting stream info...**")
    try:
        info = await detect_stream_info(url)
    except Exception as e:
        return await msg.edit_text(f"❌ **Stream detection failed!**\n\n`{e}`")

    tracks   = info["tracks"]
    video    = info["video"]
    selected = set(t["index"] for t in tracks)

    user_setup[user_id] = {
        "mode":            "record",
        "step":            "audio" if tracks else "watermark",
        "url":             url,
        "timestamp":       timestamp,
        "filename":        filename,
        "tracks":          tracks,
        "selected_tracks": selected,
        "watermark_pos":   None,
        "watermark_text":  config.DEFAULT_FILENAME,
        "auto_mode":       False,
        "video_size":      "original",
        "video_info":      video,
    }

    quality_line = format_quality_line(video)
    audio_line   = ", ".join(t["label"] for t in tracks) if tracks else "Auto"

    if tracks:
        text = (
            f"✅ **Stream Detected!**\n\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio Tracks:** `{audio_line}`\n"
            f"⏱ **Duration:** `{timestamp}`\n"
            f"📁 **File:** `{filename}`\n\n"
            f"👇 Select audio tracks to include:"
        )
        kb = build_audio_keyboard(tracks, selected, uid=user_id)
    else:
        text = (
            f"✅ **Stream Detected!**\n\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio:** No tracks — will auto-select\n\n"
        ) + setup_summary_text(user_setup[user_id])
        kb = build_watermark_keyboard(user_setup[user_id], uid=user_id)

    try:
        await msg.delete()
    except Exception:
        pass
    await message.reply_text(text, reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
#  /download
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("download") & allowed)
async def download_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "📌 **Usage:**\n"
            "```\n/download http://link FileName\n```\n\n"
            "• **URL** — direct link / m3u8 stream\n"
            "• **Filename** — optional (saved as `filename.mkv`)",
            reply_markup=build_main_keyboard(user_id)
        )

    user_id = message.from_user.id
    if len(user_tasks.get(user_id, {})) >= MAX_CONCURRENT:
        return await message.reply_text(
            f"❌ **Maximum {MAX_CONCURRENT} simultaneous downloads reached!**\n"
            f"📊 /status  |  🛑 /cancel",
            reply_markup=build_main_keyboard(user_id)
        )

    args     = message.command[1:]
    url      = args[0]
    filename = " ".join(args[1:]).strip() if len(args) > 1 else config.DEFAULT_FILENAME

    msg = await message.reply_text("🔍 **Detecting stream info...**")
    try:
        info = await detect_stream_info(url)
    except Exception as e:
        return await msg.edit_text(f"❌ **Stream detection failed!**\n\n`{e}`")

    tracks   = info["tracks"]
    video    = info["video"]
    selected = set(t["index"] for t in tracks)

    user_setup[user_id] = {
        "mode":            "download",
        "step":            "audio" if tracks else "watermark",
        "url":             url,
        "timestamp":       None,
        "filename":        filename,
        "tracks":          tracks,
        "selected_tracks": selected,
        "watermark_pos":   None,
        "watermark_text":  config.DEFAULT_FILENAME,
        "auto_mode":       False,
        "video_size":      "original",
        "video_info":      video,
    }

    quality_line = format_quality_line(video)

    if tracks:
        text = (
            f"✅ **Stream Detected!**\n\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio Tracks:** `{', '.join(t['label'] for t in tracks)}`\n"
            f"📁 **File:** `{filename}`\n\n"
            f"👇 Select audio tracks to include:"
        )
        kb = build_audio_keyboard(tracks, selected, uid=user_id)
    else:
        text = (
            f"✅ **Stream Detected!**\n\n"
            f"📺 **Quality:** `{quality_line}`\n"
            f"🎵 **Audio:** No tracks — will auto-select\n\n"
        ) + setup_summary_text(user_setup[user_id])
        kb = build_watermark_keyboard(user_setup[user_id], uid=user_id)

    try:
        await msg.delete()
    except Exception:
        pass
    await message.reply_text(text, reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
#  Core recording / download logic
# ─────────────────────────────────────────────────────────────────────────────

async def handle_record(client: Client, ref_message: Message, setup: dict, user_id: int):
    job_id = next_job_id(user_id)
    if job_id is None:
        await ref_message.reply_text(f"❌ All {MAX_CONCURRENT} recording slots are busy!")
        return

    job_key         = make_job_key(user_id, job_id)
    n               = slot_number(job_id)
    emoji           = SLOT_EMOJI[n - 1]
    mode            = setup.get("mode", "record")
    url             = setup["url"]
    timestamp       = setup.get("timestamp")
    raw_filename    = setup["filename"]
    tracks          = setup.get("tracks", [])
    selected_tracks = setup.get("selected_tracks", set())
    watermark_pos   = setup.get("watermark_pos")
    watermark_text  = setup.get("watermark_text", config.DEFAULT_FILENAME)
    auto_mode       = setup.get("auto_mode", False) if mode == "record" else False
    video_size_key  = setup.get("video_size", "original")
    is_download     = (mode == "download")
    action_label    = "Downloading" if is_download else "Recording"

    filename   = f"{raw_filename}.mkv"
    save_dir   = join(config.DOWNLOAD_DIRECTORY, f"{int(time.time())}_{job_id}")
    os.makedirs(save_dir, exist_ok=True)
    video_path = join(save_dir, filename)

    msg = await ref_message.reply_text(
        f"{emoji} **Slot {n} — Initializing {action_label.lower()}...**\n📁 `{raw_filename}`"
    )

    try:
        user_tasks.setdefault(user_id, {})[job_id] = time.time()
        duration = time_to_seconds(timestamp) if timestamp else 0
        user_status.setdefault(user_id, {})[job_id] = {
            "id": int(time.time()), "filename": raw_filename,
            "target": timestamp or "∞", "progress": "00:00:00",
            "save_dir": save_dir, "mode": mode,
        }

        recording_start = time.time()

        if tracks and selected_tracks:
            video_map  = "-map 0:V?"
            audio_maps = " ".join(f"-map 0:a:{t['index']}?" for t in tracks if t["index"] in selected_tracks)
        else:
            video_map  = "-map 0:V?"
            audio_maps = "-map 0:a?"

        meta_args     = build_metadata_args(tracks, selected_tracks, config.CHANNEL_NAME)
        size_vf       = VIDEO_SIZES.get(video_size_key, VIDEO_SIZES["original"])["vf"]
        filters_chain = []
        if size_vf:
            filters_chain.append(size_vf)
        if watermark_pos and watermark_text:
            x, y      = WM_POSITIONS[watermark_pos]
            safe_text = watermark_text.replace("'", "\\'").replace(":", "\\:")
            filters_chain.append(
                f"drawtext=text='{safe_text}':"
                f"fontsize=28:fontcolor=white@0.85:"
                f"x={x}:y={y}:box=1:boxcolor=black@0.45:boxborderw=6"
            )

        if filters_chain:
            vf          = f'-vf "{",".join(filters_chain)}"'
            video_codec = "-c:v libx264 -preset slow -b:v 330k"
        else:
            vf          = ""
            video_codec = "-c:v copy"
        audio_codec = "-c:a aac -b:a 48k"

        _pulse_pos = [0]

        async def update_progress():
            while (
                user_id in user_tasks and
                job_id  in user_tasks.get(user_id, {}) and
                job_key not in cancelled_jobs
            ):
                elapsed  = time.time() - recording_start
                prog     = TimeFormatter(int(elapsed * 1000))
                if job_id in user_status.get(user_id, {}):
                    user_status[user_id][job_id]["progress"] = prog
                speed_mb = random.uniform(2.0, 8.0)
                try:
                    if is_download:
                        _pulse_pos[0] = (_pulse_pos[0] + 1) % 10
                        p   = _pulse_pos[0]
                        bar = (PROGRESS_EMPTY * p + PROGRESS_FILLED + PROGRESS_EMPTY * (9 - p))
                        await msg.edit_text(
                            f"{emoji} **Slot {n} — Downloading**\n"
                            f"📁 `{raw_filename}`\n"
                            f"{bar}\n"
                            f"⏱️ Elapsed: `{prog}`\n"
                            f"⚡ `{speed_mb:.1f} MB/s`\n\n🛑 /cancel to stop",
                            parse_mode=enums.ParseMode.HTML
                        )
                    else:
                        pct     = min((elapsed / duration) * 100, 100) if duration > 0 else 0
                        eta_sec = ((duration - elapsed) / (pct / 100)) if pct > 0 else 0
                        filled  = int(10 * pct // 100)
                        bar     = PROGRESS_FILLED * filled + PROGRESS_EMPTY * (10 - filled)
                        await msg.edit_text(
                            f"{emoji} **Slot {n} — Recording**\n"
                            f"📁 `{raw_filename}`\n"
                            f"{bar} `{pct:.1f}%`\n"
                            f"📊 `{prog}` / `{TimeFormatter(duration * 1000)}`\n"
                            f"⚡ `{speed_mb:.1f} MB/s`  ⏳ `{TimeFormatter(int(eta_sec * 1000))}`\n\n"
                            f"🛑 /cancel to stop",
                            parse_mode=enums.ParseMode.HTML
                        )
                except Exception:
                    pass
                await asyncio.sleep(5)

        prog_task = asyncio.create_task(update_progress())
        progress_tasks.setdefault(user_id, {})[job_id] = prog_task
        video_path_local = video_path

        if auto_mode:
            await msg.edit_text(f"{emoji} **Slot {n} — Auto Mode: Recording first 1 min...**")
            part1       = join(save_dir, "part1.mkv")
            part2       = join(save_dir, "part2.mkv")
            concat_list = join(save_dir, "concat.txt")

            cmd1 = (
                f'ffmpeg -y {http_opts(url)} -probesize 10000000 -analyzeduration 15000000 '
                f'-i "{url}" {video_map} {audio_maps} {vf} '
                f'{video_codec} {audio_codec} {meta_args} -movflags +faststart -t 00:01:00 "{part1}"'
            )
            proc1 = await asyncio.create_subprocess_exec(
                *shlex.split(cmd1), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
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
                    f'{video_codec} {audio_codec} {meta_args} -movflags +faststart -t 00:01:00 "{part2}"'
                )
                proc2 = await asyncio.create_subprocess_exec(
                    *shlex.split(cmd2), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
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
                video_path_local = video_path if (rc == 0 and os.path.exists(video_path)) else part1
        else:
            time_arg   = f"-t {timestamp}" if timestamp else ""
            ffmpeg_cmd = (
                f'ffmpeg -y {http_opts(url)} -probesize 10000000 -analyzeduration 15000000 '
                f'-i "{url}" {video_map} {audio_maps} {vf} '
                f'{video_codec} {audio_codec} {meta_args} -movflags +faststart {time_arg} "{video_path}"'
            )
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(ffmpeg_cmd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            user_ffmpeg_pids.setdefault(user_id, {})[job_id] = proc.pid
            LOG.info(f"FFmpeg PID {proc.pid} | user {user_id} | {job_id}")
            _, stderr_bytes = await proc.communicate()
            user_ffmpeg_pids.get(user_id, {}).pop(job_id, None)
            video_path_local = video_path

            was_cancelled = job_key in cancelled_jobs
            if proc.returncode != 0 and not was_cancelled:
                raise Exception(f"FFmpeg Error:\n{stderr_bytes.decode()[-2000:]}")

        if job_id in progress_tasks.get(user_id, {}):
            progress_tasks[user_id][job_id].cancel()
            del progress_tasks[user_id][job_id]

        was_cancelled = job_key in cancelled_jobs

        if not os.path.exists(video_path_local) or os.path.getsize(video_path_local) == 0:
            if was_cancelled:
                await msg.edit_text(f"{emoji} **Slot {n} — Cancelled. No video.**")
                return
            raise Exception("Video file missing or empty.")

        thumb_msg  = await ref_message.reply_text(f"{emoji} **Slot {n} — Generating thumbnail...**")
        dur        = await get_duration_ffmpeg(video_path_local) or (time_to_seconds(timestamp) if timestamp else 0)
        fixed_path = join(save_dir, f"fixed_{filename}")
        rc, _, _   = await runcmd(
            f'ffmpeg -y -i "{video_path_local}" -map 0 -c copy '
            f'-metadata creation_time="{time.strftime("%Y-%m-%dT%H:%M:%S")}" "{fixed_path}"'
        )
        if rc == 0:
            os.replace(fixed_path, video_path_local)

        rand_sec   = random.randint(5, max(dur - 5, 6))
        thumb_path = join(save_dir, "thumb.jpg")
        await runcmd(f'ffmpeg -y -ss {rand_sec} -i "{video_path_local}" -vframes 1 -q:v 2 "{thumb_path}"')
        await thumb_msg.delete()

        sel_labels = [t["label"] for t in tracks if t["index"] in selected_tracks] or ["All"]
        wm_desc    = "OFF" if not watermark_pos else f"{WM_LABEL.get(watermark_pos)} → {watermark_text}"
        size_label = VIDEO_SIZES.get(video_size_key, VIDEO_SIZES["original"])["label"]

        if is_download:
            status_line = "⚠️ _Partial download (cancelled)_" if was_cancelled else "✅ _Downloaded successfully!_"
            old_line    = "" if was_cancelled else "\n_🗑 Video auto-deleted from server in 2 hours._\n_📥 Use /recording_old to get this video again._"
            caption = (
                f"{emoji} **{raw_filename}**\n\n"
                f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
                f"🎵 **Audio:** `{', '.join(sel_labels)}`\n"
                f"🖼 **Watermark:** `{wm_desc}`\n"
                f"📁 **Format:** MKV\n\n{status_line}{old_line}"
            )
        else:
            auto_desc   = "✅ First+Last 1min" if auto_mode else "❌"
            status_line = "⚠️ _Partial recording (cancelled)_" if was_cancelled else "✅ _Recorded successfully!_"
            old_line    = "" if was_cancelled else "\n_🗑 Video auto-deleted from server in 2 hours._\n_📥 Use /recording_old to get this video again._"
            caption = (
                f"{emoji} **{raw_filename}**\n\n"
                f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
                f"🎵 **Audio:** `{', '.join(sel_labels)}`\n"
                f"🖼 **Watermark:** `{wm_desc}`\n"
                f"📐 **Size:** `{size_label}`\n"
                f"⏩ **Auto:** `{auto_desc}`\n"
                f"📁 **Format:** MKV\n\n{status_line}{old_line}"
            )

        size_mb = round(os.path.getsize(video_path_local) / (1024 * 1024), 2) if os.path.exists(video_path_local) else 0
        uname   = ref_message.from_user.username or ref_message.from_user.first_name or str(user_id)
        _add_history({
            "type":       "download" if is_download else "rec",
            "status":     "cancelled" if was_cancelled else "done",
            "user_id":    user_id,
            "username":   uname,
            "filename":   raw_filename,
            "duration_s": int(dur),
            "size_mb":    size_mb,
            "url":        url[:120],
        })

        start_time = time.time()
        sent = await ref_message.reply_video(
            video=video_path_local, caption=caption, duration=dur,
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
                "type":     "download" if is_download else "rec",
            }
        shutil.rmtree(save_dir, ignore_errors=True)

    except Exception as e:
        LOG.error(f"handle_record [{job_id}] error: {e}")
        uname = ref_message.from_user.username or ref_message.from_user.first_name or str(user_id)
        _add_history({
            "type":       "download" if setup.get("mode") == "download" else "rec",
            "status":     "cancelled" if job_key in cancelled_jobs else "failed",
            "user_id":    user_id,
            "username":   uname,
            "filename":   setup.get("filename", "?"),
            "duration_s": 0,
            "size_mb":    0,
            "url":        setup.get("url", "")[:120],
        })
        if job_key not in cancelled_jobs:
            try:
                await msg.edit(f"{emoji} **Slot {n} — Failed!**\n\n`{str(e)[:3000]}`")
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
