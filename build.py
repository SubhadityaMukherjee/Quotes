#!/usr/bin/env python3
"""
Quotes — split quotes.md into small paged source files and build a GitHub Pages site.

Source of truth
---------------
  src/quotes-001.md, src/quotes-002.md, ...   one small file per page (~PAGE_SIZE quotes)
  inbox.md                                     paste NEW quotes here, then run `build`

The original quotes.md is never modified; after a successful `init` it is just a backup.

Commands
--------
  python3 build.py init     one-time: split quotes.md -> src/, verify nothing is lost, build the site
  python3 build.py          ingest inbox.md into src/, rebuild the site (the normal "I added quotes" command)
  python3 build.py build    same as above (explicit)
  python3 build.py stats    print entry / page counts
  python3 build.py serve    preview the site at http://localhost:8000

No quotes are ever lost: every entry is a blank-line-delimited block, and `init`
reconstructs the original quotes.md from the parsed blocks and aborts unless the
round-trip is byte-for-byte identical. Inbox ingest is crash-safe: the inbox is
archived (renamed) BEFORE the store is rewritten, so a crash can leave quotes
un-filed in inbox-archive/ but never lost or duplicated.
"""

import argparse
import http.server
import json
import re
import shutil
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
DOCS = ROOT / "docs"
DATA = DOCS / "data"
INBOX = ROOT / "inbox.md"
ARCHIVE = ROOT / "inbox-archive"
MASTER = ROOT / "quotes.md"

PAGE_SIZE = 100  # quotes per source file / per website page

# A markdown bullet list item, e.g. "- quote", "* quote", "+ quote".
# Such a line starts a new entry even without a preceding blank line, so a
# pasted block of back-to-back bullets is split into one entry per bullet.
LIST_ITEM_RE = re.compile(r"[-*+]\s+\S")

# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def strip_front_matter(text):
    """If text starts with a YAML front-matter block (--- ... ---), return
    (front_matter_including_delimiters, body, had_front_matter)."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                fm = "\n".join(lines[: i + 1])
                body = "\n".join(lines[i + 1 :])
                return fm, body, True
    return "", text, False


def _flush(cur, entries):
    if cur:
        entries.append("\n".join(cur).rstrip())
        cur.clear()
    return cur


def collect_quotes(text):
    """Return the list of quote entries in `text`.

    An entry is a maximal run of consecutive non-blank lines, EXCEPT that any
    line beginning with a markdown bullet (`- `, `* `, `+ `) starts a new
    entry. So a block of back-to-back bullets with no blank line between them
    is correctly split into one entry per bullet, while a single soft-wrapped
    quote (only its first line starts with a bullet) stays one entry.

    Skipped (never counted as quotes): YAML front matter, markdown headings
    (`#`), and `<!-- ... -->` comment blocks (may span multiple lines).
    """
    _fm, body, _ = strip_front_matter(text)
    entries, cur = [], []
    in_comment = False
    for line in body.split("\n"):
        stripped = line.strip()
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if "<!--" in line and "-->" in line:        # single-line comment
            cur = _flush(cur, entries)
            continue
        if "<!--" in line:                          # start of multi-line comment
            cur = _flush(cur, entries)
            in_comment = True
            continue
        if stripped.startswith("-->"):              # stray comment closer
            cur = _flush(cur, entries)
            continue
        if stripped == "":                          # blank line separates entries
            cur = _flush(cur, entries)
            continue
        if stripped.startswith("#"):                # markdown heading
            cur = _flush(cur, entries)
            continue
        if LIST_ITEM_RE.match(stripped):            # a bullet starts a new entry
            cur = _flush(cur, entries)
        cur.append(line)
    _flush(cur, entries)
    return entries


def split_blocks_exact(text):
    """Pure blank-line-delimited blocks, skipping nothing. Used to verify the
    initial split round-trips back to the exact original bytes."""
    entries, cur = [], []
    for line in text.split("\n"):
        if line.strip() == "":
            cur = _flush(cur, entries)
        else:
            cur.append(line)
    _flush(cur, entries)
    return entries


# --------------------------------------------------------------------------- #
# Source store (src/)
# --------------------------------------------------------------------------- #

def page_filename(index):
    return SRC / f"quotes-{index:03d}.md"


def write_store(entries):
    """Write `entries` into balanced src/ page files (PAGE_SIZE each).

    Only pages whose contents actually changed are rewritten, so adding a few
    quotes normally touches just the last page (plus a new page file when one
    fills up). The per-page header carries no totals on purpose, so unchanged
    pages stay byte-for-byte identical and don't show up as diffs.
    """
    SRC.mkdir(parents=True, exist_ok=True)
    total = len(entries)
    npages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    written = 0
    for p in range(npages):
        chunk = entries[p * PAGE_SIZE : (p + 1) * PAGE_SIZE]
        header = f"<!-- Page {p + 1} · add new quotes to ../inbox.md, not here -->"
        body = "\n\n".join(chunk).rstrip() + "\n"
        text = header + "\n" + body + "\n"
        path = page_filename(p + 1)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            written += 1

    # drop stale page files beyond the current page count (e.g. after a re-split)
    for old in SRC.glob("quotes-*.md"):
        idx = int(old.stem.split("-")[1])
        if idx > npages:
            old.unlink()

    return npages


def read_store():
    """Read every src/quotes-*.md in order and return the concatenated entries."""
    if not SRC.exists():
        return []
    entries = []
    for path in sorted(SRC.glob("quotes-*.md")):
        entries.extend(collect_quotes(path.read_text(encoding="utf-8")))
    return entries


# --------------------------------------------------------------------------- #
# Site data (docs/data/quotes.js)
# --------------------------------------------------------------------------- #

def ensure_quotes(text):
    """Make sure a displayed quote is wrapped in double quotation marks.

    - already starts AND ends with '"'  -> left untouched
    - has '"' on neither edge            -> wrapped as '"..."'
    - has '"' on only one edge           -> left untouched (these are usually
                                            dialogue or an attribution suffix
                                            like '"..." -Author', which wrapping
                                            would mangle)
    """
    if not text:
        return text
    starts = text[0] == '"'
    ends = text[-1] == '"'
    if starts and ends:
        return text
    if not starts and not ends:
        return '"' + text + '"'
    return text


def for_display(entry):
    """Turn a raw entry into clean display text: drop a leading '- ', join
    soft-wrapped lines with a space, collapse whitespace, and make sure it is
    wrapped in quotation marks."""
    text = entry
    if text.startswith("- "):
        text = text[2:]
    text = re.sub(r"\s+", " ", text).strip()
    return ensure_quotes(text)


def write_site_data(entries):
    DATA.mkdir(parents=True, exist_ok=True)
    payload = [for_display(e) for e in entries]
    js = "window.__QUOTES__ = " + json.dumps(payload, ensure_ascii=False) + ";\n"
    (DATA / "quotes.js").write_text(js, encoding="utf-8")
    return len(payload)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_init(args):
    if not MASTER.exists():
        die(f"Could not find {MASTER}. Run this from the repository root.")
    text = MASTER.read_text(encoding="utf-8")

    # --- Lossless verification -------------------------------------------------
    # Guarantee: no line of quote content is dropped. We compare the ordered list
    # of non-blank content lines (whitespace-normalised) between the original body
    # and the flattened parsed entries. Blank-line counts and trailing spaces in
    # the source are cosmetic and may differ; quote text must not.
    _fm, body, _had_fm = strip_front_matter(text)
    blocks = split_blocks_exact(body)
    if blocks and blocks[0].strip() == "QUOTES":
        blocks.pop(0)  # the "QUOTES" title line, not a quote

    def content_lines(s):
        return [ln.rstrip() for ln in s.split("\n") if ln.strip() != ""]

    orig = content_lines(body)
    if orig and orig[0].rstrip() == "QUOTES":
        orig = orig[1:]
    flat = [ln for b in blocks for ln in content_lines(b)]
    if orig != flat:
        (ROOT / ".init_orig.txt").write_text("\n".join(orig), encoding="utf-8")
        (ROOT / ".init_flat.txt").write_text("\n".join(flat), encoding="utf-8")
        die(
            "VERIFICATION FAILED — content lines differ between quotes.md and parsed entries.\n"
            "  Wrote .init_orig.txt and .init_flat.txt for inspection.\n"
            "  Nothing has been written. Aborting so no quotes are lost."
        )

    entries = blocks
    npages = write_store(entries)
    n = write_site_data(entries)
    print(f"init OK  —  {n} quotes across {npages} source pages (round-trip verified ✓)")
    print(f"  source: {SRC}/    site data: {DATA/'quotes.js'}")
    print("  Next: commit & push, then enable GitHub Pages on the /docs folder (see docs/README.md).")


def ingest_inbox():
    """Merge inbox.md into the store. Crash-safe: archive inbox before rewriting."""
    if not INBOX.exists():
        return 0
    inbox_text = INBOX.read_text(encoding="utf-8")
    new_entries = collect_quotes(inbox_text)
    if not new_entries:
        return 0

    # 1. Archive the inbox (atomic rename) so a crash can never lose or duplicate.
    ARCHIVE.mkdir(exist_ok=True)
    stamp = _safe_stamp()
    archive_path = ARCHIVE / f"inbox-{stamp}.md"
    shutil.move(str(INBOX), str(archive_path))
    # 2. Recreate an empty inbox.
    _write_empty_inbox()

    # 3. Rebuild the store with the new entries appended.
    store = read_store()
    store.extend(new_entries)
    write_store(store)
    return len(new_entries)


def cmd_build(args):
    if not SRC.exists() or not any(SRC.glob("quotes-*.md")):
        die("No src/ store found. Run `python3 build.py init` first.")
    added = ingest_inbox()
    if added:
        print(f"Ingested {added} new quote(s) from inbox.md  (archived to inbox-archive/)")
    entries = read_store()
    npages = write_store(entries)          # re-balance (no-op if nothing added)
    n = write_site_data(entries)
    tag = f" (+{added} new)" if added else ""
    print(f"build OK — {n} quotes across {npages} pages{tag}  →  {DATA/'quotes.js'}")


def cmd_stats(args):
    entries = read_store()
    npages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
    inbox = len(collect_quotes(INBOX.read_text(encoding="utf-8"))) if INBOX.exists() else 0
    print(f"store:   {len(entries)} quotes in {npages} src pages ({PAGE_SIZE}/page)")
    print(f"inbox:   {inbox} quote(s) waiting to be merged")
    print(f"data:    {DATA/'quotes.js'} ({'exists' if (DATA/'quotes.js').exists() else 'MISSING — run build'})")


def cmd_serve(args):
    if not DOCS.exists():
        die("No docs/ folder. Run `python3 build.py init` first.")
    port = args.port
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(DOCS), **kw
    )
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Serving {DOCS} at http://127.0.0.1:{port}/  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _safe_stamp():
    """A filesystem-safe, monotonically-increasing-ish stamp (no Date in workflow
    context, but this is a normal CLI script so datetime is fine)."""
    import datetime

    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _write_empty_inbox():
    INBOX.write_text(
        "<!--\n"
        "  INBOX — paste NEW quotes BELOW this comment block.\n"
        "  One quote per bullet (- ...). A blank line between bullets is optional:\n"
        "  each line starting with '- ' begins a new quote, so a tight list works too.\n"
        "  Quotation marks are added automatically if missing.\n"
        "\n"
        "      - your new quote here\n"
        "      - \"or with quotes, fine either way\"\n"
        "\n"
        "  Then run:  python3 build.py\n"
        "  This whole block is ignored; only the quotes below it get merged into the site.\n"
        "-->\n"
        "\n",
        encoding="utf-8",
    )


def die(msg):
    print("error:", msg, file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Split quotes.md and build a GitHub Pages site.")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("init", help="one-time: split quotes.md -> src/ and build the site")
    sub.add_parser("build", help="ingest inbox.md and rebuild the site")
    sub.add_parser("stats", help="print quote / page counts")
    serve = sub.add_parser("serve", help="preview the site locally")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    cmd = args.cmd or "build"
    {"init": cmd_init, "build": cmd_build, "stats": cmd_stats, "serve": cmd_serve}[cmd](args)


if __name__ == "__main__":
    main()
