#!/usr/bin/env python3
"""Convert an X long post or Article into a Kindle-ready EPUB.

Examples:
  python /workspace/x-to-kindle/x2kindle.py https://x.com/thedankoe/status/2010751592346030461
  python /workspace/x-to-kindle/x2kindle.py 2010751592346030461 -o samples/article.epub
  python /workspace/x-to-kindle/x2kindle.py https://x.com/i/status/123 --from-json fixtures/post.json

This tool never sends email. Use --print-send-plan to see how Grok Bot
would deliver the EPUB to Kindle through Gmail.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from epubbuild import build_epub, default_filename, validate_epub  # noqa: E402
from fetching import FetchError, fetch_post  # noqa: E402
from kindle_send import format_plan, load_config, prepare_gmail_send  # noqa: E402
from model import document_from_payload  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch an X long post or Article and write a Kindle EPUB.",
        epilog="Delivery to Kindle is prepared only; this command does not send mail.",
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="X post URL, Article URL, or numeric post ID",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output EPUB path (default: ./<handle>-<title>-<id>.epub)",
    )
    parser.add_argument(
        "--from-json",
        help="Read a saved official X API or fxtwitter JSON payload instead of fetching",
    )
    parser.add_argument(
        "--save-json",
        help="Write the fetched payload to this JSON file",
    )
    parser.add_argument(
        "--print-send-plan",
        action="store_true",
        help="Print the Gmail Send-to-Kindle plan after building the EPUB (does not send)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Refused: this CLI never sends email or creates Gmail drafts",
    )
    parser.add_argument(
        "--config",
        help="Path to config.json (kindle_address, from_address)",
    )
    args = parser.parse_args(argv)

    if not args.url and not args.from_json:
        parser.error("Provide an X URL/ID, or --from-json")

    try:
        payload = fetch_post(args.url or "", from_json=args.from_json)
    except FetchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.save_json:
        Path(args.save_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_json).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    try:
        doc = document_from_payload(payload)
    except ValueError as e:
        print(f"error: could not parse post: {e}", file=sys.stderr)
        return 2

    out = Path(args.output) if args.output else Path.cwd() / default_filename(doc)
    if out.suffix.lower() != ".epub":
        out = out.with_suffix(".epub")

    build_epub(doc, out)
    problems = validate_epub(out)
    size = out.stat().st_size
    print(f"Wrote {out} ({size} bytes)")
    print(f"  kind:   {doc.kind}")
    print(f"  title:  {doc.title}")
    print(f"  author: {doc.author_label}")
    if doc.display_date():
        print(f"  date:   {doc.display_date()}")
    print(f"  source: {doc.source}")
    print(f"  url:    {doc.url}")
    if problems:
        print("  validation issues:")
        for p in problems:
            print(f"    - {p}")
        return 3
    print("  validation: ok")

    if args.send:
        print(
            "\n--send is disabled. This tool does not send email or create Gmail drafts. "
            "Re-run with --print-send-plan, then ask Grok Bot to deliver the EPUB.",
            file=sys.stderr,
        )
        args.print_send_plan = True

    if args.print_send_plan:
        config = load_config(args.config) if args.config else load_config()
        plan = prepare_gmail_send(doc, out, config=config, include_bytes=False)
        print()
        print(format_plan(plan))
        if args.send:
            return 2
        if plan.get("error"):
            return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
