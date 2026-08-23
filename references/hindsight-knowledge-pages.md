# Hindsight Knowledge Pages (v0.9.x) — operational reference for this skill

Grounded in the official docs
([concepts](https://hindsight.vectorize.io/developer/knowledge-pages),
[API](https://hindsight.vectorize.io/developer/api/knowledge-pages)) and
**live-verified against a self-managed v0.9.1 instance on 2026-08-23**.
All endpoints relative to `http://localhost:8888/v1/default/banks/{bank_id}`
(default bank: `main`). Check yours: `curl -s $HINDSIGHT_URL/api/version`.

## What a Knowledge Page is

A living markdown document the bank writes about itself, answering one
`source_query`. It is a **mental model with document defaults pre-made**:

- Built from **consolidated observations only** (deduplicated, evidence-backed
  beliefs) — never raw conversational noise.
- **Never reads sibling pages** (`exclude_mental_models: true`) — pages can't
  cite each other into a feedback loop.
- **Delta refresh**: after each consolidation it *edits* the existing document
  with new observations rather than regenerating it.
- It is a **projection, not storage** — delete it and it re-projects from
  memory. Raw documents remain the truth about *what was said*; pages are the
  reconciled truth about *what holds*.

## Why this matters for doc-ingest (the L2↔L3 bridge)

Retained pointers tagged `wiki-ref` + topic tags are observations in the bank.
A Knowledge Page scoped by `tag_groups`/tags becomes a **self-maintaining,
searchable index over the ingest history** — the reverse projection of the
curated wiki. Document-level hybrid search (full-text + semantic, fused
server-side, **no reranking step**) is fast enough to be the agent's first
lookup; recall stays for specific facts.

## API surface (v0.9.x)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/knowledge-base/tree` | Folder/page tree, per-page `is_stale` (bodies not included) |
| `POST` | `/knowledge-base/folders` | Create folder `{"name": "..."}` |
| `POST` | `/knowledge-base/pages` | Create page — **async**, returns `operation_id` |
| `GET` | `/knowledge-base/pages/{page_id}` | Read page as markdown (frontmatter + body) |
| `GET` | `/knowledge-base/search?q=...` | Hybrid document-level search, ranked pages + snippets |
| `PATCH` | `/knowledge-base/nodes/{node_id}` | Rename / move / reconfigure |
| `DELETE` | `/knowledge-base/nodes/{node_id}` | Delete node + subtree (re-projectable) |
| `GET` | `/knowledge-base/export` | Export whole base as a markdown bundle |

CLI: `hindsight fs mount --bank main` mirrors the tree to real files on disk
(background refresh loop); `hindsight knowledge-base tree|create-page ...`.

### Create-page payload

```json
{
  "name": "Ingest Index",
  "source_query": "Which documents have been ingested into the wiki, and what are their pointers?",
  "parent_id": "<folder-id or null>",
  "tags": ["wiki-ref"]
}
```

Default trigger (when `trigger` omitted) — the document-oriented config:
`{"mode": "delta", "fact_types": ["observation"], "exclude_mental_models": true,
"refresh_after_consolidation": true}`. `max_tokens` defaults to **4096**
(plain mental models get 2048). A supplied `trigger` **replaces** the defaults,
it does not merge — repeat every field you want to keep.

## Verified behavior notes (v0.9.1, live-tested)

- Page creation returns immediately: `page_id`, `mental_model_id`,
  `operation_id`. Poll the operations API; the first build is a full generation.
- Page names must be unique within a folder (case-insensitive) — duplicates
  return `409` (PostgreSQL).
- A page whose `source_query` matches no observations renders the body
  `"I don't have information."` — that is normal for a fresh bank, not an error.
  The page fills in as tagged retains land and consolidate.
- `is_stale` in the tree comes from one bank-wide signal
  (`last_memory_write_at`); for an exact per-page answer read the backing
  mental model `GET /mental-models/{id}` (more expensive).
- v0.9.1 hardening relevant to ingest pipelines: strict per-bank scoping on
  document update/delete, concurrent appends to one document no longer lose
  turns, async (non-blocking) document export, and whole-bank transfers now
  carry the Knowledge Pages tree + regenerate its search state on import.
- v0.9.1 recall is substantially faster (~9x temporal extraction, set-wise
  scoring) with identical results — pointer recall cost is lower than in 0.8.x.
- Reflect now resolves entity names on sub-recalls and never drops retrieved
  evidence during synthesis — pointer facts survive reflection better.

## Ops checklist when adding a Knowledge Page for ingest history

1. `POST /knowledge-base/folders` (once) — e.g. `{"name": "Wiki"}`
2. `POST /knowledge-base/pages` with `tags: ["wiki-ref"]` and a
   source_query phrased as the question the page should answer
3. Record `page_id` + folder in the wiki's `log.md` entry for traceability
4. After bulk ingests, `GET /knowledge-base/tree` and surface `is_stale: true`
   pages in the report — a stale ingest-index page means consolidation produced
   knowledge the page hasn't absorbed yet
5. Never hand-edit a mounted page body — edits are overwritten by the next
   refresh; correct scope via `PATCH` on the node instead
