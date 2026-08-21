---
name: doc-ingest
description: "Use when ingesting docs into the wiki and Hindsight memory."
version: 2.0.0
author: David (david6055my), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [ingest, knowledge-base, llm-wiki, hindsight, memory, documentation]
    category: research
    related_skills: [llm-wiki]
---

# Document Ingestion Skill (L2 Hindsight + L3 LLM Wiki)

Ingests PDFs, URLs, Markdown, .txt, .docx, and pasted text into a three-layer
memory: full content into the LLM wiki (L3), one episode pointer into Hindsight
(L2), nothing into local memory (L1) unless a durable routing fact changed.
Builds on the Karpathy LLM Wiki pattern — knowledge is compiled once and kept
current, not re-derived per query.

## When to Use

- User asks to ingest, add, import, or file a document/source into the knowledge base
- User sends a document with intent to store it ("save this for later", "add to the wiki")
- Bulk ingest of multiple documents

Don't use for: live web questions (use search skills), or pure conversational
memory (Hindsight auto-retain handles that).

## Prerequisites

- Wiki at `$WIKI_PATH` (default `~/wiki`) — orient first; if absent, initialize
  per the `llm-wiki` skill before ingesting
- Hindsight at `$HINDSIGHT_URL` (default `http://localhost:8888`), bank `main`
- Extraction tools: `pymupdf` (rung 1); `docling`/`marker` if installed (rung 2);
  `tesseract`/`pdftoppm` or `vision_analyze` for OCR (rung 3)

## Procedure

1. **Orient.** `read_file` on `$WIKI/SCHEMA.md`, `$WIKI/index.md`, and the last
   ~30 lines of `$WIKI/log.md`. *Done when:* you know the domain, tag taxonomy,
   existing pages, and recent activity.
2. **Capture raw → `$WIKI/raw/`.** Use the extraction ladder — pick the LOWEST
   rung the document survives (details in `references/extraction-ladder.md`):
   - Rung 1 — text layer: `pymupdf`/`pymupdf4llm` for PDFs, `read_file` for
     .docx, `web_extract` for URLs, verbatim copy for .md/.txt
   - Rung 2 — layout models: `docling`/`marker` when tables/multi-column break rung 1
   - Rung 3 — OCR/vision: `pdftoppm` + `tesseract` or `vision_analyze` for scans
   Prepend frontmatter (`source_url`, `ingested`, `sha256` of body). Prefer
   `scripts/capture_raw.py` — it computes the hash and handles re-ingest drift.
   *Done when:* raw file saved, non-empty body verified, sha256 recorded.
   If the source already exists in `raw/`: identical hash → skip capture,
   update pages only; different hash → flag drift to user, save as `-v2`.
3. **Check coverage.** `search_files` across `entities/ concepts/ comparisons/`
   for mentioned entities. Apply the wiki's Page Thresholds (2+ source mentions
   or central to one source). *Done when:* you have a create-vs-update list.
4. **Write/update wiki pages.** Frontmatter (title, created, updated, type,
   tags from taxonomy, sources), ≥2 outbound `[[wikilinks]]` per new page,
   reverse links on updated pages, `^[raw/...]` provenance markers on pages
   synthesizing 3+ sources, `confidence: medium|low` for single-source or
   fast-moving claims. Contradictions: keep BOTH claims with dates, mark
   `contested: true` — never silently overwrite. *Done when:* every page on
   the list written and cross-linked.
5. **Update navigation.** New pages into `index.md` under the correct section
   (alphabetical); update Total pages + Last updated. Append ONE log entry:
   `## [YYYY-MM-DD] ingest | <Title>` listing every file created/updated —
   keep the `## [date] action | subject` prefix format so the log stays
   greppable. *Done when:* index count matches files on disk.
6. **Retain L2 pointer.** One `hindsight_retain` per ingest (or one batch
   retain for bulk): title, URL/path, domain, pages created/updated, key
   entities, wiki path. NEVER retain document content — that is L3's job.
   If Hindsight is down (health check fails), queue the retain text in
   `$WIKI/raw/.pending-retains.md` and retry next session. *Done when:*
   retain confirmed (or queued).
7. **Report.** One line per file created/updated: raw source, wiki pages,
   index.md, log.md, retain confirmation. Include any drift/contradiction flags.
8. **L1 promotion (rare).** Only if the ingest changes durable agent behavior
   (e.g., new wiki domain) AND local memory is under 75% capacity. One
   declarative line. Otherwise skip silently.

## Bulk Ingest

Read all sources → identify entities across all of them → ONE search pass
against existing pages → create/update in one pass → single index.md update →
single log entry → one retain per source. Ask the user before touching 10+
existing pages.

## Pitfalls

- Never modify `raw/` after ingest — corrections go in wiki pages
- Never skip index.md/log.md — the wiki degrades into duplicate piles
- Never push document content into L2 or L1
- Scanned PDFs yield empty text at rung 1 — detect empty extraction and climb
  the ladder; never save an empty raw file
- `web_extract` may be rate-limited — fallback: curl + Python html.parser
  (see `llm-wiki` skill references)
- Distinguish the two failure modes on re-ingest: hash drift (source changed)
  vs. wiki-page contradiction (sources disagree) — handle each per its rule
- LLMs can't read inline markdown images in one pass — file image references
  as paths, view them separately only if they carry content
- Git-version the wiki if configured — raw/ immutability makes clean diffs

## Verification

- Raw file exists, body non-empty, sha256 in frontmatter matches recomputed hash
- Every new/updated page appears in `index.md`; index total == files on disk
- Log entry appended with the parseable prefix format
- Hindsight retain confirmed or queued with a reason
- Report lists every file touched — none unaccounted for

## References

- `references/extraction-ladder.md` — per-format extraction commands and rung decision rules
- `scripts/capture_raw.py` — capture helper: frontmatter, sha256, drift detection
- `llm-wiki` skill — wiki structure, SCHEMA templates, lint procedure
