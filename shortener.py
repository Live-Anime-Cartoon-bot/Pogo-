from typing import Optional

_SHORTX_API   = "65aa5be4d757fb7242fff9dde00f6cd5d4acc977"
_SHRINKME_API = "9503d9bf87c90aa9e0aab35d4dec7d1ce24c0a23"


def shrink(long_url: str) -> Optional[str]:
    """shortxlinks.in se short link banao."""
    import requests as _req
    try:
        resp   = _req.get(
            f"https://shortxlinks.in/api?api={_SHORTX_API}&url={long_url}",
            timeout=10,
        )
        result = resp.json()
        if result.get("status") == "success":
            short = result.get("shortenedUrl", "")
            if short:
                return short
    except Exception:
        pass
    return None


def shrink2(long_url: str) -> Optional[str]:
    """shrinkme.io se short link banao."""
    import requests as _req
    try:
        resp   = _req.get(
            f"https://shrinkme.io/api?api={_SHRINKME_API}&url={long_url}",
            timeout=10,
        )
        result = resp.json()
        if result.get("status") == "success":
            short = result.get("shortenedUrl", "")
            if short:
                return short
    except Exception:
        pass
    return None
