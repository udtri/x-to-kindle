"""Prepare (but do not send) a Send-to-Kindle email via Gmail.

This module never talks to Gmail or SMTP. Delivery is done by Grok Bot
with the connected user-Gmail connector, after the user has confirmed.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from model import Document

DEFAULT_FROM = ""  # set X2KINDLE_FROM
MIME_EPUB = "application/epub+zip"
# Gmail connector limit; Send-to-Kindle itself allows larger personal documents.
GMAIL_ATTACHMENT_LIMIT = 25 * 1024 * 1024

CONFIG_CANDIDATES = (
    Path(__file__).resolve().parent / "config.json",
    Path(os.environ.get("X2KINDLE_CONFIG", "")),
    Path.home() / ".config" / "x2kindle" / "config.json",
)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if path:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Config not found: {p}")
        return json.loads(p.read_text(encoding="utf-8"))
    for candidate in CONFIG_CANDIDATES:
        if candidate and candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


def kindle_address(config: dict[str, Any] | None = None) -> str:
    env = os.environ.get("KINDLE_ADDRESS") or os.environ.get("X2KINDLE_KINDLE_ADDRESS")
    if env:
        return env.strip()
    config = config if config is not None else load_config()
    return (config.get("kindle_address") or config.get("kindleAddress") or "").strip()


def from_address(config: dict[str, Any] | None = None) -> str:
    env = os.environ.get("X2KINDLE_FROM") or os.environ.get("KINDLE_FROM")
    if env:
        return env.strip()
    config = config if config is not None else load_config()
    return (config.get("from_address") or config.get("fromAddress") or DEFAULT_FROM).strip()


def prepare_gmail_send(
    doc: Document,
    epub_path: str | Path,
    *,
    to: str | None = None,
    from_addr: str | None = None,
    config: dict[str, Any] | None = None,
    include_bytes: bool = False,
) -> dict[str, Any]:
    """Build the exact Gmail MCP arguments that would deliver this EPUB.

    Does not send, draft, or upload anything.
    """
    config = config if config is not None else load_config()
    to = (to or kindle_address(config) or "").strip()
    from_addr = (from_addr or from_address(config) or "").strip()
    epub_path = Path(epub_path)
    if not epub_path.is_file():
        raise FileNotFoundError(f"EPUB not found: {epub_path}")
    size = epub_path.stat().st_size
    filename = epub_path.name
    subject = (doc.title or "X post")[:200]
    body = (
        f"{doc.title}\n\n"
        f"By {doc.author_label}\n"
        f"{doc.url}\n"
        "\n"
        "Converted from X by x2kindle."
    )
    plan: dict[str, Any] = {
        "connector": "user-Gmail",
        "recommended_tool": "create_draft",
        "send_tool": "send_email",
        "why_draft_first": (
            "create_draft lets you review the message and the EPUB attachment "
            "in Gmail before anything goes to Kindle. send_email sends immediately, "
            "with no draft step. After a draft looks right, send_draft can send it."
        ),
        "arguments": {
            "to": to,
            "from": from_addr,
            "subject": subject,
            "body": body,
            "attachments": [
                {
                    "filename": filename,
                    "mimeType": MIME_EPUB,
                    "content": "<base64 of the EPUB bytes; omitted unless include_bytes=True>",
                }
            ],
        },
        "epub_path": str(epub_path.resolve()),
        "epub_bytes": size,
        "gmail_attachment_limit_bytes": GMAIL_ATTACHMENT_LIMIT,
        "amazon_setup": [
            "In Amazon: Account > Manage Your Content and Devices > Preferences > Personal Document Settings.",
            f"Confirm the Send-to-Kindle address is {to or '(not set)'}.",
            f"Add {from_addr} to the Approved Personal Document E-mail List. "
            "Mail from an unapproved address is dropped silently.",
        ],
        "limits": (
            "The Gmail connector caps attachments at 25 MB total. "
            "Kindle personal documents have a higher limit, so Gmail is the bottleneck."
        ),
        "status": "not_sent",
    }
    if not to:
        plan["error"] = (
            "No Kindle address is configured. Set kindle_address in config.json "
            "or the KINDLE_ADDRESS environment variable."
        )
    if size > GMAIL_ATTACHMENT_LIMIT:
        plan["error"] = (
            f"EPUB is {size} bytes, over the Gmail 25 MB attachment limit. "
            "Do not attach it; use a Drive link or shrink the file."
        )
    if include_bytes:
        content = base64.b64encode(epub_path.read_bytes()).decode("ascii")
        plan["arguments"]["attachments"][0]["content"] = content
    return plan


def format_plan(plan: dict[str, Any]) -> str:
    args = plan["arguments"]
    lines = [
        "Send-to-Kindle plan (NOT sent):",
        f"  Connector: {plan['connector']}",
        f"  Preferred tool: {plan['recommended_tool']} (review first), then send_draft",
        f"  Immediate alternative: {plan['send_tool']}",
        f"  To: {args['to'] or '(missing Kindle address)'}",
        f"  From: {args['from']}",
        f"  Subject: {args['subject']}",
        f"  Attachment: {args['attachments'][0]['filename']} ({plan['epub_bytes']} bytes, {args['attachments'][0]['mimeType']})",
        f"  EPUB path: {plan['epub_path']}",
        "",
        "Amazon setup:",
    ]
    for item in plan["amazon_setup"]:
        lines.append(f"  - {item}")
    lines.append("")
    lines.append(plan["limits"])
    if plan.get("error"):
        lines.append("")
        lines.append(f"Blocked: {plan['error']}")
    else:
        lines.append("")
        lines.append(
            "This CLI does not send email and will not create a Gmail draft. "
            "Ask Grok Bot to attach the EPUB with user-Gmail once you want it delivered."
        )
    return "\n".join(lines)
