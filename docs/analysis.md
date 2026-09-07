---
type: "Documentation"
title: "Seen item index"
description: "Rebuildable item extraction and SQLite FTS5 search beside the raw Seen archive."
---

# Seen item index

Run shell and Python examples from the Seen repository root unless a step says otherwise.

Seen's raw database remains the evidence source. The optional item index at
`data/seen-analysis.sqlite` is derived, rebuildable, and ignored by Git. It never
changes `data/seen.sqlite`.

## Build and search

```sh
python3 scripts/items.py build
python3 scripts/items.py search '"earned autonomy"'
python3 scripts/items.py search 'agent verification' --domain linkedin.com
python3 scripts/items.py search 'oversight supervision' --any
python3 scripts/items.py semantic 'earned trust and human oversight'
python3 scripts/items.py show ITEM_ID
python3 scripts/items.py audit --sample 50
python3 scripts/items.py bundle --since 2026-08-24 --before 2026-08-31
python3 scripts/items.py stats
```

Build is incremental and is also launched automatically after new capture delivery. It reads committed top-level snapshots through a read-only SQLite connection,
parses each new DOM locally, and stores extracted text only in the sidecar. Repeated snapshots of the
same DOM reuse prior extraction. Running the command again indexes only new snapshot IDs.

The automatic job uses a file lock and runs outside the native capture process. Failure or slow vector
generation cannot roll back a raw capture. Apple Natural Language generates local English (512-number)
and German (640-number) embeddings. Same-language matching is strongest; FTS remains the reliable tool
for exact words, names, dates, and URLs.

The default search treats quoted groups as phrases and requires every group. `--any` joins groups with
OR. `--raw-query` accepts FTS5 syntax directly. Results collapse matching versions to one logical item,
include the number of versions and occurrences, and retain a source snapshot ID.
Use `show ITEM_ID` to read the selected version's complete extracted text and provenance before
interpreting the match. `--version-id` selects an older version explicitly.

Optional filters are `--since`, `--before`, `--timezone`, `--domain`, `--kind`, and `--author`.
Dates without an offset use the selected timezone, which defaults to UTC; pass `--timezone` with your IANA timezone for local date ranges.

## Data model

1. `items` holds logical items, such as one LinkedIn post or one article URL.
2. `item_versions` retains materially different extracted text for an item.
3. `item_occurrences` links each version to the raw snapshots in which it appeared.
4. `dom_items` records the items extracted from each distinct DOM and source URL.
5. `indexed_documents` lets identical DOM payloads reuse extraction, including zero-item results.
6. `indexed_snapshots` is the incremental-build checkpoint.
7. `items_fts` is an external-content FTS5 index over version title, author, and complete body.
8. `item_chunks` and `embeddings` hold rebuildable chunks and local Apple vectors.

The FTS tokenizer is SQLite `unicode61` with diacritic removal. Ranking uses BM25 with title weighted
5, author 3, and body 1. Full item text remains available for interpretation and evidence; the index
is only retrieval machinery.

## Extraction boundary

LinkedIn feed snapshots are split at post list items. Each accepted post needs an author control and a
substantial post body. X feeds are split at article elements with a saved status permalink. Advertisements
marked Promoted, Sponsored, or Ad are skipped. LinkedIn comment-control identifiers are decoded only for
the locally validated `activity` and `ugcPost` shapes; malformed or unknown identifiers do not produce
guessed links.

For other sites, extraction prefers the longest `article`, then `main`, then `body`. Script, style,
template, noscript, SVG, and explicitly hidden content are excluded from derived text. Canonical links
are retained and common tracking parameters are removed.

This is deliberately conservative, not universal. A capture proves that content was present in a DOM.
It does not prove that the user saw, understood, or agreed with it. FTS can locate an incidental phrase;
the complete item must still be read before assigning a topic or drawing a conclusion.

## Rebuild and recovery

The sidecar contains no unique evidence. If its schema or extraction rules change, move the file aside
or delete it and run `build` again. Deleting the raw Seen archive does not automatically clear the
sidecar; rebuild it after intentional raw-data deletion when exact parity matters.

For tests, use temporary synthetic archives:

```sh
python3 -m unittest tests.test_items -v
```
