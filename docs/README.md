# Quotes — website

This folder is published as a static site by **GitHub Pages**. It needs no build
step on GitHub's side — `build.py` regenerates `data/quotes.js` locally and the
HTML/CSS/JS are plain static files. The `.nojekyll` file tells GitHub Pages to
serve everything as-is (skip Jekyll).

## Enable GitHub Pages (one time)

1. Push this repository to GitHub.
2. On GitHub, go to **Settings → Pages**.
3. **Source: Deploy from a branch** → Branch: `main` → Folder: **`/docs`** → Save.
4. Wait ~1 minute. Your site appears at
   `https://<your-username>.github.io/<repo-name>/`.

## Preview locally

From the repository root:

```bash
python3 build.py serve
# open http://127.0.0.1:8000/
```

## Files

| file | purpose |
|------|---------|
| `index.html` | page shell (header, search, pager) |
| `style.css` | theme — light/dark, responsive |
| `app.js` | pagination + instant client-side search |
| `data/quotes.js` | **generated** by `build.py` — the quotes themselves |
| `.nojekyll` | disable Jekyll processing on GitHub Pages |
