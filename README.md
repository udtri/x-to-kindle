# x2kindle

Turn an X long post or X Article into a clean EPUB you can send to a Kindle.

```bash
# One-time setup (already done on this computer)
python3 -m venv /workspace/x-to-kindle/.venv
/workspace/x-to-kindle/.venv/bin/pip install -r /workspace/x-to-kindle/requirements.txt

# Build an EPUB from a post URL or ID
/workspace/x-to-kindle/.venv/bin/python /workspace/x-to-kindle/x2kindle.py \
  https://x.com/thedankoe/status/2010751592346030461

# Or write it somewhere specific, and show the Kindle email plan (does not send)
/workspace/x-to-kindle/.venv/bin/python /workspace/x-to-kindle/x2kindle.py \
  2010751592346030461 \
  -o example.epub \
  --print-send-plan
```

Your addresses come from environment variables (see Quick setup). This command never sends mail and never creates a Gmail draft.

## Quick setup

1. Install: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in your own values. `.env` is gitignored.
   - `KINDLE_ADDRESS`: your Send-to-Kindle address (Amazon > Manage Your Content and Devices > Preferences > Personal Document Settings).
   - `X2KINDLE_FROM`: the email you send from. Add it to Amazon's Approved Personal Document E-mail List.
3. Load them: `set -a; source .env; set +a`
4. Build an EPUB: `.venv/bin/python x2kindle.py <x-post-url> --print-send-plan`

Optional: a `config.json` (see `config.example.json`) also works, but environment variables win. No personal info is stored in this repo.

## What it fetches

- **Long posts** (Premium posts longer than 280 characters). The official X tools return the full text in `note_tweet` (the schema name is `note_post`; some responses use one, some the other). The short `text` field is often truncated and ends with a `t.co` link. fxtwitter returns the full text in `text` when `is_note_tweet` is true.
- **X Articles**. The official X tools (`x.get_posts_by_id`, with `post.fields` including `article` and `article_title`, plus expansions `article.cover_media` and `article.media_entities`) return the title, `article.plain_text` (full body), cover, and media keys. They do not return typographic structure. fxtwitter (`api.fxtwitter.com/status/{id}`) returns the same Article with DraftJS blocks: headings, lists, quotes, links, and inline images in the right place. That is what the CLI uses when it can.
- **Article URLs** like `https://x.com/i/article/2010742786430021632` are not post IDs. `get_posts_by_id` returns “not found” for them. Pass the wrapping post instead (`https://x.com/thedankoe/status/2010751592346030461`).

The CLI fetches `api.fxtwitter.com` first. From this computer that host is often blocked by Cloudflare; the tool then retries through `https://r.jina.ai/https://api.fxtwitter.com/status/{id}`, which works. You can also save official X API JSON (or fxtwitter JSON) and pass `--from-json`.

The connected `user-X` plugin can read your bookmarks (`get_users_bookmarks` with your numeric user id from `get_users_me`) but does not have a post-lookup tool. Public post and Article reads go through the built-in `x` namespace (`get_posts_by_id`, `search_posts_all`). Nothing in this tool posts, likes, bookmarks, or otherwise writes to X.

## EPUB

The ebook includes the title, author name and @handle, the date in America/Los_Angeles, the source URL, the body, the cover, and inline photos. Videos are not embedded; a still frame is included when one exists. Metadata uses the Article title or the first line of a post, and the author label, so the book looks right in a Kindle library.

## Sending to Kindle (not done by this CLI)

1. Confirm your Send-to-Kindle address in Amazon under Account → Manage Your Content and Devices → Preferences → Personal Document Settings. It is stored in `config.json` as `kindle_address`.
2. Add `you@gmail.com` to Amazon’s **Approved Personal Document E-mail List**. Mail from an unapproved address is dropped.
3. Ask Grok Bot to deliver a built EPUB. The connected Gmail account is `you@gmail.com`.

Exact Gmail path (attachments are supported):

- Preferred: `user-Gmail.create_draft` with `to` set to the Kindle address, `from` set to `you@gmail.com` (or omitted, which uses that account’s default), `subject` set to the piece’s title, a short `body`, and `attachments: [{ filename, mimeType: "application/epub+zip", content: "<base64 of the EPUB>" }]`. Review the draft in Gmail, then `send_draft`.
- Immediate: the same arguments on `user-Gmail.send_email` (no draft step).
- Gmail attachments are capped at 25 MB total. Kindle personal documents can be larger, so Gmail is the limit that matters.
- Kindle no longer accepts MOBI through Send to Kindle; EPUB is the right format.

`--send` on the CLI is refused on purpose. `--print-send-plan` prints this plan without sending.

## Bookmarks (next step)

`user-X.get_users_bookmarks` works for the connected account (id from `get_users_me`). A later routine can watch new bookmarks that have `note_tweet`/`note_post` or `article`, build an EPUB, and ask before emailing it to Kindle.
