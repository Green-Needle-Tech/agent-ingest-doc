# agent-ingest-doc

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill that ingests documents — PDF, URL, Markdown, .txt, .docx, pasted text — into a three-layer memory architecture:

- **L3 (LLM wiki)** — full content: immutable raw sources (sha256 + drift detection) + synthesized, cross-linked wiki pages
- **L2 (Hindsight)** — one episode *pointer* per ingest (never the content)
- **L1 (local MEMORY.md)** — only durable behavior-changing facts, guarded by a 75% capacity rule

Based on the [Karpathy LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) for the L3 layer.

## Install

```bash
mkdir -p ~/.hermes/skills/research
git clone https://github.com/david6055my/agent-ingest-doc.git ~/.hermes/skills/research/doc-ingest
```

## Usage

Just ask your Hermes agent:
- "Ingest this PDF into the knowledge base"
- "Add this URL to the wiki"
- "Ingest these docs" (bulk mode)

The skill handles orientation, raw capture with frontmatter, page synthesis with
cross-links and contradiction handling, index/log navigation updates, the L2
Hindsight pointer retain, and reporting.

### Extraction ladder

Capture picks the **lowest rung the document survives** (verified against 2026
PDF-parsing benchmarks):

1. **Text layer** — `pymupdf4llm` for PDFs, `read_file` for .docx, `web_extract` for URLs. Free, milliseconds.
2. **Layout models** — `docling` (tables/multi-column) or `marker` (GPU speed) when rung 1 output is scrambled.
3. **OCR/vision** — `pdftoppm` + `tesseract`, or a vision model, for scans and photos.

### Helper script

`scripts/capture_raw.py` (stdlib-only) writes the raw file with `source_url` /
`ingested` / `sha256` frontmatter and detects re-ingest drift
(identical → skip; changed → flag + version bump):

```bash
python3 scripts/capture_raw.py --wiki ~/wiki --title "Doc Title" \
  --source-url https://example.com/doc < extracted.txt
```

## Workflow

1. **Orient** — read SCHEMA.md / index.md / log.md (prevents duplicates)
2. **Capture** — route by type (web_extract, PDF text-layer, OCR fallback, verbatim copy); sha256 frontmatter
3. **Check coverage** — search existing pages, apply page thresholds
4. **Write/update wiki pages** — wikilinks, provenance markers, confidence levels
5. **Update navigation** — index.md + append-only log.md
6. **Retain L2 pointer** — episode metadata via `hindsight_retain`
7. **Report** — every file created/updated

## License

MIT
