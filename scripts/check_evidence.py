#!/usr/bin/env python3
"""Mechanical evidence check for a Karpathy-style LLM wiki.

Vendored from astro-han/karpathy-llm-wiki (MIT, (c) 2026 Yuhan Lei),
unmodified — https://github.com/astro-han/karpathy-llm-wiki — so this
skill can run the karpathy-llm-wiki lint without a separate install.
Upstream is the source of truth; re-sync when it changes.

Report-only; never modifies files. Three sweeps:

1. Fidelity — extract candidate literals (specific numbers, ISO dates,
   direct quotes) from each wiki article and verify that each candidate
   appears verbatim in the body of the raw files linked by that
   article's Raw field. Misses are listed as suspects. Derived values,
   product names, and deliberate paraphrases will show up as suspects;
   judging them is the reader's job, not this script's.
2. Evidence errors — articles that cannot be verified at all: a missing
   Raw field on a non-archive article, Raw links that do not resolve,
   or Raw links that escape raw/ (evidence must live in immutable raw/).
3. Inventory — raw files that no article's Raw field references,
   excluding files whose ingest was logged as "no material".

Coverage boundary (closed candidate set, frozen): candidates are
- quotes of 15+ characters (double-quoted spans and body blockquotes)
- ISO dates (YYYY-MM-DD, YYYY-MM)
- specific numbers: thousands-grouped (10,000), dotted (2.1.80, 3.14),
  suffixed (42K, 99.9%), or 4+ digits (2026)
Small plain integers ("42", "500") and exotic forms (signs, currencies,
spelled-out dates) are deliberately not checked; they belong to the
compile-time locate-before-write rule and to judgment review. New prose
forms extend this list in the docstring, not the regexes.

The exit code carries no information; the report is the interface.

Usage: check_evidence.py [project-root] [article.md ...]
Defaults: project-root is the current directory; all wiki/**/*.md
articles except index.md and log.md are checked. Article paths may be
absolute or relative to the project root.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

NUMBER_TOKEN_RE = re.compile(
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)*(?:\s*[KMB%](?![A-Za-z]))?"
    r"|\d+(?:\.\d+)*(?:\s*[KMB%](?![A-Za-z]))?)(?![A-Za-z])"
)
SUFFIX_RE = re.compile(r"[KMB%]$")
DATE_RE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
QUOTE_RES = [re.compile(r'"([^"\n]*)"'), re.compile(r"“([^”\n]*)”")]
METADATA_RE = re.compile(r"^>\s*(Sources?|Raw|Collected|Published|Updated|Archived):")
STATUS_LINE_RE = re.compile(r"^>\s*\*\*Status:")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
PAREN_RE = re.compile(r"\(([^()]*)\)")
NO_MATERIAL_HEADING_RE = re.compile(
    r"^## \[[^\]]*\]\s*ingest\s*\|\s*no material:\s*(\S+)", re.IGNORECASE
)
ARCHIVED_RE = re.compile(r"^>\s*Archived:")
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`+|~+)(.*)$")
FENCE_OPEN_MIN = 3  # a fence needs >= 3 backticks or tildes
FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
WS_RE = re.compile(r"\s+")

SKIP_FILES = {"index.md", "log.md"}


@dataclass(frozen=True)
class Document:
    title: str | None
    header: tuple[str, ...]
    body: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    kind: str
    value: str


def normalize(text: str) -> str:
    return WS_RE.sub(" ", text).strip()


def fence_opener(line: str) -> tuple[str, int] | None:
    m = FENCE_OPEN_RE.match(line)
    if not m:
        return None
    marker, info = m.groups()
    if len(marker) < FENCE_OPEN_MIN:
        return None
    if marker[0] == "`" and "`" in info:
        return None
    return marker[0], len(marker)


def is_fence_closer(line: str, char: str, length: int) -> bool:
    m = FENCE_CLOSE_RE.match(line)
    return bool(m and m.group(1)[0] == char and len(m.group(1)) >= length)


def _feed_before_title(line: str, title: str, header: list,
                       preamble: list, body: list) -> str:
    """Handle a non-fence line while still looking for the first H1."""
    if line.startswith("# "):
        return "after_title"
    preamble.append(line)
    return "before_title"


def _feed_after_title(line: str, title: str, header: list,
                      preamble: list, body: list) -> str:
    """Handle the first non-blank line after the title: header or body."""
    if not line.strip():
        return "after_title"
    if line.strip().startswith(">"):
        header.append(line)
        return "header"
    body.append(line)
    return "body"


def _feed_header(line: str, title: str, header: list,
                 preamble: list, body: list) -> str:
    """Handle a line inside the metadata blockquote block."""
    if line.strip().startswith(">"):
        header.append(line)
        return "header"
    body.append(line)
    return "body"


def _feed_body(line: str, title: str, header: list,
               preamble: list, body: list) -> str:
    """Handle a body line."""
    body.append(line)
    return "body"


_STATE_HANDLERS = {
    "before_title": _feed_before_title,
    "after_title": _feed_after_title,
    "header": _feed_header,
    "body": _feed_body,
}


def parse_document(text: str) -> Document:
    """Return the visible title, metadata header, and body.

    The metadata header is only the contiguous blockquote immediately
    after the first H1 outside a fence. A fence is a body boundary; its
    removal must not promote a later blockquote into the header.
    """
    title = None
    header = []
    preamble = []
    body = []
    state = "before_title"
    fence_char = None
    fence_len = 0

    for line in text.splitlines():
        if fence_char:
            if is_fence_closer(line, fence_char, fence_len):
                fence_char = None
            continue
        opener = fence_opener(line)
        if opener:
            fence_char, fence_len = opener
            if state == "after_title":
                state = "body"
            continue
        new_state = _STATE_HANDLERS[state](line, title, header, preamble, body)
        if state == "before_title" and new_state == "after_title":
            title = line
        state = new_state

    return Document(title, tuple(header), tuple(preamble + body))


def strip_fences(text: str) -> str:
    """Remove Standard Markdown fenced code blocks."""
    out = []
    fence_char = None
    fence_len = 0
    for line in text.splitlines():
        if fence_char:
            if is_fence_closer(line, fence_char, fence_len):
                fence_char = None
            continue
        opener = fence_opener(line)
        if opener:
            fence_char, fence_len = opener
            continue
        out.append(line)
    return "\n".join(out)


def strip_noise(text: str) -> str:
    text = INLINE_CODE_RE.sub(" ", text)
    text = LINK_RE.sub(r"\1", text)
    return text


def keep_number(token: str) -> bool:
    token = token.strip()
    if SUFFIX_RE.search(token) or "," in token or "." in token:
        return True
    return len(token) >= 4


def extract_numeric_date_candidates(line: str) -> list[Candidate]:
    line = strip_noise(line)
    date_matches = list(DATE_RE.finditer(line))
    candidates = [Candidate("date", m.group(0)) for m in date_matches]
    number_text = list(line)
    for match in date_matches:
        number_text[match.start() : match.end()] = " " * (match.end() - match.start())
    candidates.extend(
        Candidate("number", m.group(0))
        for m in NUMBER_TOKEN_RE.finditer("".join(number_text))
        if keep_number(m.group(0))
    )
    return candidates


def _feed_blockquote_line(stripped: str, blockquote: list,
                          candidates: list, flush_paragraph) -> None:
    """Handle a blockquote line during candidate extraction."""
    flush_paragraph()
    content = strip_noise(stripped.lstrip(">").strip())
    blockquote.append(content)
    candidates.extend(extract_numeric_date_candidates(content))


def _feed_text_line(line: str, stripped: str, blockquote: list,
                    paragraph: list, candidates: list,
                    flush_blockquote, flush_paragraph) -> None:
    """Handle a non-blockquote line during candidate extraction."""
    flush_blockquote()
    if not stripped:
        flush_paragraph()
        return
    line = strip_noise(line)
    candidates.extend(extract_numeric_date_candidates(line))
    paragraph.append(line)


def extract_candidates(text: str) -> list[Candidate]:
    document = parse_document(text)
    lines = ([document.title] if document.title else []) + [
        line for line in document.header if not METADATA_RE.match(line.strip())
    ] + list(document.body)
    candidates: list[Candidate] = []
    skip_status_block = False
    blockquote: list[str] = []
    paragraph: list[str] = []

    def flush_blockquote():
        if blockquote:
            joined = normalize(" ".join(blockquote))
            if len(joined) >= 15:
                candidates.append(Candidate("quote", joined))
            blockquote.clear()

    def flush_paragraph():
        if paragraph:
            joined = normalize(" ".join(paragraph))
            for quote_re in QUOTE_RES:
                candidates.extend(
                    Candidate("quote", m.group(1))
                    for m in quote_re.finditer(joined)
                    if len(m.group(1).strip()) >= 15
                )
            paragraph.clear()

    for line in lines:
        stripped = line.strip()
        if STATUS_LINE_RE.match(stripped):
            flush_blockquote()
            flush_paragraph()
            skip_status_block = True
            continue
        if skip_status_block:
            if stripped.startswith(">"):
                continue
            skip_status_block = False
        if stripped.startswith(">"):
            _feed_blockquote_line(stripped, blockquote, candidates,
                                  flush_paragraph)
            continue
        _feed_text_line(line, stripped, blockquote, paragraph, candidates,
                         flush_blockquote, flush_paragraph)
    flush_blockquote()
    flush_paragraph()
    seen = set()
    unique = []
    for candidate in candidates:
        value = candidate.value.strip().strip(".,;:()[]")
        candidate = Candidate(candidate.kind, value)
        if value and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def raw_link_target(inner: str) -> str | None:
    """Return the .md path inside a parenthesised link target, else None.

    Replaces the former regex r"\\(([^)]+\\.md)[^)]*\\)" whose nested
    character-class quantifiers had super-linear backtracking on inputs
    with many ".md" runs (Sonar python:S8786). This scan is linear.
    """
    target = inner
    for sep in ("?", "#"):
        cut = target.find(sep)
        if cut != -1:
            target = target[:cut]
    if target.endswith(".md") and len(target) > 3:
        return target
    return None


def raw_links_of(article_text: str) -> list[str]:
    """Raw links come only from the metadata header; identical lines in
    the body or in code fences are content, not fields."""
    links = []
    for line in parse_document(article_text).header:
        if re.match(r"^>\s*Raw:", line.strip()):
            for m in PAREN_RE.finditer(line):
                target = raw_link_target(m.group(1))
                if target:
                    links.append(target)
    return links


def contains(haystack: str, candidate: Candidate) -> bool:
    if candidate.kind == "quote":
        return candidate.value in haystack
    # Values must stand on their own, while sentence punctuation remains
    # valid. A month may not pass as the prefix of a full ISO date.
    right = r"(?!-\d{2})" if candidate.kind == "date" and len(candidate.value) == 7 else ""
    pattern = (
        r"(?<![\d.,])" + re.escape(candidate.value) + right + r"(?![A-Za-z0-9]|[.,]\d|%)"
    )
    return re.search(pattern, haystack) is not None


def source_content(path: Path) -> str:
    """Raw file body with the metadata header removed. Collection
    metadata (Source/Collected/Published) is bookkeeping, not evidence;
    letting it match candidates would false-pass dates and years."""
    document = parse_document(path.read_text(encoding="utf-8"))
    return normalize("\n".join(document.body))


def resolve_wiki_path(root: Path, arg: str) -> Path:
    """Resolve a CLI-supplied article path and validate it stays inside root.

    The path is joined onto root and checked BEFORE any filesystem access,
    so a faulty CLI argument (e.g. one containing '..') cannot escape the
    wiki tree the checker is allowed to read.
    """
    candidate = Path(arg)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"path escapes wiki root: {arg}")
    return resolved


def _resolve_raw_links(article: Path, links: list[str],
                       raw_root: Path) -> tuple[list[str], list[str]]:
    """Resolve each Raw link to source content; collect errors instead."""
    raws = []
    errors = []
    for link in links:
        target = (article.parent / link).resolve()
        if not target.is_relative_to(raw_root):
            errors.append(f"Raw link escapes raw/: {link}")
        elif not target.is_file():
            errors.append(f"unresolvable Raw link: {link}")
        else:
            raws.append(source_content(target))
    return raws, errors


def _find_unsupported_candidates(text: str, raws: list[str]) -> list[str]:
    """Return candidate values not found in any raw source body."""
    misses = []
    if not raws:
        return misses
    for candidate in extract_candidates(text):
        candidate = Candidate(candidate.kind, normalize(candidate.value))
        if not any(contains(raw, candidate) for raw in raws):
            misses.append(candidate.value)
    return misses


def check_article(article: Path, root: Path) -> tuple[list[str], list[str]]:
    """Return (fidelity suspects, evidence errors) for one article."""
    text = article.read_text(encoding="utf-8")
    links = raw_links_of(text)
    if not links:
        if any(ARCHIVED_RE.match(line.strip()) for line in parse_document(text).header):
            return [], []
        return [], ["article has no Raw field"]
    raw_root = (root / "raw").resolve()
    raws, errors = _resolve_raw_links(article, links, raw_root)
    misses = _find_unsupported_candidates(text, raws)
    return misses, errors


def iter_articles(wiki_dir: Path):
    for path in sorted(wiki_dir.rglob("*.md")):
        if path.relative_to(wiki_dir).as_posix() not in SKIP_FILES:
            yield path


def no_material_paths(log_file: Path) -> set[str]:
    if not log_file.is_file():
        return set()
    paths = set()
    text = strip_fences(log_file.read_text(encoding="utf-8"))
    for line in text.splitlines():
        m = NO_MATERIAL_HEADING_RE.match(line)
        if m:
            paths.add(m.group(1).strip("`,;."))
    return paths


def referenced_raws(root: Path) -> set[Path]:
    referenced = set()
    for article in iter_articles(root / "wiki"):
        for link in raw_links_of(article.read_text(encoding="utf-8")):
            target = (article.parent / link).resolve()
            referenced.add(target)
    return referenced


def unreferenced_raws(root: Path) -> list[str]:
    raw_dir = root / "raw"
    if not raw_dir.is_dir():
        return []
    referenced = referenced_raws(root)
    disposed = no_material_paths(root / "wiki" / "log.md")
    missing = []
    for path in sorted(raw_dir.rglob("*.md")):
        if path.resolve() not in referenced and path.relative_to(root).as_posix() not in disposed:
            missing.append(path.relative_to(root).as_posix())
    return missing


def _collect_articles(argv: list[str], root: Path, wiki_dir: Path) -> list[Path]:
    """Resolve CLI article args to validated paths; empty list means all."""
    articles = []
    for arg in argv[2:]:
        try:
            path = resolve_wiki_path(root, arg)
        except ValueError as exc:
            print(f"warning: {exc}", file=sys.stderr)
            continue
        try:
            if path.relative_to(wiki_dir).as_posix() in SKIP_FILES:
                print(f"warning: {arg} is an index/log file, skipping", file=sys.stderr)
                continue
        except ValueError:
            pass
        if not path.is_file():
            print(f"warning: article not found: {arg}", file=sys.stderr)
            continue
        articles.append(path)
    return articles


def _print_section(title: str, results: dict, root: Path,
                   pick) -> tuple[int, Path]:
    """Print one report section; returns (item_count, label helper)."""
    def label(article: Path) -> Path:
        try:
            return article.resolve().relative_to(root)
        except ValueError:
            return article

    print(f"## {title}")
    count = 0
    for article, items in results.items():
        items = pick(items)
        if items:
            print(f"\n{label(article)}")
            for item in items:
                print(f"- {item}")
                count += 1
    if count == 0:
        print("\n(none)" if title == "Fidelity suspects" else "(none)")
    return count, label


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path.cwd()
    wiki_dir = root / "wiki"
    if not wiki_dir.is_dir():
        print(f"no wiki/ directory under {root}")
        return 1

    articles = _collect_articles(argv, root, wiki_dir)
    if len(argv) <= 2:
        articles = list(iter_articles(wiki_dir))

    results = {}
    for article in articles:
        results[article] = check_article(article, root)

    print("# Evidence check\n")
    suspect_count, _ = _print_section("Fidelity suspects", results, root,
                                      lambda r: r[0])
    error_count, _ = _print_section("Evidence errors", results, root,
                                    lambda r: r[1])

    print("\n## Unreferenced raw files")
    orphans = unreferenced_raws(root)
    for path in orphans:
        print(f"- {path}")
    if not orphans:
        print("(none)")

    print(
        f"\n## Summary\n{suspect_count} fidelity suspect(s), "
        f"{error_count} evidence error(s), {len(orphans)} unreferenced raw file(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
