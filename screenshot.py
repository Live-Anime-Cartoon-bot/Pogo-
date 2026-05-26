import asyncio
import os
import shutil
import time
from os.path import join
from pyrogram import filters
from pyrogram.types import Message, InputMediaPhoto

import config
from state import app, allowed, LOG
from utils import get_video_media, get_duration_ffmpeg, runcmd, TimeFormatter
from keyboards import build_main_keyboard


@app.on_message(filters.command("screenshot") & allowed)
async def screenshot_cmd(client, message: Message):
    if not message.reply_to_message or not get_video_media(message.reply_to_message):
        return await message.reply_text(
            "❌ **Reply to a video with /screenshot [count]**\n\n"
            "Example: `/screenshot 10` → 10 screenshots (max 30)",
            reply_markup=build_main_keyboard(user_id)
        )
    try:
        count = int(message.command[1]) if len(message.command) > 1 else 1
        count = max(1, min(count, 30))
    except (ValueError, IndexError):
        count = 1

    user_id       = message.from_user.id
    video_message = message.reply_to_message
    msg = await message.reply_text(
        f"📸 **Extracting {count} screenshot{'s' if count > 1 else ''}...**",
        reply_markup=build_main_keyboard(user_id)
    )
    save_dir = join(config.DOWNLOAD_DIRECTORY, f"{int(time.time())}_ss_{user_id}")
    os.makedirs(save_dir, exist_ok=True)

    try:
        await msg.edit_text("📥 **Downloading video...**")
        orig_path = join(save_dir, "video.mkv")
        await client.download_media(video_message, file_name=orig_path)

        if not os.path.exists(orig_path) or os.path.getsize(orig_path) == 0:
            raise Exception("Video download failed or file is empty.")

        dur = await get_duration_ffmpeg(orig_path)

        await msg.edit_text(f"📸 **Extracting {count} screenshot{'s' if count > 1 else ''}...**")

        if dur <= 0:
            timestamps = [0]
            count = 1
        elif dur == 1:
            timestamps = [0]
            count = 1
        elif count == 1:
            timestamps = [max(dur // 2, 0)]
        else:
            usable_dur = max(dur - 2, 1)
            count      = min(count, usable_dur)
            step       = usable_dur / max(count - 1, 1)
            timestamps = [min(int(i * step), dur - 1) for i in range(count)]

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

        await msg.edit_text(
            f"📤 **Uploading {len(screenshot_paths)} screenshot{'s' if len(screenshot_paths) > 1 else ''}...**"
        )

        caption_main = (
            f"📸 **{len(screenshot_paths)} Screenshot{'s' if len(screenshot_paths) > 1 else ''}**\n"
            f"⏱ **Video Duration:** `{TimeFormatter(dur * 1000)}`"
        )
        for batch_start in range(0, len(screenshot_paths), 10):
            batch = screenshot_paths[batch_start: batch_start + 10]
            media_group = [
                InputMediaPhoto(sp, caption=caption_main if (batch_start == 0 and idx == 0) else "")
                for idx, sp in enumerate(batch)
            ]
            await message.reply_media_group(media_group)

        await msg.edit_text(
            f"✅ **{len(screenshot_paths)} screenshot{'s' if len(screenshot_paths) > 1 else ''} sent!**"
        )
        shutil.rmtree(save_dir, ignore_errors=True)

    except Exception as e:
        LOG.error(f"screenshot error: {e}")
        try:
            await msg.edit_text(f"❌ **Screenshot failed!**\n\n`{str(e)[:2000]}`")
        except Exception:
            pass
        shutil.rmtree(save_dir, ignore_errors=True)
