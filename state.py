import logging
from typing import Dict, List
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
import pytz
import config
import verify
import limit_system

tz = pytz.timezone(config.TIMEZONE)


def _tz_time(*args):
    return datetime.now(tz).timetuple()


logging.Formatter.converter = _tz_time
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%d-%m-%Y %I:%M:%S %p " + tz.tzname(datetime.now()),
)
LOG = logging.getLogger("ott_bot")

app = Client(
    "recorder",
    bot_token=config.BOT_TOKEN,
    api_id=config.API_ID,
    api_hash=config.API_HASH,
)

# ── Remove reply-quoting from ALL bot messages ────────────────────────────────
_orig_reply_text = Message.reply_text


async def _reply_no_quote(self, text, quote: bool = False, **kw):
    return await _orig_reply_text(self, text, quote=quote, **kw)


Message.reply_text = _reply_no_quote  # type: ignore[method-assign]


# ── Allowed filter ─────────────────────────────────────────────────────────────
def _is_allowed(_, __, message) -> bool:
    uid = message.from_user.id if message.from_user else None
    if uid is None:
        return False
    if uid in config.OWNER_ID or uid in config.AUTH_USERS:
        return True
    if verify.is_verified(uid, config.OWNER_ID, config.AUTH_USERS):
        return True
    try:
        user_data = limit_system.get_user(uid)
        if user_data.get("rec_limit", 0) > 0:
            return True
    except Exception:
        pass
    return False


allowed = filters.create(_is_allowed)

# ── Shared state dicts ────────────────────────────────────────────────────────
user_tasks:       Dict[int, Dict[str, float]] = {}
user_status:      Dict[int, Dict[str, dict]]  = {}
user_ffmpeg_pids: Dict[int, Dict[str, int]]   = {}
progress_tasks:   Dict[int, Dict[str, object]] = {}
cancelled_jobs:   set = set()
scheduled_jobs:   Dict[int, Dict[str, dict]]  = {}
_sch_counter:     Dict[int, int]              = {}
history_log:      List[dict]                  = []
user_setup:       Dict[int, dict]             = {}
compress_pending: Dict[int, int]              = {}
recording_cache:  Dict[int, dict]             = {}   # last successful upload per user
