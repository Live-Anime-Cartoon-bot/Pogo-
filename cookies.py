import os
import time
from os.path import join
from datetime import datetime
from pyrogram import filters
from pyrogram.types import Message

import config
from state import app, allowed, LOG, tz, user_setup
from keyboards import build_main_keyboard


def cookies_dir() -> str:
    path = join(config.DOWNLOAD_DIRECTORY, "cookies")
    os.makedirs(path, exist_ok=True)
    return path


def cookies_path(user_id: int) -> str:
    return join(cookies_dir(), f"{user_id}_cookies.txt")


def has_cookies(user_id: int) -> bool:
    return os.path.exists(cookies_path(user_id))


@app.on_message(filters.command("cookies_add") & allowed)
async def cookies_add_cmd(client, message: Message):
    user_setup.setdefault(message.from_user.id, {})["awaiting_cookies"] = True
    await message.reply_text(
        "🍪 **Add Cookies**\n\n"
        "📎 **Reply to this message with your `cookies.txt` file.**\n\n"
        "📝 How to get cookies:\n"
        "• Install **EditThisCookie** or **Get cookies.txt** extension\n"
        "• Login to OTT platform\n"
        "• Export cookies as `cookies.txt` (Netscape format)\n\n"
        "⚠️ _Cookies are stored privately per user._",
        reply_markup=build_main_keyboard(user_id)
    )


@app.on_message(filters.document & allowed)
async def document_handler(client, message: Message):
    user_id = message.from_user.id
    setup   = user_setup.get(user_id, {})
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
            f"📦 **Size:** `{size_kb:.1f} KB`\n\n"
            f"Now use /ott_download with OTT URLs — cookies will be applied automatically. 🍪"
        )
    except Exception as e:
        LOG.error(f"cookies_add error: {e}")
        await msg.edit_text(f"❌ **Failed to save cookies:** `{e}`")


@app.on_message(filters.command("cookies_status") & allowed)
async def cookies_status_cmd(client, message: Message):
    user_id = message.from_user.id
    path    = cookies_path(user_id)
    if not os.path.exists(path):
        return await message.reply_text(
            "❌ **No cookies found!**\n\nUse /cookies_add to upload.",
            reply_markup=build_main_keyboard(user_id)
        )
    size_kb  = os.path.getsize(path) / 1024
    created  = datetime.fromtimestamp(os.path.getctime(path), tz=tz).strftime("%d-%m-%Y %I:%M:%S %p")
    modified = datetime.fromtimestamp(os.path.getmtime(path), tz=tz).strftime("%d-%m-%Y %I:%M:%S %p")
    with open(path, "r", errors="ignore") as f:
        lines = [l for l in f.readlines() if l.strip() and not l.startswith("#")]
    await message.reply_text(
        f"🍪 **Cookies Status**\n\n"
        f"✅ **Status:** Active\n"
        f"📦 **Size:** `{size_kb:.1f} KB`\n"
        f"🔢 **Entries:** `{len(lines)}`\n"
        f"🕒 **Uploaded:** `{created}`\n"
        f"🔄 **Modified:** `{modified}`\n\n"
        f"🗑 Use /del_cookies to remove",
        reply_markup=build_main_keyboard(user_id)
    )


@app.on_message(filters.command("del_cookies") & allowed)
async def del_cookies_cmd(client, message: Message):
    user_id = message.from_user.id
    path    = cookies_path(user_id)
    if not os.path.exists(path):
        return await message.reply_text("❌ **No cookies to delete!**", reply_markup=build_main_keyboard(user_id))
    os.remove(path)
    await message.reply_text(
        "🗑 **Cookies deleted successfully!**\n\nUse /cookies_add to upload new ones.",
        reply_markup=build_main_keyboard(user_id)
    )
