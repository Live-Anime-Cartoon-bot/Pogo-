import asyncio
import psutil
from datetime import datetime, timedelta
from pyrogram import filters, Client
from pyrogram.types import Message

import config
from state import (
    app, allowed, LOG, tz,
    user_tasks, user_status, user_ffmpeg_pids, progress_tasks,
    cancelled_jobs, scheduled_jobs, _sch_counter, user_setup,
)
from constants import SLOT_EMOJI
from utils import make_job_key, slot_number
from keyboards import build_main_keyboard, build_cancel_keyboard


# ─────────────────────────────────────────────────────────────────────────────
#  Schedule helpers
# ─────────────────────────────────────────────────────────────────────────────

def _next_sch_id(user_id: int) -> str:
    _sch_counter[user_id] = _sch_counter.get(user_id, 0) + 1
    return f"S{_sch_counter[user_id]}"


def _parse_schedule_time(time_str: str):
    now = datetime.now(tz)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t      = datetime.strptime(time_str, fmt)
            target = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target
        except ValueError:
            continue
    return None


def _format_wait(seconds: float) -> str:
    seconds = int(seconds)
    h, rem  = divmod(seconds, 3600)
    m, s    = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


async def _schedule_waiter(client: Client, user_id: int, chat_id: int,
                            sch_id: str, job: dict):
    now    = datetime.now(tz)
    wait_s = max((job["target_dt"] - now).total_seconds(), 0)
    await asyncio.sleep(wait_s)

    if sch_id not in scheduled_jobs.get(user_id, {}):
        return

    scheduled_jobs.get(user_id, {}).pop(sch_id, None)

    kind     = job["kind"]
    url      = job["url"]
    filename = job["filename"]
    duration = job.get("duration", "")

    fire_time = datetime.now(tz).strftime("%I:%M:%S %p")

    await client.send_message(
        chat_id,
        f"⏰ **Schedule {sch_id} Fired!**\n\n"
        f"🕒 **Time:** `{fire_time} IST`\n"
        f"📁 **File:** `{filename}`\n"
        f"🔗 **URL:** `{url[:60]}{'…' if len(url) > 60 else ''}`\n\n"
        f"🚀 Starting `/{kind}` now…",
        reply_markup=build_main_keyboard(user_id),
    )

    cmd_text = {
        "rec":          f"/rec {url} {duration} {filename}",
        "download":     f"/download {url} {filename}",
        "ott_download": f"/ott_download {url} {filename}",
    }.get(kind, f"/rec {url} {duration} {filename}")

    await client.send_message(chat_id, cmd_text)


# ─────────────────────────────────────────────────────────────────────────────
#  /schedule
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("schedule") & allowed)
async def schedule_cmd(client: Client, message: Message):
    args    = message.command[1:]
    user_id = message.from_user.id

    def _usage():
        return message.reply_text(
            "❌ **Invalid format.**\n\n"
            "📌 **Usage:**\n"
            "```\n"
            "/schedule HH:MM URL 00:00:00 Filename\n"
            "/schedule HH:MM dl URL Filename\n"
            "/schedule HH:MM ott https://... Filename\n"
            "```\n\n"
            "Examples:\n"
            "• `/schedule 21:00 http://stream 01:30:00 NightShow`\n"
            "• `/schedule 09:30 dl http://vod.m3u8 Morning`\n"
            "• `/schedule 18:00 ott https://youtube.com/... Movie`",
            reply_markup=build_main_keyboard(user_id),
            disable_web_page_preview=True,
        )

    if len(args) < 3:
        return await _usage()

    time_str  = args[0]
    target_dt = _parse_schedule_time(time_str)
    if not target_dt:
        return await message.reply_text(
            "❌ Invalid time format. Use **HH:MM** or **HH:MM:SS** (24-hour IST).",
            reply_markup=build_main_keyboard(user_id)
        )

    kind = "rec"
    rest = args[1:]
    if rest[0].lower() in ("dl", "download"):
        kind = "download"
        rest = rest[1:]
    elif rest[0].lower() in ("ott", "ott_download"):
        kind = "ott_download"
        rest = rest[1:]

    if not rest:
        return await _usage()

    url  = rest[0]
    rest = rest[1:]

    duration = ""
    if kind == "rec":
        if not rest:
            return await _usage()
        if rest[0].count(":") >= 1:
            duration = rest[0]
            rest     = rest[1:]
        else:
            return await _usage()

    filename = " ".join(rest).strip() if rest else config.DEFAULT_FILENAME

    sch_id = _next_sch_id(user_id)
    job = {
        "kind":      kind,
        "url":       url,
        "filename":  filename,
        "duration":  duration,
        "time_str":  time_str,
        "target_dt": target_dt,
    }

    scheduled_jobs.setdefault(user_id, {})[sch_id] = job
    job["task"] = asyncio.create_task(
        _schedule_waiter(client, user_id, message.chat.id, sch_id, job)
    )

    wait_s     = (target_dt - datetime.now(tz)).total_seconds()
    fire_label = target_dt.strftime("%I:%M %p")
    day_label  = "today" if target_dt.date() == datetime.now(tz).date() else "tomorrow"
    kind_emoji = {"rec": "🎥", "download": "📥", "ott_download": "🌐"}.get(kind, "🎥")
    dur_line   = f"⏱ **Duration:** `{duration}`\n" if duration else ""

    await message.reply_text(
        f"✅ **Schedule {sch_id} Created!**\n\n"
        f"{kind_emoji} **Type:** `/{kind}`\n"
        f"🕒 **Fire at:** `{fire_label} IST` ({day_label})\n"
        f"⏳ **In:** `{_format_wait(wait_s)}`\n"
        f"{dur_line}"
        f"📁 **File:** `{filename}`\n"
        f"🔗 **URL:** `{url[:60]}{'…' if len(url) > 60 else ''}`\n\n"
        f"📋 Use /schedules to see all · /cancel_schedule {sch_id} to remove",
        reply_markup=build_main_keyboard(user_id),
        disable_web_page_preview=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /schedules
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("schedules") & allowed)
async def schedules_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    jobs    = scheduled_jobs.get(user_id, {})
    if not jobs:
        return await message.reply_text(
            "📭 **No pending schedules.**\n\nUse /schedule to create one.",
            reply_markup=build_main_keyboard(user_id)
        )

    now        = datetime.now(tz)
    lines      = [f"📋 **Pending Schedules ({len(jobs)})**\n"]
    kind_emoji = {"rec": "🎥", "download": "📥", "ott_download": "🌐"}

    for sid, job in sorted(jobs.items()):
        wait_s    = max((job["target_dt"] - now).total_seconds(), 0)
        fire_time = job["target_dt"].strftime("%I:%M %p")
        day_label = "today" if job["target_dt"].date() == now.date() else "tomorrow"
        k_emoji   = kind_emoji.get(job["kind"], "🎥")
        dur_part  = f" · `{job['duration']}`" if job.get("duration") else ""
        lines.append(
            f"{k_emoji} **{sid}** — fires `{fire_time}` {day_label} _(in {_format_wait(wait_s)})_\n"
            f"   📁 `{job['filename']}`{dur_part}\n"
            f"   🔗 `{job['url'][:50]}{'…' if len(job['url']) > 50 else ''}`\n"
        )

    lines.append("🗑 /cancel_schedule <ID> to remove one")
    await message.reply_text("\n".join(lines), reply_markup=build_main_keyboard(user_id))


# ─────────────────────────────────────────────────────────────────────────────
#  /cancel_schedule
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("cancel_schedule") & allowed)
async def cancel_schedule_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args    = message.command[1:]

    if not args:
        jobs = scheduled_jobs.get(user_id, {})
        if not jobs:
            return await message.reply_text(
                "📭 No pending schedules to cancel.",
                reply_markup=build_main_keyboard(user_id)
            )
        ids = ", ".join(sorted(jobs.keys()))
        return await message.reply_text(
            f"❓ **Which schedule to cancel?**\n\n"
            f"Pending: `{ids}`\n\n"
            f"Usage: `/cancel_schedule S1`",
            reply_markup=build_main_keyboard(user_id)
        )

    sch_id  = args[0].upper()
    user_js = scheduled_jobs.get(user_id, {})

    if sch_id not in user_js:
        return await message.reply_text(
            f"❌ Schedule `{sch_id}` not found.\n"
            f"Use /schedules to see pending ones.",
            reply_markup=build_main_keyboard(user_id)
        )

    job  = user_js.pop(sch_id)
    task = job.get("task")
    if task and not task.done():
        task.cancel()

    await message.reply_text(
        f"✅ **Schedule {sch_id} cancelled.**\n\n"
        f"📁 `{job['filename']}` @ `{job['time_str']} IST`",
        reply_markup=build_main_keyboard(user_id)
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /cancel
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("cancel") & allowed)
async def cancel_command(client, message: Message):
    user_id = message.from_user.id

    if user_id in user_setup:
        user_setup.pop(user_id, None)
        return await message.reply_text(
            "❌ **Setup cancelled.**",
            reply_markup=build_main_keyboard(user_id)
        )

    jobs = user_tasks.get(user_id, {})
    if not jobs:
        return await message.reply_text(
            "❌ **No active recording to cancel!**",
            reply_markup=build_main_keyboard(user_id)
        )

    if len(jobs) == 1:
        job_id = list(jobs.keys())[0]
        await do_cancel_job(user_id, job_id, message)
        await message.reply_text("✅ Done.", reply_markup=build_main_keyboard(user_id))
    else:
        user_setup.setdefault(user_id, {})["step"] = "cancel"
        await message.reply_text(
            f"📋 **You have {len(jobs)} active recordings.**\nWhich one to cancel?",
            reply_markup=build_cancel_keyboard(user_id)
        )


async def do_cancel_job(user_id: int, job_id: str, ref_message: Message):
    job_key = make_job_key(user_id, job_id)
    cancelled_jobs.add(job_key)

    if user_id in progress_tasks and job_id in progress_tasks[user_id]:
        progress_tasks[user_id][job_id].cancel()
        del progress_tasks[user_id][job_id]

    if user_id in user_ffmpeg_pids and job_id in user_ffmpeg_pids[user_id]:
        pid = user_ffmpeg_pids[user_id][job_id]
        try:
            parent   = psutil.Process(pid)
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

    info     = user_status.get(user_id, {}).get(job_id, {})
    filename = info.get("filename", "Unknown")
    n        = slot_number(job_id)
    emoji    = SLOT_EMOJI[n - 1]

    await ref_message.reply_text(
        f"✅ **Recording Cancelled!**\n\n"
        f"{emoji} **Slot {n}:** `{filename}`\n"
        f"🛑 Stopped — uploading recorded portion..."
    )
