# Extraction Ladder

Pick the LOWEST rung the document survives. Climbing rungs costs time, GPU, or
API tokens — never pay for a vision model when the text layer already has the
characters.

## Decision rule

1. Try rung 1. If extracted body is non-empty (>200 chars for a typical doc)
   and tables are not scrambled → done.
2. If body is empty (scan/photo) → jump to rung 3.
3. If body is non-empty but tables/multi-column are scrambled → rung 2.

## Rung 1 — text layer (free, milliseconds)

**PDF (digital-native):**
```python
import pymupdf4llm  # pip install pymupdf4llm
md = pymupdf4llm.to_markdown("doc.pdf")
```
or plain `pymupdf` (`fitz`) `page.get_text()`. Up to ~250x cheaper than vision
approaches. Useless on scans.

**URL:** `web_extract` → markdown. Fallback: `curl -sL "$URL" -o /tmp/page.html`
then Python `html.parser`/regex strip (see llm-wiki skill's
`references/extracting-documentation-pages.md`).

**.docx / .pptx / .xlsx:** `read_file` auto-extracts, or python-docx.

**.md / .txt:** copy verbatim.

**EPUB:** `read_file` auto-extracts (or `pandoc file.epub -t markdown`).

**Pasted text:** save directly.

## Rung 2 — layout models (local, needs pip install + optionally GPU)

Use when the text layer exists but layout breaks it: multi-column pages,
nested tables, figures interrupting flow.

- **Docling (IBM)** — strongest open option for complex tables and multi-column;
  `pip install docling` then `docling doc.pdf --to md`
- **Marker** — fastest with a GPU, strong on academic/reference-heavy PDFs;
  `pip install marker-pdf` then `marker_single doc.pdf --output_dir out/`

Both run entirely local — no per-page fee, nothing leaves the network.

## Rung 3 — OCR / vision (scans, photos, handwriting)

1. Render: `pdftoppm -png -r 150 doc.pdf page`
2. OCR: `tesseract page-1.png out -l eng` (add language packs as needed)
3. Or per-page vision: `vision_analyze` on rendered pages — better for dense
   tables, math, handwriting; costs per call, so page-count budget matters.

## Per-format routing summary

| Input | Rung 1 | Rung 2 | Rung 3 |
|---|---|---|---|
| PDF digital | pymupdf4llm | docling/marker (scrambled tables) | — |
| PDF scanned | (empty) | — | pdftoppm+tesseract / vision |
| URL | web_extract | browser snapshot (JS-rendered) | — |
| .docx/.pptx | read_file | — | — |
| .md/.txt | verbatim copy | — | — |
| EPUB | read_file / pandoc | — | — |

## Raw file sizing

Large PDFs: extract per-chapter where practical, keep each raw file under
~500KB. Name descriptively: `<author-or-domain>-<topic>-YYYY-MM.md`.
