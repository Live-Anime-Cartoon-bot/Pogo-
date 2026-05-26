import json
import shlex
import asyncio
import time
from typing import Tuple, Optional
from urllib.parse import urlparse

import config
from constants import LANG_MAP, LANG_FULL
from state import LOG, history_log, user_tasks
from constants import MAX_HISTORY

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def make_job_key(user_id: int, job_id: str) -> str:
    return f"{user_id}:{job_id}"


def next_job_id(user_id: int) -> Optional[str]:
    used = set(user_tasks.get(user_id, {}).keys())
    for slot in ["slot1", "slot2", "slot3"]:
        if slot not in used:
            return slot
    return None


def slot_number(job_id: str) -> int:
    return int(job_id.replace("slot", ""))


async def runcmd(cmd: str, timeout: int = 120) -> Tuple[int, str, str]:
    args = shlex.split(cmd)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return -1, "", f"Command timed out after {timeout}s"
    return (
        process.returncode,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


def time_to_seconds(time_str: str) -> int:
    try:
        h, m, s = time_str.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return 0


def TimeFormatter(milliseconds: int) -> str:
    seconds, _ = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, min_ = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02}:{min_:02}:{sec:02}"
    return f"{min_:02}:{sec:02}"


async def get_duration_ffmpeg(input_file: str) -> int:
    try:
        cmd = (
            f'ffprobe -v error -show_entries format=duration '
            f'-of default=noprint_wrappers=1:nokey=1 "{input_file}"'
        )
        retcode, out, _ = await runcmd(cmd)
        if retcode == 0:
            return int(float(out.strip()))
    except Exception as e:
        LOG.warning(f"FFprobe duration failed: {e}")
    return 0


def _add_history(entry: dict):
    entry.setdefault("ts", time.time())
    history_log.append(entry)
    if len(history_log) > MAX_HISTORY:
        del history_log[0]


def build_metadata_args(tracks: list, selected_tracks: set, channel_name: str) -> str:
    if not channel_name or not tracks or not selected_tracks:
        return ""
    selected = [t for t in tracks if t["index"] in selected_tracks]
    parts = []
    for out_idx, track in enumerate(selected):
        lang  = track.get("language", "")
        iso   = lang[:3] if lang else ""
        label = LANG_FULL.get(lang, track.get("display", f"Audio {out_idx + 1}"))
        title = f"{channel_name} {label}".strip()
        safe  = title.replace('"', '\\"')
        parts += [
            f'-metadata:s:a:{out_idx} title="{safe}"',
            f'-metadata:s:a:{out_idx} handler_name="{safe}"',
        ]
        if iso:
            parts.append(f'-metadata:s:a:{out_idx} language={iso}')
    return " ".join(parts)


def http_opts(url: str) -> str:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    return (
        f'-user_agent "{_UA}" '
        f'-headers "Referer: {origin}\\r\\n"'
    )


def get_video_media(msg):
    if not msg:
        return None
    return msg.video or msg.document or None


async def detect_stream_info(url: str) -> dict:
    cmd = (
        f'ffprobe -v quiet -timeout 15000000 {http_opts(url)} -print_format json '
        f'-show_streams "{url}"'
    )
    retcode, out, _ = await runcmd(cmd, timeout=25)
    result = {"video": None, "tracks": []}
    if retcode != 0 or not out.strip():
        return result
    try:
        streams   = json.loads(out).get("streams", [])
        audio_idx = 0
        for s in streams:
            ctype = s.get("codec_type", "")
            if ctype == "video" and result["video"] is None:
                w   = s.get("width",  0)
                h   = s.get("height", 0)
                fps_raw = s.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_raw.split("/")
                    fps = round(int(num) / int(den), 2) if int(den) else 0
                except Exception:
                    fps = 0
                br = int(s.get("bit_rate", 0) or 0) // 1000
                result["video"] = {
                    "width": w, "height": h,
                    "codec": s.get("codec_name", "").upper(),
                    "bitrate_kbps": br, "fps": fps,
                }
            elif ctype == "audio":
                lang_tag = (
                    s.get("tags", {}).get("language", "")
                    or s.get("tags", {}).get("LANGUAGE", "")
                ).lower()
                codec   = s.get("codec_name", "audio").upper()
                display = LANG_MAP.get(lang_tag, lang_tag.upper() if lang_tag else f"Track {audio_idx + 1}")
                result["tracks"].append({
                    "index":        audio_idx,
                    "stream_index": s.get("index", audio_idx),
                    "language":     lang_tag,
                    "codec":        codec,
                    "label":        f"{display} ({codec})",
                    "display":      display,
                })
                audio_idx += 1
    except Exception as e:
        LOG.warning(f"Stream info parse error: {e}")
    return result


def format_quality_line(video: dict | None) -> str:
    if not video or not video.get("width"):
        return "Unknown"
    parts = [f"{video['width']}×{video['height']}"]
    if video.get("codec"):
        parts.append(video["codec"])
    if video.get("bitrate_kbps"):
        parts.append(f"{video['bitrate_kbps']}kbps")
    if video.get("fps"):
        parts.append(f"{video['fps']}fps")
    return " | ".join(parts)
