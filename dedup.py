#!/usr/bin/env python3
"""
dedup.py — find near-duplicate quotes and interactively prune them.

Reuses build.py's parser (the exact same one the website uses), so what you see
here is what the site sees. Stdlib only, like build.py.

"Duplicate" means similar above a threshold (default 0.80), measured by
difflib.SequenceMatcher.ratio() on normalised text — case folded, quotes and
apostrophes removed, whitespace collapsed. So a quote with dropped apostrophes
("I don t know") still matches its clean form ("I don't know"), and stray
quotation marks don't count against you.

It NEVER edits anything until you have reviewed every group and typed `y` at the
final confirmation. Removed quotes are backed up to dedup-removed-<stamp>.md.

Usage
-----
  python3 dedup.py                  scan, then review duplicate groups interactively
  python3 dedup.py --report         just list duplicate groups, change nothing
  python3 dedup.py --auto           no prompts: keep the longest in every group,
                                   print every quote deleted, then remove + rebuild
  python3 dedup.py --threshold 0.85 raise/lower the similarity cutoff
  python3 dedup.py --limit 20       only process the 20 strongest groups
  python3 dedup.py --dry-run        show what would change, don't write
  python3 dedup.py --exhaustive     disable the token prefilter (slow; paranoid mode)

  python3 dedup.py selftest         sanity-check the page round-trip, then exit
"""

import argparse
import difflib
import re
import shutil
import sys
from pathlib import Path

import build  # the project script in this same directory

THRESHOLD_DEFAULT = 0.80

# Characters treated as "not content" for matching: straight/curly quotes,
# apostrophes, guillemets. Removing them means punctuation style is ignored.
_STRIP_CHARS = "'`´‘’“”«»\""
_STRIP_TABLE = str.maketrans("", "", _STRIP_CHARS)


# --------------------------------------------------------------------------- #
# Loading + normalising
# --------------------------------------------------------------------------- #

def normalize(entry):
    """Canonical form for comparison: drop the leading bullet, case-fold, strip
    quotes/apostrophes, collapse whitespace. Two quotes that differ only by
    punctuation or apostrophe style map to the same string."""
    text = entry[2:] if entry.startswith("- ") else entry
    text = text.lower().translate(_STRIP_TABLE)
    return re.sub(r"\s+", " ", text).strip()


def load_entries():
    """Read every src page, returning a list of records:
         {file, idx, raw, disp, norm, key}
    idx is the 0-based position of the entry inside its page file."""
    items = []
    for path in sorted(build.SRC.glob("quotes-*.md")):
        entries = build.collect_quotes(path.read_text(encoding="utf-8"))
        for i, raw in enumerate(entries):
            items.append({
                "file": path,
                "idx": i,
                "key": (str(path), i),
                "raw": raw,
                "disp": build.for_display(raw),  # clean, quote-wrapped for display
                "norm": normalize(raw),
            })
    return items


# --------------------------------------------------------------------------- #
# Finding near-duplicate groups
# --------------------------------------------------------------------------- #

class UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def find_groups(items, threshold, use_prefilter, out=sys.stderr):
    """Compare normalised texts and cluster near-duplicates.

    Returns (groups, edge) where:
      groups — list of index-lists (each len >= 2), strongest first
      edge   — dict (i, j) -> similarity, for pairs that crossed the threshold

    Correctness first: the length window is a provably safe bound (ratio can't
    exceed 2·min/(sum), so a pair below the threshold in length can't reach it
    in content). The token-overlap prefilter is also safe, not merely heuristic:
    if two strings share under half their tokens, the non-shared tokens alone
    cap the character ratio under ~60%, so a ≥80%-similar pair can never be
    dropped. It is ON by default; --exhaustive turns it off (much slower).
    """
    n = len(items)
    order = sorted(range(n), key=lambda k: len(items[k]["norm"]))
    lens = [len(items[k]["norm"]) for k in order]
    token_sets = [set(items[k]["norm"].split()) for k in range(n)] if use_prefilter else None
    uf = UnionFind(n)
    edge = {}

    comparisons = links = 0
    for ii in range(n):
        i = order[ii]
        li = lens[ii]
        if li == 0:
            continue
        ti = token_sets[i] if use_prefilter else None
        jj = ii + 1
        # walk forward while the longer string is still within 1.5× the shorter;
        # every unordered pair {i, j} with i as the shorter is visited exactly once
        while jj < n and lens[jj] <= 1.5 * li:
            j = order[jj]
            if use_prefilter:
                tj = token_sets[j]
                small, big = (ti, tj) if len(ti) <= len(tj) else (tj, ti)
                if big and len(small & big) / len(big) < 0.5:
                    jj += 1
                    continue
            r = difflib.SequenceMatcher(
                None, items[i]["norm"], items[j]["norm"], autojunk=False
            ).ratio()
            comparisons += 1
            if r >= threshold:
                uf.union(i, j)
                edge[(min(i, j), max(i, j))] = max(edge.get((min(i, j), max(i, j)), 0.0), r)
                links += 1
            jj += 1
        if ii and ii % 1000 == 0:
            print(f"  …scanned {ii}/{n}  ({comparisons} comparisons, {links} links)",
                  file=out)

    comp = {}
    for k in range(n):
        comp.setdefault(uf.find(k), []).append(k)
    groups = [sorted(g) for g in comp.values() if len(g) >= 2]
    groups.sort(key=lambda g: (-len(g), -group_strength(g, edge)))
    return groups, edge


def group_strength(group, edge):
    """Highest pairwise similarity inside a group (its strongest evidence)."""
    best = 0.0
    for a in range(len(group)):
        for b in range(a + 1, len(group)):
            best = max(best, edge.get((group[a], group[b]), 0.0))
    return best


def pick_keep(items, group):
    """Default survivor: the longest display text. Tie → earliest index."""
    return max(group, key=lambda k: (len(items[k]["disp"]), -k))


def auto_removals(items, groups):
    """Non-interactive resolution: in every group keep the longest, mark the
    rest for removal. Returns a set of keys."""
    to_remove = set()
    for group in groups:
        keep = pick_keep(items, group)
        for k in group:
            if k != keep:
                to_remove.add(items[k]["key"])
    return to_remove


def one_line(text, limit=110):
    """Collapse to a single line and trim to `limit` chars (for list previews)."""
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= limit else t[: limit - 3] + "…"


# --------------------------------------------------------------------------- #
# Display helpers
# --------------------------------------------------------------------------- #

def _wrap(text, width, indent):
    return "\n".join(
        (indent + line) for line in
        _fill(text, width - len(indent)).split("\n")
    )


def _fill(text, width):
    width = max(40, width)
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return "\n".join(out)


def print_review_card(items, group, edge, gi, total, keep_default, out):
    strength = group_strength(group, edge)
    keep_pos = group.index(keep_default) + 1
    print(
        f"\n━━━ Group {gi}/{total}  ·  {len(group)} quotes  ·  best match {strength:.0%} ━━━",
        file=out,
    )
    for pos, k in enumerate(group, 1):
        rec = items[k]
        star = " ★ keep" if k == keep_default else ""
        sim = "" if k == keep_default else (
            f"  ·  {edge.get((min(k, keep_default), max(k, keep_default)), 0.0):.0%} to keep"
        )
        print(
            f"  [{pos}]{star}  {rec['file'].name} #{rec['idx'] + 1}"
            f"  ·  {len(rec['disp'])} chars{sim}",
            file=out,
        )
        print(_wrap(rec["disp"], 100, "        "), file=out)
    print(f"  → suggested: keep [{keep_pos}] (longest), drop the rest", file=out)


# --------------------------------------------------------------------------- #
# Interactive review
# --------------------------------------------------------------------------- #

def review_groups(items, groups, edge, limit, out):
    """Walk the user through each group; return the set of keys to remove."""
    to_remove = set()
    total = len(groups) if limit is None else min(limit, len(groups))
    gi = 0
    while gi < total:
        group = groups[gi]
        keep_default = pick_keep(items, group)
        keep_pos = group.index(keep_default) + 1
        print_review_card(items, group, edge, gi + 1, total, keep_default, out)

        choice = input(
            f"  keep which? [Enter=keep [{keep_pos}] only, or type e.g. 1,3"
            f" | a=all | s=skip | q=finish] "
        ).strip().lower()

        if choice in ("q", "quit", "finish"):
            print("  finishing review early.", file=out)
            break
        if choice in ("a", "all", "s", "skip"):
            if choice in ("s", "skip"):
                print("  (skipped — keeping all in this group)", file=out)
            gi += 1
            continue
        if choice == "":
            keepers = {keep_default}
        else:
            nums = re.findall(r"\d+", choice)
            positions = {int(x) for x in nums if int(x) in range(1, len(group) + 1)}
            if not positions:
                print("  (didn't understand that — keeping all for safety)", file=out)
                gi += 1
                continue
            keepers = {group[p - 1] for p in positions}
        dropped = [k for k in group if k not in keepers]
        for k in dropped:
            to_remove.add(items[k]["key"])
        print(f"  ✓ keep {len(keepers)}, drop {len(dropped)}", file=out)
        gi += 1

    return to_remove


# --------------------------------------------------------------------------- #
# Removal — surgical, in-place, with a round-trip safety guard
# --------------------------------------------------------------------------- #

def format_page(header, entries):
    """Reproduce build.write_store's per-page format exactly:
         header + "\\n" + "\\n\\n".join(entries).rstrip() + "\\n\""""
    body = "\n\n".join(entries).rstrip() + "\n"
    return header + "\n" + body + "\n"


def rewrite_page(path, drop_indices):
    """Remove the entries at 0-based `drop_indices` from one src page, in place.

    Aborts (raises) without writing if the page doesn't round-trip through the
    standard format — i.e. if it looks hand-edited — so we never corrupt a file
    we don't understand."""
    text = path.read_text(encoding="utf-8")
    header = text.split("\n", 1)[0]
    entries = build.collect_quotes(text)
    if format_page(header, entries) != text:
        raise RuntimeError(
            f"{path.name}: page format is non-standard (hand-edited?). "
            f"Refusing to rewrite it — remove that quote by hand."
        )
    kept = [e for i, e in enumerate(entries) if i not in drop_indices]
    new_text = format_page(header, kept) if kept else header + "\n"
    path.write_text(new_text, encoding="utf-8")
    return len(entries) - len(kept)


def write_backup(items, to_remove):
    """Snapshot every removed quote to a markdown file so they're recoverable."""
    import datetime
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = build.ROOT / f"dedup-removed-{stamp}.md"
    by_key = {r["key"]: r for r in items}
    lines = [
        f"<!-- {len(to_remove)} quote(s) removed by dedup.py on {stamp}. "
        "Each block names the file + index it came from. -->",
        "",
    ]
    for key in sorted(to_remove):
        rec = by_key[key]
        lines.append(f"<!-- from {rec['file'].name} #{rec['idx'] + 1} -->")
        lines.append(rec["raw"].strip())
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def apply_removals(items, to_remove, dry_run, out):
    """Edit src/ pages and regenerate docs/data/quotes.js.

    Prints the full list of quotes being removed (location + text preview) so
    there is a visible record of exactly what gets deleted."""
    by_key = {r["key"]: r for r in items}
    by_file = {}
    for key in to_remove:
        path_str, idx = key
        by_file.setdefault(path_str, set()).add(idx)

    print(f"\n  Removing {len(to_remove)} quote(s) across {len(by_file)} file(s):",
          file=out)
    for key in sorted(to_remove):
        rec = by_key[key]
        print(f"    • {rec['file'].name} #{rec['idx'] + 1}  {one_line(rec['disp'])}",
              file=out)
    print("  (full text of each is also saved to dedup-removed-<stamp>.md)",
          file=out)

    if dry_run:
        print("  (dry-run — nothing will be written)", file=out)
        return

    confirm = input("\n  Proceed with removal? [y/N] ").strip().lower()
    if confirm != "y":
        print("  aborted — nothing changed.", file=out)
        return

    backup = write_backup(items, to_remove)
    removed = 0
    for path_str, idxs in by_file.items():
        removed += rewrite_page(Path(path_str), idxs)

    # Regenerate site data from the now-trimmed store.
    remaining = build.write_site_data(build.read_store())
    print(f"\n  done: removed {removed}, {remaining} quotes remain on the site.",
          file=out)
    print(f"  backup: {backup.name}", file=out)
    print("  review with `git diff src/`, then commit when happy.", file=out)


# --------------------------------------------------------------------------- #
# Report mode (change nothing)
# --------------------------------------------------------------------------- #

def write_report(items, groups, edge, threshold, path):
    lines = [
        f"# Duplicate-quote report",
        f"",
        f"- threshold: {threshold:.0%}",
        f"- {len(groups)} duplicate group(s) covering "
        f"{sum(len(g) for g in groups)} of {len(items)} quotes",
        f"- generated by `python3 dedup.py --report`",
        "",
    ]
    for gi, group in enumerate(groups, 1):
        keep = pick_keep(items, group)
        strength = group_strength(group, edge)
        lines.append(f"## Group {gi} — {len(group)} quotes — best match {strength:.0%}")
        for k in group:
            rec = items[k]
            mark = "★" if k == keep else " "
            lines.append(
                f"{mark} `{rec['file'].name} #{rec['idx'] + 1}` "
                f"({len(rec['disp'])} chars):"
            )
            lines.append(f"  > {rec['disp']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_scan(args):
    items = load_entries()
    print(f"Loaded {len(items)} quotes from {build.SRC}/", file=sys.stderr)
    print(f"Comparing at ≥ {args.threshold:.0%} similarity "
          f"(prefilter {'off' if args.exhaustive else 'on'})…", file=sys.stderr)
    groups, edge = find_groups(items, args.threshold, not args.exhaustive)
    n_in_groups = sum(len(g) for g in groups)
    print(
        f"Found {len(groups)} duplicate group(s) spanning {n_in_groups} quotes "
        f"(of {len(items)}).",
        file=sys.stderr,
    )
    if not groups:
        print("Nothing similar above the threshold — all clear. ✓", file=sys.stderr)
        return

    if args.report:
        path = build.ROOT / "dedup-report.md"
        write_report(items, groups, edge, args.threshold, path)
        print(f"Wrote report → {path.name}", file=sys.stderr)
        print("(review it; nothing on disk has changed.)", file=sys.stderr)
        return

    # Resolve groups — either interactively, or automatically (keep longest).
    to_review = groups if args.limit is None else groups[:args.limit]

    if args.auto:
        to_remove = auto_removals(items, to_review)
        print(
            f"--auto: keeping the longest in each of {len(to_review)} group(s); "
            f"{len(to_remove)} quote(s) will be removed.", file=sys.stderr
        )
    else:
        strongest = group_strength(to_review[0], edge) if to_review else 0.0
        print(
            f"Strongest match {strongest:.0%}. Reviewing "
            f"{len(to_review)} group(s) now — Enter to start, Ctrl-C to abort.",
            file=sys.stderr,
        )
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            print("Aborted — nothing changed.", file=sys.stderr)
            return
        to_remove = review_groups(items, to_review, edge, args.limit, sys.stdout)

    if not to_remove:
        print("\nNothing marked for removal. ✓", file=sys.stderr)
        return
    apply_removals(items, to_remove, args.dry_run, sys.stdout)


def cmd_selftest(args):
    """Confirm every src page round-trips through format_page(), which is the
    safety guard removal relies on."""
    items = load_entries()
    bad = 0
    for path in sorted(build.SRC.glob("quotes-*.md")):
        text = path.read_text(encoding="utf-8")
        header = text.split("\n", 1)[0]
        entries = build.collect_quotes(text)
        if format_page(header, entries) != text:
            print(f"  NON-STANDARD: {path.name}", file=sys.stderr)
            bad += 1
    if bad:
        print(f"selftest: {bad} page(s) do not round-trip — removal would skip them.",
              file=sys.stderr)
    else:
        print(f"selftest OK — all {len(list(build.SRC.glob('quotes-*.md')))} pages "
              f"round-trip cleanly ({len(items)} quotes).", file=sys.stderr)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    p = argparse.ArgumentParser(
        description="Find and interactively prune near-duplicate quotes."
    )
    p.add_argument("--threshold", type=float, default=THRESHOLD_DEFAULT,
                   help="similarity cutoff, 0..1 (default %(default)s)")
    p.add_argument("--report", action="store_true",
                   help="just write dedup-report.md, change nothing")
    p.add_argument("--auto", action="store_true",
                   help="no prompts: keep the longest in every group, print deletions, then remove")
    p.add_argument("--limit", type=int, default=None,
                   help="only review the N strongest groups")
    p.add_argument("--dry-run", action="store_true",
                   help="review and show what would change, but don't write")
    p.add_argument("--exhaustive", action="store_true",
                   help="disable the token prefilter (much slower; only if you suspect misses)")
    p.add_argument("selftest", nargs="?", default=None,
                   help="pass 'selftest' to verify page round-trip and exit")
    args = p.parse_args()

    if args.selftest == "selftest":
        cmd_selftest(args)
        return
    cmd_scan(args)


if __name__ == "__main__":
    main()
