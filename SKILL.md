---
name: doc-ingest
description: "Use when ingesting docs into the wiki and Hindsight memory."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [ingest, knowledge-base, llm-wiki, hindsight, memory, documentation]
    category: research
    related_skills: [llm-wiki, hindsight-memory, ocr-and-documents]
---

# Document Ingestion Skill (L2 Hindsight + L3 LLM Wiki)

## When to Use

Trigger when the user asks to ingest, add, import, or file a document or source
— PDF, URL/webpage, .MD, .txt, .docx, or pasted text — into the knowledge base,
or mentions "ingest this doc", "add to the wiki", or "store this for later".
Also use for bulk ingestion of multiple documents.

Ingests documents (PDF, URL, .MD, .txt, .docx, pasted text) into the three-layer
memory architecture:

- **L3 (LLM wiki)**: full content — raw source (immutable) + synthesized wiki pages
- **L2 (Hindsight)**: one episode pointer per ingest (NOT the content)
- **L1 (local MEMORY.md)**: only if the ingest changes durable agent routing facts — usually nothing

## Paths

- Wiki: `${WIKI_PATH:-$HOME/wiki}` (default `~/wiki`)
- Hindsight: localhost:8888, bank `main`, via `hindsight_retain` tool

## Workflow

### 0. Orient (mandatory for existing wiki)
```bash
read_file "$WIKI/SCHEMA.md"
read_file "$WIKI/index.md"
read_file "$WIKI/log.md" offset=<last 30 lines>
```
Prevents duplicate pages and missed cross-references.

### 1. Capture raw source → L3 `raw/`

Route by type:
- **URL** → `web_extract` → save markdown to `raw/articles/<author-or-domain>-<topic>-YYYY-MM.md`
- **PDF (URL)** → `web_extract` handles PDFs directly → `raw/papers/`
- **PDF (local, text layer)** → `read_file` (auto-converts) or pymupdf → `raw/papers/`
- **PDF (scanned, no text)** → render pages with pdftoppm + OCR (tesseract / vision_analyze) before saving
- **.MD / .txt** → copy verbatim to `raw/articles/` (or `raw/transcripts/` for notes)
- **.docx** → `read_file` auto-extracts; save extracted text to `raw/articles/`

Every raw file gets this frontmatter (prepend to body):
```yaml
---
source_url: <url or "local file: /path">
ingested: YYYY-MM-DD
sha256: <hex digest of the body BELOW the frontmatter>
---
```
```python
# compute sha over body only (everything after the closing '---' line + blank line)
import hashlib
sha = hashlib.sha256(body_bytes).hexdigest()
```

**Re-ingest check:** if the file already exists in `raw/`, recompute sha256:
- identical → skip capture, still update wiki pages if new takeaways
- different → source drifted: flag to user, save new version with `-v2` suffix, update pages

### 2. Extract takeaways + check existing coverage
- Summarize what's new/interesting (skip the discussion step in cron contexts)
- `search_files` across `entities/ concepts/ comparisons/` for mentioned entities
- Apply Page Thresholds from SCHEMA.md: create a page only when an entity/concept
  appears in 2+ sources OR is central to one source. Otherwise append to existing pages.

### 3. Write/update wiki pages (L3)
- New pages: YAML frontmatter (title, created, updated, type, tags from taxonomy, sources), ≥2 outbound `[[wikilinks]]`
- Existing pages: append new facts, bump `updated`, add reverse links
- Contradictions: keep both claims with dates, set `contested: true` / `contradictions:` frontmatter — never silently overwrite
- Provenance: on pages synthesizing 3+ sources, append `^[raw/articles/source.md]` markers
- Confidence: single-source or fast-moving claims → `confidence: medium|low`

### 4. Update navigation (L3 backbone)
- Add new pages to `index.md` under correct section, alphabetically; update Total pages + Last updated
- Append one log entry:
```markdown
## [YYYY-MM-DD] ingest | <Source Title>
- Source: <url/path>
- Created raw: raw/.../file.md
- Pages created: entities/..., concepts/...
- Pages updated: ...
```

### 5. Register episode pointer → L2 (hindsight_retain)
One retain per ingest, metadata not content:
```
hindsight_retain: "Ingested '<Doc Title>' (<url/path>) into LLM wiki on YYYY-MM-DD.
Domain: <topic>. Created/updated pages: <list>. Key entities: <names>.
Wiki path: raw/.../file.md. Query the wiki (~/wiki) for details."
```
- NEVER retain full document text into Hindsight — that is L3's job
- The retain exists so future recall surfaces "this content lives in the wiki"

### 6. L1 promotion (rare)
Only if the ingest changes durable agent behavior — e.g. "Wiki domain now covers X".
One declarative line, only if MEMORY.md is under 75% capacity. Otherwise skip.

### 7. Report
List every file created/updated: raw source, wiki pages, index.md, log.md, and the
Hindsight retain confirmation. One line per file.

## Bulk Ingest
Batch: read all sources → identify entities across all of them → one search pass
against existing pages → create/update in one pass → single index.md update →
single log entry → one Hindsight retain per source (or one batch retain).
Ask the user before an ingest touches 10+ existing pages.

## Pitfalls
- Never modify anything in `raw/` after ingest — corrections go in wiki pages
- Never skip index.md/log.md — the wiki degrades into a pile of duplicates
- Never push document content into L2 Hindsight or L1 memory
- Always orient (step 0) before writing — duplicates and missed links are the #1 failure
- Scanned PDFs with no text layer yield nothing via text extraction — detect
  empty extraction and switch to OCR instead of saving an empty raw file
- web_extract may be unavailable/rate-limited — fallback: curl + Python
  html.parser (see llm-wiki skill references)
- Large PDFs: extract per-chapter, keep raw under ~500KB per file where practical
- Verify Hindsight health (GET localhost:8888/health) before retain; if down,
  queue the retain text in `raw/.pending-retains.md` and retry next session
