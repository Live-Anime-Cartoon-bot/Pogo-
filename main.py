import asyncio
import platform
import psutil
from datetime import datetime

import pytz

import config
import limit_system
from state import app, LOG

# ── Import all handler modules so decorators register with `app` ───────────────
import handlers.commands    # noqa: F401
import handlers.schedule    # noqa: F401
import handlers.record      # noqa: F401
import handlers.router      # noqa: F401
import handlers.playlist    # noqa: F401
import handlers.cookies     # noqa: F401
import handlers.ott         # noqa: F401
import handlers.compress    # noqa: F401
import handlers.screenshot  # noqa: F401

from pyrogram import idle


# ─────────────────────────────────────────────────────────────────────────────
#  Daily limit refresh loop
# ─────────────────────────────────────────────────────────────────────────────

async def _daily_refresh_loop():
    while True:
        await asyncio.sleep(12 * 3600)
        limit_system.daily_refresh_all()
        LOG.info("✅ 12-hour rec limit refresh complete.")


# ─────────────────────────────────────────────────────────────────────────────
#  Startup message to owners
# ─────────────────────────────────────────────────────────────────────────────

async def send_startup_message(client):
    _tz     = pytz.timezone(config.TIMEZONE)
    now_str = datetime.now(_tz).strftime("%d %b %Y  %I:%M:%S %p")
    try:
        cpu       = psutil.cpu_percent(interval=1)
        ram       = psutil.virtual_memory()
        disk      = psutil.disk_usage("/")
        ram_used  = f"{ram.used  / (1024**3):.1f} GB"
        ram_total = f"{ram.total / (1024**3):.1f} GB"
        disk_free = f"{disk.free / (1024**3):.1f} GB"
    except Exception:
        cpu = ram_used = ram_total = disk_free = "N/A"
    try:
        users_count = len(limit_system._load())
    except Exception:
        users_count = 0

    text = (
        "🟢 **Bot is Now Online!**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 **Bot:** OTT Recorder Bot\n"
        f"🕒 **Started At:** `{now_str}`\n"
        f"🌍 **Timezone:** `{config.TIMEZONE}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  🖥 CPU Usage   : `{cpu}%`\n"
        f"  🧠 RAM Used    : `{ram_used}` / `{ram_total}`\n"
        f"  💾 Disk Free   : `{disk_free}`\n"
        f"  🐍 Python      : `{platform.python_version()}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  👥 Auth Users  : `{len(config.AUTH_USERS)}`\n"
        f"  👤 Total Users : `{users_count}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ All systems running normally."
    )
    for owner_id in config.OWNER_ID:
        try:
            await client.send_message(owner_id, text)
        except Exception as e:
            LOG.warning(f"Startup msg fail (owner {owner_id}): {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🎬 Starting Video Recorder Bot...")
    print("✅ Bot is now running!")
    app.start()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_startup_message(app))
    loop.create_task(_daily_refresh_loop())
    print("⏰ 12-hour auto-refresh scheduled.")
    print("🤖 OTT Recorder Bot is Live!")
    idle()
