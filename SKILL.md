---
name: doc-ingest
description: "Use when ingesting docs into the wiki and Hindsight memory."
version: 2.3.1
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
   `scripts/capture_raw.py` — it computes the hash and handles re-ingest
   drift (matched by slug in the target subdir AND by `source_url` across
   all raw/ subdirs). Hash scope: the sha256 covers the stored body exactly
   as written after frontmatter and the `# Title` heading — verify by
   stripping both and recomputing (see `scripts/verify_raw.py`).
   *Done when:* raw file saved, non-empty body verified, sha256 recorded.
   If the source already exists in `raw/`: identical hash → skip capture,
   update pages only; different hash → flag drift to user, save as `-vN`
   (original unsuffixed file is v1, first drift is `-v2`, next `-v3`).
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
   The pointer pattern is deliberate (Hindsight × LLM Wiki research, Aug 2026):
   recall surfaces the pointer and the agent opens the page, so Hindsight acts
   as the semantic search layer over the wiki (including temporal and
   entity-multi-hop queries the wiki's index can't answer) without duplicating
   content or paying double consolidation cost. Tag pointers `wiki-ref` plus
   topic tags so page-scoped Hindsight features (e.g. Knowledge Pages tag
   groups) can consume them. If a page is later archived/moved/renamed,
   invalidate its stale pointer (`PATCH /memories/{id}`) — recall-verified.
   If Hindsight is down (health check fails), queue the retain text in
   `$WIKI/raw/.pending-retains.md` and retry next session. On Hindsight ≥ v0.9,
   a recall of the pointer phrase should return the episode — cheap sanity
   check that the retain landed. *Done when:* retain confirmed (or queued).
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

Run `scripts/verify_raw.py --wiki $WIKI` after each ingest. It checks:

- Raw file body non-empty (>=50 chars), sha256 in frontmatter matches the
  recomputed hash of the stored body (frontmatter + title heading stripped)
- source_url present; no duplicate source_url across **separate version
  chains** (drift versions within one chain — doc.md, doc-v2.md — share
  the same source_url and are valid)
- index.md "Total pages" matches page files on disk (when declared)
- log.md last entry matches `## [YYYY-MM-DD] action | subject` format

**Not checked by verify_raw.py** (manual or future work):
- Every new/updated page appears in index.md (only total count is checked)
- Wiki links resolve
- Hindsight retain confirmed or queued
- Report lists every file touched
- Page frontmatter follows schema

`verify_raw.py` exits 0 on pass, 1 on failure; `--json` for machine-readable
output. Tests: `tests/test_capture_raw.py` (`python3 -m pytest tests/ -q`
or `tests/run_tests.sh` stdlib fallback).

## L2 ↔ L3 integration patterns (Hindsight × LLM Wiki research, Aug 2026)

This skill implements Pattern 1 of four; know the others to know when to escalate:

1. **Pointer retention (this skill's default).** Full content → wiki (L3), one tagged pointer → Hindsight (L2). Hindsight's recall (TEMPR: semantic + BM25 + graph + temporal, cross-encoder reranked) finds the pointer; the wiki holds the knowledge. Cleanest separation, lowest LLM cost.
2. **Wiki as Hindsight raw layer.** For high-value curated corpora, also push wiki pages through Hindsight's documents API — gaining temporal queries, entity multi-hop traversal, and automatic contradiction reconciliation across pages (a static wiki keeps contradictions "a paragraph apart"; Hindsight consolidation resolves them into observations with evidence quotes). Use sparingly: every retained token is extracted, consolidated, and reranked forever.
3. **Dual-store role separation.** At query time, route: question maps to a known wiki page → read the page (index-first, deterministic); temporal / personal / entity-relational question → `hindsight_recall`/`reflect`. Ingest writes to both stores per this skill; queries pick by shape.
4. **Knowledge Pages (Hindsight ≥ v0.9) — the reverse projection.** Hindsight can render its OWN wiki: `hindsight fs mount --bank <bank>` projects self-updating markdown pages (built from consolidated observations only, delta-edited on each consolidation, never reading sibling pages). A page is a projected view over memory — delete it and it re-projects from facts. Not a replacement for this skill's curated wiki: raw sources stay the truth about *what was said*; knowledge pages are the reconciled truth about *what holds*. Useful as an auto-maintained companion view of a bank's operational memory.
   **Bridge optimization (verified live on v0.9.1, Aug 2026):** the `wiki-ref`-tagged pointers this skill retains are consolidated observations — so a Knowledge Page created with `tags: ["wiki-ref"]` (e.g. "Ingest Index", source_query "Which documents have been ingested into the wiki?") becomes a self-maintaining, delta-refreshed, document-level searchable index over the ingest history. Create it once per bank via `POST /knowledge-base/pages`, poll the returned `operation_id`, and thereafter surface `is_stale: true` pages in bulk-ingest reports. Search (`GET /knowledge-base/search?q=`) is the agent's fast first lookup; recall stays for specific facts. Never hand-edit a mounted page body — refresh overwrites it; correct scope via `PATCH` on the node. Full API surface and verified-behavior notes: `references/hindsight-knowledge-pages.md`.

## References

- `references/extraction-ladder.md` — per-format extraction commands and rung decision rules
- `references/hindsight-knowledge-pages.md` — Knowledge Pages API surface + v0.9.1 verified-behavior notes (live-tested 2026-08-23)
- `scripts/capture_raw.py` — capture helper: frontmatter, sha256, drift detection, cross-subdir dedupe, `--slug` override
- `scripts/verify_raw.py` — post-ingest lint: hash round-trip, source_url presence/dupes, index/log format
- `tests/test_capture_raw.py` — 17-test suite driving both scripts end-to-end
- `llm-wiki` skill — wiki structure, SCHEMA templates, lint procedure
- Hindsight docs — [Knowledge Pages](https://hindsight.vectorize.io/developer/knowledge-pages) and [Knowledge Pages API](https://hindsight.vectorize.io/developer/api/knowledge-pages) (v0.9): projection model, `hindsight fs mount`, default trigger
- Latimer et al., [Hindsight is 20/20](https://arxiv.org/abs/2512.12818) (arXiv:2512.12818) — retain/recall/reflect architecture, TEMPR retrieval, 91.4% LongMemEval
- Karpathy, [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (Apr 2026) — compile-once vs RAG-per-query, index-first scaling
