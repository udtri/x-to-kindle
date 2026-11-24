"""Fetch an X post or Article and return raw JSON from a known source."""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

USER_AGENT = "x2kindle/1.0 (+https://x.com; personal ebook converter)"
TIMEOUT = 30

# Status / Article IDs are snowflake integers.
STATUS_RE = re.compile(
    r"""(?x)
    (?:https?://)?
    (?:(?:www|mobile|pro)\.)?
    (?:x|twitter)\.com/
    (?:
        i/web/status/(?P<web_id>\d+)
        | i/status/(?P<istatus_id>\d+)
        | i/article/(?P<article_id>\d+)
        | (?P<user>[A-Za-z0-9_]+)/status/(?P<status_id>\d+)
    )
    """,
)
BARE_ID_RE = re.compile(r"^\d{5,25}$")


class FetchError(RuntimeError):
    """Raised when a post cannot be fetched or identified."""


def parse_input(value: str) -> dict[str, str]:
    """Parse a URL or numeric ID into {kind, id, url?}."""
    value = value.strip()
    m = STATUS_RE.search(value)
    if m:
        if m.group("article_id"):
            aid = m.group("article_id")
            return {
                "kind": "article",
                "id": aid,
                "url": f"https://x.com/i/article/{aid}",
            }
        sid = m.group("status_id") or m.group("web_id") or m.group("istatus_id")
        user = m.group("user") or "i"
        return {
            "kind": "status",
            "id": sid,
            "url": f"https://x.com/{user}/status/{sid}",
        }
    if BARE_ID_RE.match(value):
        return {"kind": "status", "id": value, "url": f"https://x.com/i/status/{value}"}
    raise FetchError(
        f"Could not parse an X post or Article URL/ID from: {value!r}"
    )


def _http_get(url: str, accept: str = "*/*") -> tuple[int, bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
        method="GET",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        return e.code, body, e.headers.get("Content-Type", "") if e.headers else ""


def _extract_json(payload: str) -> Any:
    """Parse a JSON object, including Jina-wrapped 'Markdown Content:' bodies."""
    text = payload.strip()
    if text.startswith("{") or text.startswith("["):
        return json.loads(text)
    marker = "Markdown Content:"
    idx = text.find(marker)
    if idx >= 0:
        rest = text[idx + len(marker) :].strip()
        # Jina sometimes wraps JSON in a fenced block.
        rest = re.sub(r"^```(?:json)?\s*", "", rest)
        rest = re.sub(r"\s*```\s*$", "", rest)
        start = rest.find("{")
        if start >= 0:
            data, _ = json.JSONDecoder().raw_decode(rest[start:])
            return data
    start = text.find("{")
    if start >= 0:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
        return data
    raise FetchError("Response was not JSON")


def fetch_fxtwitter(status_id: str) -> dict[str, Any]:
    """Fetch a post from api.fxtwitter.com, falling back through Jina if blocked."""
    direct = f"https://api.fxtwitter.com/status/{status_id}"
    proxied = f"https://r.jina.ai/{quote(direct, safe=':/')}"
    last_error: Exception | None = None
    for url, label in ((direct, "fxtwitter"), (proxied, "jina+fxtwitter")):
        try:
            status, body, _ctype = _http_get(url, accept="application/json, text/plain, */*")
            if status != 200:
                last_error = FetchError(f"{label} HTTP {status}")
                continue
            text = body.decode("utf-8", errors="replace")
            if "Just a moment" in text or "cf-mitigated" in text.lower():
                last_error = FetchError(f"{label} returned a Cloudflare challenge")
                continue
            data = _extract_json(text)
            tweet = data.get("tweet") if isinstance(data, dict) else None
            if not isinstance(tweet, dict):
                # Some envelopes nest further.
                if isinstance(data, dict) and data.get("code") and not tweet:
                    last_error = FetchError(
                        f"{label} error: {data.get('message') or data.get('code')}"
                    )
                    continue
                last_error = FetchError(f"{label} response had no tweet object")
                continue
            return {"source": label, "data": data}
        except (FetchError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            continue
    raise FetchError(
        f"Could not fetch post {status_id} from fxtwitter "
        f"({last_error})"
    )


def fetch_article_page(article_id: str) -> dict[str, Any] | None:
    """Best-effort fetch of an /i/article/ URL. Returns None if it is unusable."""
    urls = [
        f"https://api.fxtwitter.com/i/article/{article_id}",
        f"https://r.jina.ai/https://api.fxtwitter.com/i/article/{article_id}",
        f"https://r.jina.ai/https://x.com/i/article/{article_id}",
    ]
    for url in urls:
        try:
            status, body, _ = _http_get(url)
            if status != 200:
                continue
            text = body.decode("utf-8", errors="replace")
            if "Just a moment" in text:
                continue
            try:
                data = _extract_json(text)
            except (FetchError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and (data.get("tweet") or data.get("article") or data.get("data")):
                return {"source": "article-url", "data": data}
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
    return None


def load_json_file(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise FetchError("JSON file must contain an object")
    # Already in the fetch() envelope: {source, data, requested?}.
    if data.get("source") and "data" in data and "tweet" not in data:
        return data
    return {"source": "json-file", "data": data}


def fetch_post(value: str, from_json: str | None = None) -> dict[str, Any]:
    """Fetch a post from --from-json, an X URL/ID, or a saved fixture."""
    if from_json:
        parsed = load_json_file(from_json)
        # Still record the requested URL when provided.
        try:
            parsed["requested"] = parse_input(value) if value else {}
        except FetchError:
            parsed["requested"] = {"raw": value}
        return parsed

    ident = parse_input(value)
    if ident["kind"] == "article":
        # /i/article/{id} is not a post ID. Try article endpoints, then
        # tell the caller we need a status URL if that fails.
        result = fetch_article_page(ident["id"])
        if result:
            result["requested"] = ident
            return result
        raise FetchError(
            f"{ident['url']} is an Article URL, not a post URL. "
            "The X post lookup API does not accept Article IDs. "
            "Pass the wrapping post instead, e.g. https://x.com/user/status/123, "
            "or fetch it with the X tools and pass --from-json."
        )

    result = fetch_fxtwitter(ident["id"])
    result["requested"] = ident
    return result


def download_bytes(url: str, max_bytes: int = 15 * 1024 * 1024) -> bytes | None:
    """Download an image (or small file). Returns None on failure."""
    if not url:
        return None
    try:
        status, body, _ = _http_get(url)
        if status != 200 or not body:
            return None
        if len(body) > max_bytes:
            return None
        return body
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


# Silence unused import warning if callers want a polite pause between images.
_ = time
