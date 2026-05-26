import asyncio
import time
from datetime import datetime
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import config
import verify
import limit_system
from state import app, allowed, LOG, tz, history_log, user_tasks, user_status, recording_cache
from constants import MAX_CONCURRENT, SLOT_EMOJI
from lang import t, set_lang, get_lang
from utils import slot_number, TimeFormatter, time_to_seconds
from keyboards import build_main_keyboard
from shortener import shrink, shrink2

_RECORDING_OLD_EXPIRY = 2 * 3600   # 2 hours in seconds


# ─────────────────────────────────────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("start"))
async def start(client, message: Message):
    user_id = message.from_user.id

    if len(message.command) > 1 and message.command[1].startswith("verify_"):
        token = message.command[1].replace("verify_", "", 1)
        if verify.confirm_token(user_id, token):
            limit_system.apply_verify_bonus(user_id)
            user_name = message.from_user.first_name or message.from_user.username or str(user_id)
            await message.reply_text(
                f"🎉 **Verification Successful!**\n"
                f"✅ **{user_name}** has successfully verified.\n\n"
                f"🎁 Your account has been upgraded, and you can now access **Rec 5** features!\n\n"
                f"📊 Check your updated limits using: /limit",
                reply_markup=build_main_keyboard(user_id)
            )
        else:
            await message.reply_text(
                "❌ Invalid or expired token. Send /start to get a new one.",
                reply_markup=build_main_keyboard(user_id)
            )
        return

    if limit_system.is_new_user(user_id):
        limit_system.get_user(user_id)
        await message.reply_text(
            limit_system.NEW_USER_WELCOME,
            reply_markup=build_main_keyboard(user_id)
        )

    if user_id in config.AUTH_USERS or verify.is_verified(user_id, config.OWNER_ID, config.AUTH_USERS):
        await message.reply_text(
            "🎬 **Welcome to Video Bot!**\n\n"
            "🎥 **Record:** `/rec http://link 00:00:00 Filename`\n"
            "📥 **Download:** `/download http://link Filename`\n"
            "🌐 **OTT/YouTube:** `/ott_download https://youtube.com/... Name`\n"
            "⏰ **Schedule:** `/schedule HH:MM URL 00:00:00 Filename`\n"
            "🗜 **Compress:** Reply to video + `/compress`\n"
            "📸 **Screenshots:** Reply to video + `/screenshot [1-30]`\n\n"
            f"📢 Channel: {config.CHANNEL_NAME}\n\n"
            "👇 Use the menu buttons below or type /help\n"
            "🌐 Language: /Hindi_or_English",
            reply_markup=build_main_keyboard(user_id)
        )
    else:
        token = verify.create_token(user_id)
        verify_url = f"https://t.me/{(await client.get_me()).username}?start=verify_{token}"
        short_url  = verify_url
        await message.reply_text(
            "🔒 **Access Restricted**\n\n"
            "This bot is private. To get **4 hours** of access, verify yourself:\n\n"
            f"👉 [Click here to verify]({short_url})\n\n"
            "_Or send_ `/verify {token}` _directly._",
            disable_web_page_preview=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  /verify
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("verify"))
async def verify_cmd(client, message: Message):
    user_id = message.from_user.id
    args    = message.command[1:]

    if user_id in config.OWNER_ID or user_id in config.AUTH_USERS:
        return await message.reply_text(
            "✅ **Aap Owner/Admin hain — verification ki zaroorat nahi!**\n\n"
            "Seedha /start use karein.",
            reply_markup=build_main_keyboard(user_id)
        )

    if args and len(args[0]) == 32:
        token = args[0]
        if verify.confirm_token(user_id, token):
            remaining = verify.time_remaining(user_id)
            ok, bonus_msg = limit_system.apply_verify_bonus(user_id)
            bonus_line = f"\n🎁 **Rec Bonus:** {bonus_msg}" if ok else ""
            await message.reply_text(
                f"✅ **Verified!** You have access for **{remaining}**."
                f"{bonus_line}\n\nType /start to use the bot.",
                reply_markup=build_main_keyboard(user_id)
            )
        else:
            await message.reply_text(
                "❌ **Invalid or expired token.**\n\nDobara /verify karein.",
                reply_markup=build_main_keyboard(user_id)
            )
        return

    user_data   = limit_system.get_user(user_id)
    verify_left = user_data.get("verify_left", 0)

    if verify_left <= 0:
        return await message.reply_text(
            "🚫 **ACCESS LOCKED (Limit 0)** 🚫\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "❌ Aapki aaj ki saari Verify aur Rec limit khatam ho gayi hai.\n\n"
            "🔄 Kal tak wait karein — system 12 ghante mein reset hoga.",
            reply_markup=build_main_keyboard(user_id)
        )

    token      = verify.create_token(user_id)
    bot_me     = await client.get_me()
    verify_url = f"https://t.me/{bot_me.username}?start=verify_{token}"
    url1 = shrink(verify_url)  or verify_url
    url2 = shrink2(verify_url) or verify_url

    next_step  = user_data.get("verify_done", 0)
    rec_reward = "+Rec 5" if next_step == 0 else ("Rec 4" if next_step == 1 else "Rec 3")

    await message.reply_text(
        "🔐 **Verification Required**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Aage bot ka istemal karne aur **{rec_reward}** ka quota unlock karne ke liye "
        "neeche diye gaye **kisi ek button** par click karke verification poora karein.\n\n"
        f"🆓 **Remaining Verify Chances:** {verify_left}\n\n"
        "⚠️ _Note: Verification poora karte hi aapki 'Verify Limit' chalu ho jayegi._\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Link 1 (ShortX)", url=url1),
                InlineKeyboardButton("✅ Link 2 (Shrinkme)", url=url2),
            ]
        ]),
        disable_web_page_preview=True
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /limit
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("limit"))
async def limit_cmd(client, message: Message):
    user_id = message.from_user.id
    if user_id in config.OWNER_ID or user_id in config.AUTH_USERS:
        await message.reply_text(
            "♾️ **Aapki Limit: UNLIMITED**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👑 **Owner / Admin** hain aap — koi bhi limit nahi hai!\n\n"
            "✅ Rec: **∞ Unlimited**\n"
            "✅ Download: **∞ Unlimited**\n"
            "✅ Verify: **Not required**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=build_main_keyboard(user_id)
        )
        return
    text = limit_system.format_limit_message(user_id)
    limit_system.mark_seen(user_id)
    await message.reply_text(text, reply_markup=build_main_keyboard(user_id), disable_web_page_preview=True)


# ─────────────────────────────────────────────────────────────────────────────
#  /setlimit  (owner only)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("setlimit") & filters.user(config.OWNER_ID))
async def setlimit_cmd(client, message: Message):
    args = message.command[1:]
    if len(args) < 2:
        return await message.reply_text(
            "❌ **Galat format!**\n\n"
            "📌 **Usage:**\n"
            "```\n/setlimit USER_ID 10\n/setlimit USER_ID +5\n/setlimit USER_ID -3\n```"
        )
    try:
        target_id = int(args[0])
        val_str   = args[1].strip()
    except (ValueError, IndexError):
        return await message.reply_text("❌ Invalid USER_ID.")
    try:
        if val_str.startswith("+"):
            limit_system.add_rec(target_id, int(val_str[1:]))
            action_text = f"➕ Added +{val_str[1:]} Rec"
        elif val_str.startswith("-"):
            limit_system.add_rec(target_id, -int(val_str[1:]))
            action_text = f"➖ Removed {val_str} Rec"
        else:
            limit_system.set_rec(target_id, int(val_str))
            action_text = f"🔧 Set to Rec {val_str}"
    except ValueError:
        return await message.reply_text("❌ Invalid value. Jaise: 10, +5, -3")
    new_rec = limit_system.get_user(target_id)["rec_limit"]
    await message.reply_text(
        f"✅ **Limit Updated!**\n\n"
        f"👤 **User ID:** `{target_id}`\n"
        f"🔧 **Action:** {action_text}\n"
        f"📊 **New Rec Limit:** Rec {new_rec}"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /grant_access  (owner only)
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("grant_access") & filters.user(config.OWNER_ID))
async def grant_access_cmd(client, message: Message):
    args = message.command[1:]
    if len(args) < 1:
        return await message.reply_text("Usage: `/grant_access USER_ID [HOURS]`\nDefault hours: 24")
    try:
        target_id = int(args[0])
        hours     = float(args[1]) if len(args) > 1 else 24
    except ValueError:
        return await message.reply_text("❌ Invalid user ID or hours.")

    verify.add_validity(target_id, int(hours * 3600))
    remaining = verify.time_remaining(target_id)
    await message.reply_text(
        f"✅ **Access granted!**\n\n"
        f"👤 User: `{target_id}`\n"
        f"⏳ Valid for: **{remaining}**"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /alive
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("alive"))
async def alive_cmd(client, message: Message):
    await message.reply_text(
        "✅ **Bot working, you can use it!**",
        reply_markup=build_main_keyboard(message.from_user.id)
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /help
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("help") & allowed)
async def help_cmd(client, message: Message):
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
        "• 📥 `/download` — Download full stream\n"
        "• 🌐 `/ott_download` — OTT/YouTube download\n"
        "• ⏰ `/schedule` — Pre-schedule a recording\n"
        "• 📋 `/schedules` — List pending schedules\n"
        "• 🗑 `/cancel_schedule` — Remove a schedule\n"
        "• 🗜 `/compress` — Compress video _(reply to video)_\n"
        "• 📸 `/screenshot [1-30]` — Screenshots _(reply to video)_\n"
        "• 🛑 `/cancel` — Stop active task\n"
        "• 📊 `/status` — All active tasks\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏰ **Scheduling:**\n"
        "```\n"
        "/schedule 21:00 http://link 01:30:00 ShowName\n"
        "/schedule 09:30 dl http://vod.m3u8 Morning\n"
        "/schedule 18:00 ott https://yt/... Film\n"
        "```\n\n"
        "🍪 **Cookies:**\n"
        "• `/cookies_add` — Upload cookies.txt\n"
        "• `/cookies_status` — Check cookie info\n"
        "• `/del_cookies` — Delete cookies\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🆕 **Features:**\n"
        "• 🎵 Multi audio track selection\n"
        "• 🖼 Watermark (5 positions)\n"
        "• 📐 Video size presets\n"
        "• ⏩ Auto mode: First+Last 1min _(rec only)_\n"
        "• 🔢 Up to **3 simultaneous** tasks\n\n"
        f"🔸 Default filename: `{config.DEFAULT_FILENAME}`",
        reply_markup=build_main_keyboard(user_id),
        disable_web_page_preview=True
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /status
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("status") & allowed)
async def status_cmd(client, message: Message):
    uid  = message.from_user.id
    jobs = user_status.get(uid, {})
    if not jobs:
        return await message.reply(
            "📭 No active recording tasks found.",
            reply_markup=build_main_keyboard(user_id)
        )
    lines = [f"📊 **Active Recordings ({len(jobs)}/{MAX_CONCURRENT})**\n"]
    for job_id, status in sorted(jobs.items()):
        n        = slot_number(job_id)
        emoji    = SLOT_EMOJI[n - 1]
        start_dt = datetime.fromtimestamp(status["id"], tz=tz).strftime("%I:%M:%S %p")
        target_s = time_to_seconds(status["target"]) if status["target"] != "∞" else 0
        prog_s   = time_to_seconds(status["progress"])
        remaining = max(target_s - prog_s, 0)
        eta      = TimeFormatter(remaining * 1000) if target_s else "—"
        lines.append(
            f"{emoji} **Slot {n}**\n"
            f"  📁 `{status['filename']}`\n"
            f"  ⏱ `{status['progress']}` / `{status['target']}`\n"
            f"  ⏳ ETA: `{eta}`  🕒 Started: `{start_dt}`\n"
        )
    lines.append("🛑 Use /cancel to stop a recording")
    await message.reply_text("\n".join(lines), reply_markup=build_main_keyboard(user_id))


# ─────────────────────────────────────────────────────────────────────────────
#  /history
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("history") & allowed)
async def history_cmd(client, message: Message):
    user_id  = message.from_user.id
    is_owner = user_id in config.OWNER_ID
    args     = message.command[1:]

    show_all   = "all"   in args
    show_stats = "stats" in args
    filter_u   = next((a for a in args if a.startswith("@")), None)

    if is_owner and (show_all or filter_u):
        entries = list(history_log)
    else:
        entries = [e for e in history_log if e["user_id"] == user_id]

    if filter_u:
        fname   = filter_u.lstrip("@").lower()
        entries = [e for e in entries if fname in (e.get("username") or "").lower()]

    if not entries:
        return await message.reply_text(
            "📭 **No history yet.**\n\nActivities appear here after recordings/downloads complete.",
            reply_markup=build_main_keyboard(user_id)
        )

    if show_stats:
        total  = len(history_log) if is_owner else len(entries)
        done   = sum(1 for e in entries if e["status"] == "done")
        canc   = sum(1 for e in entries if e["status"] == "cancelled")
        failed = sum(1 for e in entries if e["status"] == "failed")
        recs   = sum(1 for e in entries if e["type"] == "rec")
        dls    = sum(1 for e in entries if e["type"] == "download")
        otts   = sum(1 for e in entries if e["type"] == "ott")
        tot_dur = sum(e.get("duration_s", 0) for e in entries)
        tot_mb  = sum(e.get("size_mb", 0) for e in entries)

        user_block = ""
        if is_owner:
            from collections import Counter
            uc   = Counter(f"{e.get('username','?')} ({e['user_id']})" for e in history_log)
            top5 = uc.most_common(5)
            user_block = (
                "\n━━━━━━━━━━━━━━━━━━━━\n"
                "👤 **Top Users:**\n" +
                "\n".join(f"  {i+1}. `{u}` — {c} tasks" for i, (u, c) in enumerate(top5))
            )

        await message.reply_text(
            f"📊 **History Stats**{'  (Global)' if is_owner else ''}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **Total activities:** `{total}`\n"
            f"✅ **Completed:**        `{done}`\n"
            f"⚠️ **Cancelled:**        `{canc}`\n"
            f"❌ **Failed:**           `{failed}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎥 **Recordings:**  `{recs}`\n"
            f"📥 **Downloads:**   `{dls}`\n"
            f"🌐 **OTT/YouTube:** `{otts}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ **Total duration:** `{TimeFormatter(tot_dur * 1000)}`\n"
            f"💾 **Total size:**    `{tot_mb:.1f} MB`"
            f"{user_block}",
            reply_markup=build_main_keyboard(user_id)
        )
        return

    limit  = len(entries) if show_all else min(15, len(entries))
    recent = entries[-limit:][::-1]

    TYPE_EMOJI   = {"rec": "🎥", "download": "📥", "ott": "🌐"}
    STATUS_EMOJI = {"done": "✅", "cancelled": "⚠️", "failed": "❌"}

    lines = [f"📋 **Activity History** ({'Global · ' if is_owner and show_all else ''}last {len(recent)})\n"]
    for e in recent:
        dt      = datetime.fromtimestamp(e["ts"], tz).strftime("%d %b %I:%M %p")
        t_emoji = TYPE_EMOJI.get(e["type"], "📁")
        s_emoji = STATUS_EMOJI.get(e["status"], "❓")
        dur_str = TimeFormatter(e.get("duration_s", 0) * 1000) if e.get("duration_s") else "—"
        mb_str  = f"{e['size_mb']} MB" if e.get("size_mb") else "—"
        user_tag = f" · `@{e['username']}`" if is_owner else ""
        extra = ""
        if e["type"] == "ott" and e.get("res_label"):
            extra = f" · `{e['res_label']}` `{e.get('audio_label','')}`"
        lines.append(
            f"{t_emoji}{s_emoji} **{e['filename']}**{user_tag}\n"
            f"   ⏱ `{dur_str}` · 💾 `{mb_str}` · 🕒 `{dt}`{extra}\n"
        )

    if len(entries) > limit:
        lines.append(f"\n_…{len(entries) - limit} more. Use /history all to see everything._")

    lines.append("\n📊 /history stats — aggregated totals")
    await message.reply_text("\n".join(lines), reply_markup=build_main_keyboard(user_id))


# ─────────────────────────────────────────────────────────────────────────────
#  /recording_old  — resend last completed recording
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("recording_old") & allowed)
async def recording_old_cmd(client, message: Message):
    user_id = message.from_user.id
    cached  = recording_cache.get(user_id)

    if not cached:
        return await message.reply_text(
            "📭 **Koi purani recording nahi mili!**\n\n"
            "Pehle `/rec`, `/download` ya `/ott_download` se koi recording complete karein.",
            reply_markup=build_main_keyboard(user_id)
        )

    age_s    = time.time() - cached["ts"]
    if age_s > _RECORDING_OLD_EXPIRY:
        recording_cache.pop(user_id, None)
        return await message.reply_text(
            "⏳ **Recording Expire Ho Gayi!**\n\n"
            "Aapki last recording 2 ghante pehle upload hui thi — ab uska link available nahi.\n\n"
            "Dobara record karne ke liye `/rec` ya `/download` use karein.",
            reply_markup=build_main_keyboard(user_id)
        )

    remaining_s  = int(_RECORDING_OLD_EXPIRY - age_s)
    rem_min, rem_sec = divmod(remaining_s, 60)
    rem_h,   rem_min = divmod(rem_min, 60)
    if rem_h:
        rem_str = f"{rem_h}h {rem_min}m"
    elif rem_min:
        rem_str = f"{rem_min}m {rem_sec}s"
    else:
        rem_str = f"{rem_sec}s"

    TYPE_EMOJI = {"rec": "🎥", "download": "📥", "ott": "🌐"}
    t_emoji    = TYPE_EMOJI.get(cached.get("type", "rec"), "📁")

    wait_msg = await message.reply_text(
        f"{t_emoji} **Aapki Recording Bhej Raha Hoon...**\n\n"
        f"📁 `{cached['filename']}`\n"
        f"⏳ Link valid hai: `{rem_str}` aur",
    )

    try:
        await client.forward_messages(
            chat_id=message.chat.id,
            from_chat_id=cached["chat_id"],
            message_ids=cached["msg_id"],
        )
        await wait_msg.delete()
    except Exception as e:
        LOG.warning(f"recording_old forward failed: {e}")
        try:
            await wait_msg.edit_text(
                "❌ **Forward Fail!**\n\n"
                "Original message mil nahi raha — shayad delete ho gaya.\n\n"
                f"`{str(e)[:500]}`",
                reply_markup=build_main_keyboard(user_id)
            )
        except Exception:
            pass
        recording_cache.pop(user_id, None)
