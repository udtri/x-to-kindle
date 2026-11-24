"""Build a Kindle-friendly EPUB from a Document."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ebooklib import epub

from fetching import download_bytes
from model import Block, Document, Image

CSS = """
@namespace epub "http://www.idpf.org/2007/ops";
body {
  font-family: Georgia, "Palatino Linotype", Palatino, serif;
  font-size: 1em;
  line-height: 1.5;
  margin: 0 5%;
  color: #111;
}
h1.title {
  font-size: 1.6em;
  line-height: 1.25;
  margin: 0.4em 0 0.4em;
  font-weight: 700;
}
.byline, .meta, .source {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 0.92em;
  color: #444;
  margin: 0 0 0.35em;
}
.source { margin-bottom: 1.4em; }
.source a { color: #166; }
hr.rule {
  border: 0;
  border-top: 1px solid #ccc;
  margin: 1em 0 1.2em;
}
h2 { font-size: 1.22em; margin: 1.5em 0 0.6em; }
h3 { font-size: 1.08em; margin: 1.3em 0 0.5em; }
p { margin: 0 0 0.85em; }
blockquote {
  margin: 1em 0 1em 0.2em;
  padding: 0.1em 0 0.1em 0.9em;
  border-left: 3px solid #bbb;
  color: #333;
  font-style: italic;
}
ul, ol { margin: 0 0 0.9em 1.2em; padding: 0; }
li { margin: 0 0 0.3em; }
img.fig, img.cover-inline {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 0.8em auto;
}
.caption, .video-note {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 0.85em;
  color: #555;
  text-align: center;
  margin: 0 0 1em;
}
"""


def slugify(text: str, fallback: str = "post") -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:70] or fallback)


def default_filename(doc: Document) -> str:
    handle = slugify(doc.author_handle or "x", "x")
    title = slugify(doc.title, "post")
    pid = doc.post_id or "unknown"
    return f"{handle}-{title}-{pid}.epub"


def _guess_image_meta(data: bytes, url: str) -> tuple[str, str]:
    """Return (filename extension, mime type)."""
    lower = (url or "").lower().split("?", 1)[0]
    if data.startswith(b"\x89PNG"):
        return ".png", "image/png"
    if data.startswith(b"GIF8"):
        return ".gif", "image/gif"
    if data.startswith(b"\xff\xd8"):
        return ".jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if lower.endswith(".png"):
        return ".png", "image/png"
    if lower.endswith(".gif"):
        return ".gif", "image/gif"
    if lower.endswith(".webp"):
        return ".webp", "image/webp"
    return ".jpg", "image/jpeg"


def _collect_images(doc: Document) -> list[Image]:
    seen: set[str] = set()
    out: list[Image] = []
    if doc.cover and doc.cover.url:
        seen.add(doc.cover.url)
        out.append(doc.cover)
    for block in doc.blocks:
        if block.image and block.image.url and block.image.url not in seen:
            seen.add(block.image.url)
            out.append(block.image)
        if block.image and block.image.poster_url and block.image.poster_url not in seen:
            # Video posters already stored as url usually; skip duplicates.
            pass
    return out


def _blocks_to_html(blocks: list[Block], image_hrefs: dict[str, str]) -> str:
    parts: list[str] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.type in {"li-ul", "li-ol"}:
            tag = "ul" if b.type == "li-ul" else "ol"
            parts.append(f"<{tag}>")
            while i < len(blocks) and blocks[i].type == b.type:
                parts.append(f"<li>{blocks[i].text_html}</li>")
                i += 1
            parts.append(f"</{tag}>")
            continue
        if b.type == "h2":
            parts.append(f"<h2>{b.text_html}</h2>")
        elif b.type == "h3":
            parts.append(f"<h3>{b.text_html}</h3>")
        elif b.type == "quote":
            parts.append(f"<blockquote><p>{b.text_html}</p></blockquote>")
        elif b.type == "hr":
            parts.append('<hr class="rule"/>')
        elif b.type in {"image", "video"} and b.image:
            href = image_hrefs.get(b.image.url) or image_hrefs.get(b.image.poster_url or "")
            if href:
                alt = (b.image.alt or ("Video still" if b.type == "video" else "")).replace('"', "&quot;")
                parts.append(f'<img class="fig" src="{href}" alt="{alt}"/>')
            if b.type == "video":
                parts.append(
                    '<p class="video-note">This post includes a video, which is not '
                    "embedded in the ebook. A still frame is shown when available.</p>"
                )
            elif b.image.alt:
                parts.append(f'<p class="caption">{_escape_basic(b.image.alt)}</p>')
        else:
            if b.text_html.strip():
                # Already wrapped pre/code stays as-is
                if b.text_html.lstrip().startswith("<pre"):
                    parts.append(b.text_html)
                else:
                    parts.append(f"<p>{b.text_html}</p>")
        i += 1
    return "\n".join(parts)


def _escape_basic(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_epub(doc: Document, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    book = epub.EpubBook()
    ident = f"x2kindle-{doc.post_id or slugify(doc.title)}"
    book.set_identifier(ident)
    book.set_title(doc.title)
    book.add_author(doc.author_label)
    book.set_language("en")
    if doc.created_at:
        book.add_metadata("DC", "date", doc.created_at.date().isoformat())
    if doc.url:
        book.add_metadata("DC", "source", doc.url)
        book.add_metadata("DC", "identifier", doc.url, {"id": "source-url"})
    book.add_metadata("DC", "publisher", "x2kindle")
    book.add_metadata(
        "DC",
        "description",
        f"Saved from X ({doc.kind}) by {doc.author_label}.",
    )

    image_hrefs: dict[str, str] = {}
    cover_added = False
    for i, img in enumerate(_collect_images(doc)):
        data = download_bytes(img.url)
        if not data and img.poster_url:
            data = download_bytes(img.poster_url)
        if not data:
            continue
        ext, mime = _guess_image_meta(data, img.url)
        name = f"images/img{i}{ext}"
        item = epub.EpubItem(uid=f"img{i}", file_name=name, media_type=mime, content=data)
        book.add_item(item)
        image_hrefs[img.url] = name
        if img.poster_url:
            image_hrefs[img.poster_url] = name
        is_cover = doc.cover is not None and img.url == doc.cover.url
        if is_cover and not cover_added:
            book.set_cover("images/cover" + ext, data)
            cover_added = True

    byline = _escape_basic(doc.author_label)
    date = _escape_basic(doc.display_date())
    source = ""
    if doc.url:
        safe = _escape_basic(doc.url)
        source = f'<p class="source">Source: <a href="{safe}">{safe}</a></p>'
    kind_label = {
        "article": "X Article",
        "long_post": "X long post",
        "post": "X post",
    }.get(doc.kind, "X post")

    header = [
        f'<h1 class="title">{_escape_basic(doc.title)}</h1>',
        f'<p class="byline">{byline}</p>',
    ]
    if date:
        header.append(f'<p class="meta">{date} · {kind_label}</p>')
    else:
        header.append(f'<p class="meta">{kind_label}</p>')
    header.append(source)
    header.append('<hr class="rule"/>')

    # Show the cover once in the chapter if we have it, so it appears in the body too.
    cover_html = ""
    if doc.cover and doc.cover.url in image_hrefs:
        href = image_hrefs[doc.cover.url]
        cover_html = f'<img class="cover-inline" src="{href}" alt="{_escape_basic(doc.cover.alt or doc.title)}"/>'

    body = _blocks_to_html(doc.blocks, image_hrefs)
    # Body only: ebooklib wraps the document and would drop a hand-written
    # <link rel="stylesheet"> in <head>. Attach the stylesheet with add_link.
    chapter_html = "\n".join(header) + cover_html + body

    style = epub.EpubItem(
        uid="style",
        file_name="style/book.css",
        media_type="text/css",
        content=CSS.encode("utf-8"),
    )
    book.add_item(style)

    chapter = epub.EpubHtml(
        title=doc.title,
        file_name="chapter.xhtml",
        lang="en",
    )
    chapter.content = chapter_html
    chapter.add_link(href="style/book.css", rel="stylesheet", type="text/css")
    book.add_item(chapter)

    book.toc = [chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    epub.write_epub(str(out_path), book, {})
    return out_path


def validate_epub(path: str | Path) -> list[str]:
    """Structural checks (not a full epubcheck). Returns a list of problems."""
    path = Path(path)
    problems: list[str] = []
    if not path.is_file():
        return [f"missing file: {path}"]
    if path.stat().st_size < 200:
        problems.append("file is suspiciously small")
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "mimetype" not in names:
                problems.append("missing mimetype file")
            else:
                info = zf.getinfo("mimetype")
                if info.compress_type != zipfile.ZIP_STORED:
                    problems.append("mimetype is compressed (should be stored)")
                mime = zf.read("mimetype")
                if mime != b"application/epub+zip":
                    problems.append(f"bad mimetype: {mime!r}")
            if "META-INF/container.xml" not in names:
                problems.append("missing META-INF/container.xml")
            else:
                container = zf.read("META-INF/container.xml")
                try:
                    root = ET.fromstring(container)
                    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
                    rootfile = root.find(".//c:rootfile", ns)
                    opf = rootfile.get("full-path") if rootfile is not None else None
                    if not opf or opf not in names:
                        problems.append("container.xml does not point at an OPF file")
                    else:
                        zf.read(opf)
                except ET.ParseError as e:
                    problems.append(f"container.xml is not valid XML: {e}")
            xhtml_files = [n for n in names if n.endswith((".xhtml", ".html"))]
            if not xhtml_files:
                problems.append("no XHTML documents in the EPUB")
            else:
                found_title = False
                for name in xhtml_files:
                    raw = zf.read(name)
                    try:
                        ET.fromstring(raw)
                    except ET.ParseError as e:
                        problems.append(f"{name} is not well-formed XML: {e}")
                    if b"<h1" in raw or b"<p" in raw:
                        found_title = True
                if not found_title:
                    problems.append("no heading or paragraph content found")
            if not any(n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")) for n in names):
                # Images are optional (text-only long posts).
                pass
    except zipfile.BadZipFile:
        problems.append("not a valid ZIP/EPUB file")
    return problems
