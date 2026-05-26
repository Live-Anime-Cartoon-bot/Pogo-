import asyncio
import os
import random
import shutil
import time
from os.path import join
from pyrogram import filters
from pyrogram.types import Message

import config
from state import (
    app, allowed, LOG,
    user_tasks, user_status, user_ffmpeg_pids, progress_tasks, cancelled_jobs,
    compress_pending, user_setup,
)
from constants import MAX_CONCURRENT, SLOT_EMOJI, COMPRESS_PRESETS
from lang import t, to_canonical
from utils import (
    make_job_key, next_job_id, slot_number, runcmd,
    get_duration_ffmpeg, get_video_media,
)
from keyboards import build_main_keyboard, build_compress_keyboard
from handlers.ott import progress_for_pyrogram

# Bilingual compress preset keys: both EN and HI map to EN key
_COMPRESS_KEYS = {
    t(None, "btn_cmp_high"): t(None, "btn_cmp_high"),
    t(None, "btn_cmp_med"):  t(None, "btn_cmp_med"),
    t(None, "btn_cmp_low"):  t(None, "btn_cmp_low"),
}


@app.on_message(filters.command("compress") & allowed)
async def compress_cmd(client, message: Message):
    if not message.reply_to_message or not get_video_media(message.reply_to_message):
        return await message.reply_text(
            "❌ **Reply to a video message with /compress**",
            reply_markup=build_main_keyboard(message.from_user.id)
        )
    user_id = message.from_user.id
    compress_pending[user_id] = message.reply_to_message.id
    user_setup[user_id] = {"step": "compress"}
    await message.reply_text(
        "🗜 **Video Compress**\n\nSelect compression quality:",
        reply_markup=build_compress_keyboard(uid=user_id)
    )


async def run_compress(client, message: Message, user_id: int, canon_text: str):
    """canon_text is already the canonical English button label from the router."""
    cancel_en = t(None, "btn_cmp_cancel")

    if canon_text == cancel_en:
        compress_pending.pop(user_id, None)
        user_setup.pop(user_id, None)
        return await message.reply_text(
            "❌ Compression cancelled.",
            reply_markup=build_main_keyboard(user_id)
        )

    if canon_text not in COMPRESS_PRESETS:
        return await message.reply_text(
            "❓ Please choose a quality option.",
            reply_markup=build_compress_keyboard(uid=user_id)
        )

    video_msg_id = compress_pending.pop(user_id, None)
    if not video_msg_id:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            "❌ Session expired. Reply to video and use /compress again.",
            reply_markup=build_main_keyboard(user_id)
        )

    ffmpeg_args, quality_desc = COMPRESS_PRESETS[canon_text]
    user_setup.pop(user_id, None)

    if len(user_tasks.get(user_id, {})) >= MAX_CONCURRENT:
        return await message.reply_text(
            f"❌ All {MAX_CONCURRENT} slots busy. Cancel one first.",
            reply_markup=build_main_keyboard(user_id)
        )

    job_id  = next_job_id(user_id)
    if not job_id:
        return
    job_key  = make_job_key(user_id, job_id)
    n        = slot_number(job_id)
    emoji_s  = SLOT_EMOJI[n - 1]
    save_dir = join(config.DOWNLOAD_DIRECTORY, f"{int(time.time())}_{job_id}_compress")
    os.makedirs(save_dir, exist_ok=True)

    user_tasks.setdefault(user_id, {})[job_id] = time.time()
    user_status.setdefault(user_id, {})[job_id] = {
        "id": int(time.time()), "filename": "Compressed Video",
        "target": "∞", "progress": "00:00:00",
        "save_dir": save_dir, "mode": "compress",
    }

    msg = await message.reply_text(
        f"{emoji_s} **Slot {n} — Starting compression ({quality_desc})...**",
        reply_markup=build_main_keyboard(user_id)
    )

    async def do_compress():
        try:
            await msg.edit_text(f"{emoji_s} **Slot {n} — Downloading original video...**")
            orig_path     = join(save_dir, "original.mkv")
            video_message = await client.get_messages(message.chat.id, video_msg_id)
            if not video_message or not get_video_media(video_message):
                raise Exception("Original video message not found.")
            await client.download_media(video_message, file_name=orig_path)

            if not os.path.exists(orig_path) or os.path.getsize(orig_path) == 0:
                raise Exception("Download failed or file is empty.")

            orig_size_mb = os.path.getsize(orig_path) / (1024 * 1024)
            await msg.edit_text(
                f"{emoji_s} **Slot {n} — Compressing...**\n"
                f"📦 Original: `{orig_size_mb:.1f} MB`  🎛 `{quality_desc}`"
            )

            out_path = join(save_dir, "compressed.mkv")
            rc, _, err = await runcmd(f'ffmpeg -y -i "{orig_path}" {ffmpeg_args} "{out_path}"')
            if rc != 0:
                raise Exception(f"FFmpeg error:\n{err[-1500:]}")

            new_size_mb = os.path.getsize(out_path) / (1024 * 1024)
            reduction   = max(0, (1 - new_size_mb / orig_size_mb) * 100)

            dur      = await get_duration_ffmpeg(out_path)
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
                video=out_path, caption=caption, duration=dur,
                thumb=thumb_path if os.path.exists(thumb_path) else None,
                progress=progress_for_pyrogram,
                progress_args=(msg, start_time, msg, save_dir, False, job_id)
            )
            shutil.rmtree(save_dir, ignore_errors=True)

        except Exception as e:
            LOG.error(f"compress error [{job_id}]: {e}")
            try:
                await msg.edit_text(f"{emoji_s} **Compression Failed!**\n\n`{str(e)[:2000]}`")
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
