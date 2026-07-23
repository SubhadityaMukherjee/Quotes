# Quotes

A personal collection of quotes, split across many small markdown files and
published as a searchable website via GitHub Pages.

## Layout

```
quotes.md            the original file (kept as a backup; no longer the source of truth)
build.py             the one script that does everything
inbox.md             paste NEW quotes here
src/quotes-001.md …  source of truth — one small, fast-to-open file per ~100 quotes
docs/                the website (published by GitHub Pages)
  index.html, style.css, app.js
  data/quotes.js     generated from src/ by build.py
inbox-archive/       snapshots of each inbox batch after it was merged (safety net)
```

## First-time setup

Already done once (this created `src/` and `docs/data/quotes.js`):

```bash
python3 build.py init     # split quotes.md → src/, verify nothing is lost, build the site
```

`init` proves losslessness: it checks that every content line of `quotes.md`
survives in the parsed entries, and aborts without writing anything if not.

## Adding quotes (the normal workflow)

1. Open `inbox.md` (it's tiny) and paste new quotes under the comment block:

   ```markdown
   - "a new quote you want to keep"

   - "and another one"
   ```

2. Rebuild:

   ```bash
   python3 build.py
   ```

   This merges `inbox.md` into `src/` (creating new page files as needed),
   archives the inbox batch into `inbox-archive/`, and regenerates the site data.
   The ingest is crash-safe: the inbox is archived *before* the store is
   rewritten, so a crash can leave quotes un-filed in the archive but never lost
   or duplicated.

3. Commit and push. The site updates automatically.

## Preview locally

```bash
python3 build.py serve      # http://127.0.0.1:8000/
```

## Publish to GitHub Pages

Push to GitHub, then **Settings → Pages → Source: Deploy from a branch →
`main` / `/docs`**. See [`docs/README.md`](docs/README.md).

## Other commands

```bash
python3 build.py stats     # show quote / page counts and inbox status
```

## Notes on the split

Each quote is a blank-line-delimited block. A handful of quotes in the original
file sit back-to-back with no blank line between them; those are kept together
as one entry (their text is fully preserved, just shown in the same card). The
site shows ~100 quotes per page with instant search across the whole collection.
