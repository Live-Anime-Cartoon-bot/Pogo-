import json
import random
import logging
import requests as req

logger = logging.getLogger(__name__)

SHORTENERS_FILE = "shorteners.json"
MAX_SHORTENERS = 5

PROVIDER_APIS = {
    "shrinkme.io": "https://shrinkme.io/api?api={api}&url={url}",
    "shortxlinks.in": "https://shortxlinks.in/api?api={api}&url={url}",
}


def _load() -> list:
    try:
        with open(SHORTENERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save(data: list):
    with open(SHORTENERS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_all() -> list:
    return _load()


def add_shortener(provider: str, api_key: str) -> tuple:
    data = _load()
    if len(data) >= MAX_SHORTENERS:
        return False, f"Maximum {MAX_SHORTENERS} shorteners allowed."
    for s in data:
        if s["provider"] == provider:
            return False, f"`{provider}` is already connected."
    if provider not in PROVIDER_APIS:
        return False, f"Unsupported provider. Supported: {', '.join(PROVIDER_APIS.keys())}"
    data.append({"provider": provider, "api": api_key})
    _save(data)
    return True, f"`{provider}` connected successfully."


def delete_shortener(provider: str) -> tuple:
    data = _load()
    new_data = [s for s in data if s["provider"] != provider]
    if len(new_data) == len(data):
        return False, f"`{provider}` not found."
    _save(new_data)
    return True, f"`{provider}` deleted successfully."


def shorten_url(long_url: str) -> str:
    data = _load()
    if not data:
        return long_url
    shortener = random.choice(data)
    provider = shortener["provider"]
    api_key = shortener["api"]
    try:
        api_url = PROVIDER_APIS[provider].format(api=api_key, url=long_url)
        resp = req.get(api_url, timeout=10)
        result = resp.json()
        if result.get("status") == "success":
            return result.get("shortenedUrl", long_url)
    except Exception as e:
        logger.error(f"Shortener error ({provider}): {e}")
    return long_url


def shorteners_text() -> str:
    data = _load()
    if not data:
        return "No shorteners connected yet."
    lines = []
    for i, s in enumerate(data, 1):
        lines.append(f"{i}. **{s['provider']}**")
    return "\n".join(lines)
