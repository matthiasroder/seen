---
type: "Documentation"
title: "Seen SQLite interface"
description: "Local DOM archive schema and examples for other applications."
---

# Seen SQLite interface

Run shell and Python examples from the Seen repository root unless a step says otherwise.

Database: `data/seen.sqlite`. Schema version: `PRAGMA user_version = 3`. No external server, service port, or account. Opening an older archive creates the additive attention tables without rewriting snapshots or DOM.

## Schema

1. `snapshots`: one observation, with `id`, full `url`, `top_url`, `title`, `captured_at` (Unix milliseconds), `tab_id`, `frame_id`, `document_id`, and `dom_hash`.
2. `doms`: content-addressed DOM payload, with SHA-256 `hash`, UTF-8 `bytes`, and `payload` (JSON text). The payload has `html`, `shadowRoots`, and `formState`. There is no duplicated extracted-text column or index.
3. `documents`: a view exposing `hash`, `bytes`, `html`, `shadow_roots_json`, and `form_state_json` from the sole payload.
4. `page_visits`: one top-level page/route visit, with `id`, full `url`, `title`, `started_at`, nullable `ended_at`, accumulated `focused_ms`, and Chrome `tab_id`. Timing updates are idempotent and separate from DOM snapshots.
5. `item_attention`: cumulative observations of top-level `article` and list-item containers while at least half visible. It stores a local text hash, page provenance, focused-visible milliseconds, and maximum visible ratio. It does not store a second copy of the item text.

The optional `data/seen-analysis.sqlite` is a separate, rebuildable item and FTS5
index. It is not part of this canonical schema. See [Seen item index](analysis.md).

HTML uses the browser's HTML serialization for element subtrees, retaining document-level doctype and comments. Live property state is supplemental, not written back into the source DOM. Shadow/form `path` arrays start at the document and use `childNodes` indices; `"shadow"` enters a host's shadow root, and `"template"` enters template content. A shadow record's path identifies its host.

Version 2 and later capture only top-level documents (`frame_id = 0`). Schema migration alone leaves older subframe snapshots readable; the optional cleanup below removes them.

Item attention begins only after Seen 0.5 is loaded. Old scrolling cannot be reconstructed.

## Read recent snapshots

```sh
sqlite3 -readonly data/seen.sqlite \
  'SELECT datetime(captured_at/1000,"unixepoch"),title,url,frame_id FROM snapshots ORDER BY captured_at DESC LIMIT 20;'
```

## Read the DOM

```sql
SELECT s.id, s.url, s.captured_at, d.html, d.shadow_roots_json, d.form_state_json
FROM snapshots AS s
JOIN documents AS d ON d.hash = s.dom_hash
ORDER BY s.captured_at DESC;
```

## Read foreground time

```sql
SELECT url, title, started_at, ended_at, focused_ms
FROM page_visits
ORDER BY started_at DESC;
```

For totals by URL, use `sum(focused_ms)` and group by `url`. A focused counter means the tab was active in the focused Chrome window while Chrome did not report idle/locked; it does not prove visual attention.

## Remove pre-0.4 capture noise

This legacy cleanup supports schema versions 1 and 2. Version 3 archives are rejected without changes. For a supported archive, preview the cleanup before applying it:

```sh
python3 scripts/prune-noisy.py
python3 scripts/prune-noisy.py --apply
```

The tool removes subframe snapshots and top-level checkpoints whose meaningful state is unchanged from the preceding checkpoint for the same tab document and URL. Uncertain cases stay. Apply mode creates a timestamped SQLite backup under `data/backups/`, deletes orphaned DOM payloads, and leaves `page_visits` untouched. Run `VACUUM` separately when physical file compaction is wanted.

Read-only Python example:

```python
import json
import sqlite3

db = sqlite3.connect('file:data/seen.sqlite?mode=ro', uri=True)
for url, payload in db.execute('''
    SELECT s.url, d.payload FROM snapshots s JOIN doms d ON d.hash=s.dom_hash
    ORDER BY s.captured_at DESC LIMIT 10
'''):
    dom = json.loads(payload)
    # Feed dom['html'] to a parser. Do not execute it or load referenced resources.
```

## Reliability and backup

The writer uses WAL and full synchronous commits. SHA-256 and byte counts are checked before a capture is inserted, and snapshot IDs make delivery retries idempotent. Partial transfers never appear in `snapshots`. No automatic retention deletion runs.

Keep readers' transactions short. For backups use SQLite's backup API; copying only the main file while a writer runs can miss changes still in WAL:

```python
import sqlite3
source = sqlite3.connect('file:data/seen.sqlite?mode=ro', uri=True)
destination = sqlite3.connect('data/seen-backup.sqlite')
source.backup(destination)
destination.close()
source.close()
```

The helper uses [Chrome native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) with bounded base64 chunks. Chrome may expose closed shadow trees through its [DOM API](https://developer.chrome.com/docs/extensions/reference/api/dom). Neither mechanism fetches website resources.
