# Wiki Schema

## Page types
- **entity** — a person, org, technology, or concept with its own page
- **concept** — an idea or mental model
- **comparison** — head-to-head of 2+ entities/concepts
- **query** — a recurring question + synthesized answer

## Frontmatter fields
```yaml
title: Page Title
created: 2026-01-01
updated: 2026-01-01
type: entity|concept|comparison|query
tags: [tag1, tag2]
sources: [raw/articles/source.md]
confidence: high|medium|low
contested: false
```

## Rules
- Every page has >= 2 outbound [[wikilinks]]
- Pages synthesizing 3+ sources carry ^[raw/...] provenance markers
- `contested: true` on contradictions — never silently overwrite
- raw/ files are immutable after ingest
- index.md lists all pages alphabetically within each section
- log.md is append-only: `## [YYYY-MM-DD] action | subject`
