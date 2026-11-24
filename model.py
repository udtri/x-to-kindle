"""Normalize official X API and fxtwitter payloads into a Document."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

USER_TZ = ZoneInfo("America/Los_Angeles")


def looks_like_heading(part: str) -> bool:
    """Conservative heading detector for plain-text bodies (official API / long posts)."""
    s = part.strip()
    if not s:
        return False
    if s.startswith(("# ", "## ", "### ")):
        return True
    # "I – Title" / "VII – Title"
    if re.match(r"^[IVXLCDM]{1,8}\s+[–—-]\s+\S", s) and len(s) <= 180:
        return True
    letters = re.sub(r"[^A-Za-z]", "", s)
    # Short ALL-CAPS line, not a typical sentence.
    if 3 <= len(s) <= 80 and letters and letters.isupper() and " " in s:
        return True
    return False



@dataclass
class Image:
    media_id: str
    url: str
    alt: str = ""
    kind: str = "photo"  # photo | video | other
    poster_url: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class Block:
    type: str  # p, h2, h3, li-ul, li-ol, quote, image, hr, video
    text_html: str = ""
    image: Image | None = None


@dataclass
class Document:
    post_id: str
    url: str
    title: str
    author_name: str
    author_handle: str
    created_at: datetime | None
    kind: str  # article | long_post | post
    source: str
    blocks: list[Block] = field(default_factory=list)
    cover: Image | None = None
    raw_text: str = ""

    @property
    def author_label(self) -> str:
        if self.author_name and self.author_handle:
            return f"{self.author_name} (@{self.author_handle})"
        return self.author_name or (f"@{self.author_handle}" if self.author_handle else "Unknown")

    def display_date(self) -> str:
        if not self.created_at:
            return ""
        local = self.created_at.astimezone(USER_TZ)
        return local.strftime("%b %-d, %Y, %-I:%M %p PT (America/Los_Angeles)")


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        # Heuristic: seconds vs milliseconds
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, OverflowError):
        return None


def first_line_title(text: str, fallback: str = "X post") -> str:
    for line in (text or "").splitlines():
        line = re.sub(r"https?://\S+", "", line).strip(" \t-–—")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            return line[:140]
    return fallback


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def apply_styles(text: str, style_ranges: list[dict], link_ranges: list[dict]) -> str:
    """Render DraftJS-style inline ranges to HTML, including partial overlaps."""
    if not text:
        return ""
    n = len(text)
    styles: list[set[str]] = [set() for _ in range(n)]
    links: list[str | None] = [None] * n
    for r in style_ranges or []:
        start = int(r.get("offset") or 0)
        end = min(n, start + int(r.get("length") or 0))
        name = str(r.get("style") or "").lower()
        if not name:
            continue
        for i in range(max(0, start), end):
            styles[i].add(name)
    for r in link_ranges or []:
        start = int(r.get("offset") or 0)
        end = min(n, start + int(r.get("length") or 0))
        url = r.get("url") or ""
        if not url:
            continue
        for i in range(max(0, start), end):
            links[i] = url

    def tags_for(style_set: set[str], href: str | None) -> list[str]:
        out: list[str] = []
        if href:
            out.append(f'a href="{html.escape(href, quote=True)}"')
        if "bold" in style_set:
            out.append("strong")
        if "italic" in style_set:
            out.append("em")
        if "underline" in style_set:
            out.append("u")
        if "strikethrough" in style_set:
            out.append("s")
        if "code" in style_set:
            out.append("code")
        return out

    parts: list[str] = []
    i = 0
    while i < n:
        j = i + 1
        while j < n and styles[j] == styles[i] and links[j] == links[i]:
            j += 1
        chunk = _escape(text[i:j])
        for tag in reversed(tags_for(styles[i], links[i])):
            name = tag.split(" ", 1)[0]
            if " " in tag:
                chunk = f"<{tag}>{chunk}</{name}>"
            else:
                chunk = f"<{tag}>{chunk}</{tag}>"
        parts.append(chunk)
        i = j
    return "".join(parts)


def _linkify(text: str) -> str:
    escaped = _escape(text)

    def repl(m: re.Match) -> str:
        url = m.group(0)
        # The URL was escaped; unescape is a no-op for typical URLs.
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>'

    return re.sub(r"https?://[^\s<]+", repl, escaped)


def expand_urls(text: str, entities: Any) -> str:
    """Replace t.co URLs with expanded URLs when entities are present."""
    if not text or not entities:
        return text
    urls = []
    if isinstance(entities, dict):
        urls = entities.get("urls") or []
    for u in urls:
        short = u.get("url") or ""
        expanded = u.get("expanded_url") or u.get("unwound_url") or u.get("text") or ""
        if short and expanded and short in text:
            text = text.replace(short, expanded)
    return text


def _users_index(includes: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for u in includes.get("users") or []:
        if u.get("id"):
            out[str(u["id"])] = u
    return out


def _media_index(includes: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in includes.get("media") or []:
        key = str(m.get("media_key") or m.get("id") or "")
        if key:
            out[key] = m
        mid = str(m.get("id") or "")
        if mid:
            out[mid] = m
    return out


def _image_from_media(m: dict, default_id: str = "") -> Image | None:
    if not m:
        return None
    kind = (m.get("type") or "photo").lower()
    url = m.get("url") or ""
    poster = m.get("preview_image_url") or m.get("thumbnail_url")
    if kind == "video" or kind == "animated_gif":
        if not poster:
            variants = m.get("variants") or []
            # Keep poster only; do not embed video.
            poster = poster or ""
        return Image(
            media_id=str(m.get("media_key") or m.get("id") or default_id),
            url=poster or url,
            alt=m.get("alt_text") or "Video",
            kind="video",
            poster_url=poster or url or None,
            width=m.get("width"),
            height=m.get("height"),
        )
    if not url:
        # Sometimes only preview_image_url is present.
        url = poster or ""
    if not url:
        return None
    return Image(
        media_id=str(m.get("media_key") or m.get("id") or default_id),
        url=url,
        alt=m.get("alt_text") or "",
        kind="photo",
        width=m.get("width"),
        height=m.get("height"),
    )



def drop_redundant_title(blocks: list[Block], title: str) -> list[Block]:
    """If the body opens with the same line as the title, keep it only as the heading."""
    if not blocks or not title:
        return blocks
    first = blocks[0]
    if first.type not in {"p", "h2", "h3"} or first.image or not first.text_html:
        return blocks
    plain = re.sub(r"<[^>]+>", "", first.text_html)
    plain_norm = re.sub(r"\s+", " ", plain).strip()
    norm_title = re.sub(r"\s+", " ", title).strip()
    if not plain_norm.casefold().startswith(norm_title.casefold()):
        return blocks
    rest = plain_norm[len(norm_title):].lstrip(" .:;—-")
    if not rest:
        return blocks[1:]
    first.text_html = _linkify(rest)
    blocks[0] = first
    return blocks



def _text_to_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    # Prefer paragraph breaks; if the text is mostly single newlines (X Articles
    # from the official API), treat each non-empty line as a paragraph.
    if "\n\n" in text and text.count("\n\n") >= max(2, text.count("\n") // 8):
        parts = re.split(r"\n\s*\n", text)
    else:
        parts = text.split("\n")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if looks_like_heading(part):
            heading = re.sub(r"^#{1,3}\s+", "", part)
            blocks.append(Block(type="h2", text_html=_linkify(heading)))
        else:
            blocks.append(Block(type="p", text_html=_linkify(part)))
    return blocks


# --- Official X API ---------------------------------------------------------

def from_official_api(payload: dict) -> Document:
    data = payload.get("data") or payload
    # list of posts (bookmarks / search)
    if isinstance(data, list):
        if not data:
            raise ValueError("Official API payload has an empty data list")
        # If the caller passed a whole search result, use the first item.
        post = data[0]
        payload = {**payload, "data": post}
        data = post
    includes = payload.get("includes") or {}
    users = _users_index(includes)
    media = _media_index(includes)

    post_id = str(data.get("id") or "")
    url = data.get("url") or (f"https://x.com/i/status/{post_id}" if post_id else "")
    author_id = str(data.get("author_id") or "")
    user = users.get(author_id) or {}
    author_name = user.get("name") or ""
    author_handle = user.get("username") or ""
    created = parse_datetime(data.get("created_at"))

    article = data.get("article") or {}
    note = data.get("note_tweet") or data.get("note_post") or {}
    text = ""
    kind = "post"
    title = ""
    cover = None
    blocks: list[Block] = []

    if article and (article.get("plain_text") or article.get("title")):
        kind = "article"
        title = (
            article.get("title")
            or (data.get("article_title") or {}).get("title")
            or "X Article"
        ).strip()
        text = article.get("plain_text") or article.get("preview_text") or ""
        blocks = _text_to_blocks(text)
        cover_key = article.get("cover_media")
        if cover_key and cover_key in media:
            cover = _image_from_media(media[cover_key], cover_key)
        # Inline article media, if not already used as the cover.
        seen = {cover.media_id} if cover else set()
        for key in article.get("media_entities") or []:
            key = str(key)
            if key in seen:
                continue
            img = _image_from_media(media.get(key) or {}, key)
            if img:
                blocks.append(Block(type="image" if img.kind == "photo" else "video", image=img))
                seen.add(img.media_id)
    else:
        if isinstance(note, dict) and note.get("text"):
            kind = "long_post"
            text = expand_urls(note.get("text") or "", note.get("entities"))
        else:
            text = expand_urls(data.get("text") or "", data.get("entities"))
            if len(text) > 280 and not data.get("text", "").endswith("…"):
                # Full text already in `text` (some sources).
                if len(text) > 400:
                    kind = "long_post"
        title = first_line_title(text, fallback=f"Post by @{author_handle}" if author_handle else "X post")
        blocks = drop_redundant_title(_text_to_blocks(text), title)
        # Attached photos / video posters
        keys = (data.get("attachments") or {}).get("media_keys") or []
        for key in keys:
            img = _image_from_media(media.get(str(key)) or {}, str(key))
            if img:
                if img.kind == "video":
                    blocks.append(Block(type="video", image=img))
                else:
                    blocks.append(Block(type="image", image=img))

    if not author_handle and url:
        m = re.search(r"x\.com/([^/]+)/status/", url)
        if m and m.group(1) not in {"i", "web"}:
            author_handle = m.group(1)

    return Document(
        post_id=post_id,
        url=url,
        title=title or "X post",
        author_name=author_name,
        author_handle=author_handle,
        created_at=created,
        kind=kind,
        source="x-api",
        blocks=blocks or [Block(type="p", text_html=_linkify(text or ""))],
        cover=cover,
        raw_text=text,
    )


# --- fxtwitter --------------------------------------------------------------

def _entity_map(raw: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            out[str(k)] = v if isinstance(v, dict) else {"value": v}
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            if "key" in item and "value" in item:
                out[str(item["key"])] = item["value"] or {}
            elif "key" in item:
                out[str(item["key"])] = item
    return out


def _fx_media_lookup(article: dict) -> dict[str, Image]:
    out: dict[str, Image] = {}
    for m in article.get("media_entities") or []:
        info = (m or {}).get("media_info") or {}
        url = info.get("original_img_url") or ""
        mid = str(m.get("media_id") or "")
        if not url:
            continue
        img = Image(
            media_id=mid or str(m.get("media_key") or ""),
            url=url,
            alt="",
            kind="photo",
            width=info.get("original_img_width"),
            height=info.get("original_img_height"),
        )
        if img.media_id:
            out[img.media_id] = img
            out[str(m.get("media_key") or "")] = img
    return out


def _fx_cover(article: dict) -> Image | None:
    cover = article.get("cover_media") or {}
    if not cover:
        return None
    info = cover.get("media_info") or {}
    url = info.get("original_img_url") or ""
    if not url:
        return None
    return Image(
        media_id=str(cover.get("media_id") or cover.get("media_key") or "cover"),
        url=url,
        alt=article.get("title") or "Cover",
        kind="photo",
        width=info.get("original_img_width"),
        height=info.get("original_img_height"),
    )


def _blocks_from_draftjs(article: dict) -> list[Block]:
    content = article.get("content") or {}
    raw_blocks = content.get("blocks") or []
    emap = _entity_map(content.get("entityMap") or content.get("entity_map"))
    media = _fx_media_lookup(article)
    out: list[Block] = []
    for b in raw_blocks:
        btype = (b.get("type") or "unstyled").lower()
        text = b.get("text") or ""
        # Collect links from entity ranges
        link_ranges: list[dict] = []
        images: list[Image] = []
        divider = False
        for er in b.get("entityRanges") or []:
            ent = emap.get(str(er.get("key"))) or {}
            etype = (ent.get("type") or "").upper()
            data = ent.get("data") or {}
            if etype == "LINK" and data.get("url"):
                link_ranges.append(
                    {
                        "offset": er.get("offset") or 0,
                        "length": er.get("length") or 0,
                        "url": data["url"],
                    }
                )
            elif etype == "MEDIA":
                for item in data.get("mediaItems") or []:
                    mid = str(item.get("mediaId") or "")
                    if mid in media:
                        images.append(media[mid])
                    elif mid:
                        # URL unknown; skip unless we later find it.
                        pass
            elif etype == "DIVIDER":
                divider = True
        styled = apply_styles(text, b.get("inlineStyleRanges") or [], link_ranges)
        if btype in {"header-one", "header-two", "header-three"}:
            level = {"header-one": "h2", "header-two": "h2", "header-three": "h3"}[btype]
            if styled.strip():
                out.append(Block(type=level, text_html=styled))
            for img in images:
                out.append(Block(type="image", image=img))
        elif btype == "blockquote":
            out.append(Block(type="quote", text_html=styled.replace("\n", "<br/>")))
        elif btype == "ordered-list-item":
            out.append(Block(type="li-ol", text_html=styled))
        elif btype == "unordered-list-item":
            out.append(Block(type="li-ul", text_html=styled))
        elif btype == "atomic":
            if divider:
                out.append(Block(type="hr"))
            for img in images:
                out.append(Block(type="image", image=img))
        else:
            # unstyled / code-block / etc.
            if styled.strip():
                if btype == "code-block":
                    out.append(Block(type="p", text_html=f"<pre><code>{_escape(text)}</code></pre>"))
                else:
                    out.append(Block(type="p", text_html=styled.replace("\n", "<br/>")))
            for img in images:
                out.append(Block(type="image", image=img))
    return out


def _fx_tweet_media(tweet: dict) -> list[Image]:
    images: list[Image] = []
    media = tweet.get("media") or {}
    items = []
    if isinstance(media, dict):
        items = media.get("photos") or media.get("all") or []
    elif isinstance(media, list):
        items = media
    for m in items:
        if not isinstance(m, dict):
            continue
        kind = (m.get("type") or "").lower()
        if kind == "video" or m.get("duration") or m.get("thumbnail_url") and "video" in (m.get("url") or ""):
            poster = m.get("thumbnail_url") or m.get("preview_image_url")
            images.append(
                Image(
                    media_id=str(m.get("id") or ""),
                    url=poster or "",
                    alt="Video",
                    kind="video",
                    poster_url=poster,
                    width=m.get("width"),
                    height=m.get("height"),
                )
            )
        elif kind in {"photo", "image", ""} or m.get("url"):
            url = m.get("url") or ""
            if url and not url.endswith(".mp4"):
                images.append(
                    Image(
                        media_id=str(m.get("id") or ""),
                        url=url,
                        alt=m.get("altText") or m.get("alt") or "",
                        kind="photo",
                        width=m.get("width"),
                        height=m.get("height"),
                    )
                )
    return images


def from_fxtwitter(payload: dict) -> Document:
    envelope = payload
    if "tweet" not in envelope and isinstance(envelope.get("data"), dict) and "tweet" in envelope["data"]:
        envelope = envelope["data"]
    tweet = envelope.get("tweet") or envelope
    article = tweet.get("article") or {}
    author = tweet.get("author") or {}
    post_id = str(tweet.get("id") or "")
    url = tweet.get("url") or (f"https://x.com/i/status/{post_id}" if post_id else "")
    author_name = author.get("name") or ""
    author_handle = author.get("screen_name") or author.get("username") or ""
    created = parse_datetime(tweet.get("created_at") or tweet.get("created_timestamp"))

    if article and (article.get("content") or article.get("title") or article.get("plain_text")):
        title = (article.get("title") or "X Article").strip()
        if article.get("content"):
            blocks = _blocks_from_draftjs(article)
            raw = "\n".join(
                re.sub("<[^>]+>", "", b.text_html) for b in blocks if b.text_html
            )
        else:
            raw = article.get("plain_text") or article.get("preview_text") or ""
            blocks = _text_to_blocks(raw)
        return Document(
            post_id=post_id,
            url=url,
            title=title,
            author_name=author_name,
            author_handle=author_handle,
            created_at=created,
            kind="article",
            source="fxtwitter",
            blocks=blocks,
            cover=_fx_cover(article),
            raw_text=raw,
        )

    text = tweet.get("text") or ""
    raw_text_obj = tweet.get("raw_text") or {}
    # Prefer the full note text when present (fxtwitter puts it in `text`
    # when is_note_tweet is true).
    if tweet.get("is_note_tweet") or (isinstance(raw_text_obj, dict) and len(text) > len(raw_text_obj.get("text") or "")):
        kind = "long_post"
    elif len(text) > 400:
        kind = "long_post"
    else:
        kind = "post"
    title = first_line_title(text, fallback=f"Post by @{author_handle}" if author_handle else "X post")
    blocks = drop_redundant_title(_text_to_blocks(text), title)
    for img in _fx_tweet_media(tweet):
        blocks.append(Block(type="video" if img.kind == "video" else "image", image=img))
    return Document(
        post_id=post_id,
        url=url,
        title=title,
        author_name=author_name,
        author_handle=author_handle,
        created_at=created,
        kind=kind,
        source="fxtwitter",
        blocks=blocks,
        cover=None,
        raw_text=text,
    )


def document_from_payload(payload: dict, source_hint: str = "") -> Document:
    """Dispatch on the shape of a fetched or saved payload."""
    data = payload
    # Unwrap {source, data, requested} envelopes, including a nested wrap
    # from --from-json of an already-enveloped fixture.
    for _ in range(5):
        if not isinstance(data, dict):
            break
        if data.get("tweet"):
            break
        inner = data.get("data")
        is_envelope = "source" in data or "requested" in data
        if is_envelope and isinstance(inner, dict) and "tweet" not in data:
            # Official API post wrapped as {source, data: {id, text, ...}, includes?}
            # vs fxtwitter {source, data: {code, tweet}}.
            if inner.get("tweet") or inner.get("code") is not None:
                source_hint = source_hint or str(data.get("source") or "")
                data = inner
                continue
            if "includes" in data and isinstance(inner, dict) and inner.get("id"):
                break  # {data: post, includes, source} — official, don't unwrap includes away
            if inner.get("id") and (inner.get("text") is not None or inner.get("article") or inner.get("note_tweet") or inner.get("note_post")):
                # Official post sitting in .data with no includes at this level.
                source_hint = source_hint or str(data.get("source") or "")
                # Keep official shape if includes live next to data.
                if data.get("includes"):
                    break
                data = inner
                continue
            # Nested envelope.
            if inner.get("source") and "data" in inner:
                source_hint = source_hint or str(inner.get("source") or data.get("source") or "")
                data = inner
                continue
            source_hint = source_hint or str(data.get("source") or "")
            data = inner
            continue
        break

    if isinstance(data, dict) and (data.get("tweet") or (data.get("code") is not None and isinstance(data.get("tweet"), dict))):
        return from_fxtwitter(data)
    if isinstance(data, dict) and (
        data.get("data") is not None
        or data.get("includes")
        or data.get("article")
        or data.get("note_tweet")
        or data.get("note_post")
        or data.get("id")
    ):
        return from_official_api(data)
    raise ValueError(f"Unrecognized payload shape (source={source_hint!r})")
