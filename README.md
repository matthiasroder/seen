---
type: "Documentation"
title: "Seen"
description: "Meaningful top-level DOM snapshots and foreground time in a local SQLite database, outside Chrome."
---

# Seen

A small Chrome extension plus a local SQLite helper. Saves meaningful checkpoints of the top-level DOM, not a separate extracted-text archive, and records foreground-tab time separately. All regular HTTP/HTTPS websites use exactly the same capture rules. Incognito is always excluded.

Repository: [matthiasroder/seen](https://github.com/matthiasroder/seen). Current installer targets macOS and Google Chrome 120+; Python 3 with SQLite is required. Node.js is only needed for development checks.

An optional [iPhone Safari companion](docs/mobile.md) is built and has passed local tests and a signed device launch. It is designed to queue DOM on the phone and deliver it over encrypted local-network transport into the same Mac database. Pairing and end-to-end Safari capture still need verification. It does not capture Chrome on iPhone; the desktop installation stays unchanged. The current free Personal Team signing requires renewal after seven days.

## Get the source

```sh
git clone https://github.com/matthiasroder/seen.git
cd seen
```

Run the commands below from the cloned `seen` directory. The installer derives its helper and database paths from that checkout. For an existing installation, keep the checkout in place; moving it requires updating Chrome's unpacked-extension path and native-helper registration.

## Update / install

1. Open `chrome://extensions`. For an existing installation, keep it: do not remove Seen or its old archive. New installations: enable Developer mode, choose Load unpacked, and select the `extension` folder inside your checkout.
2. Copy Seen's 32-letter extension ID.
3. In Terminal, run `python3 native/install.py YOUR_EXTENSION_ID`, replacing the final argument with that ID. This registers one local native-messaging helper, restricted to Seen. No web server or always-running service is installed.
4. Reload Seen in `chrome://extensions`. Approve the added idle-detection permission if Chrome prompts. Refresh already-open pages once if their old content script was invalidated by the update.
5. Open Seen → Open archive → Settings. It should show the absolute path to `data/seen.sqlite` inside your checkout. An existing global pause remains paused; click Paused to resume.

The extension and database are built. Helper registration and browser permission changes require installation; they are not implied by building the files. The archive settings also show the exact installation command with your extension ID.

## What is saved

1. At every accepted checkpoint, the complete accessible top-level document tree: HTML, attributes, scripts, styles, comments, templates, hidden and offscreen content. No domain-, route-, or sensitivity-based exclusions. URLs retain their queries and fragments.
2. Open and Chrome-accessible closed shadow DOM, stored alongside the main HTML with node paths. Live form values, checked states, and selected options are stored as DOM state, since HTML serialization alone does not preserve these current properties.
3. Subframes are never collected. Embedded ads, blank frames, login helpers, widgets, comments, maps, and iframe media remain represented only by their top-level `<iframe>` elements.
4. A temporary meaningful-state fingerprint covers the URL, title, semantic text outside explicitly hidden/noisy containers, links, non-password form controls, and accessible shadow content. Script/style/template/iframe content, explicitly hidden UI, short live regions, status alerts, timers, ad-labelled containers, cookie UI, and toasts do not trigger checkpoints. The fingerprint is only a gate: when it changes, the saved snapshot still contains the complete top-level DOM, including sensitive values already described below.
5. Changes settle for about 2.5 seconds, with a ten-second maximum wait during continuous meaningful change. Scrolling alone, blur, and hiding the tab do not capture. Initial load, meaningful changes, route changes, and a changed final `pagehide` state do.
6. Foreground time is stored separately per page visit. It counts only while the tab is active, its Chrome window is focused, and Chrome reports the user active. Visible-item time additionally requires at least half of a top-level `article` or list item to be on screen. It starts with Seen 0.5; old scrolling is not guessed.
7. The capture database has no separate text capture or text index. Raw search reads the stored DOM. A rebuildable item-level sidecar provides FTS and Apple-local semantic vectors. It refreshes after new raw captures in a separate process, so capture does not wait for analysis.

The archive opens on a lightweight day index. Snapshot metadata loads only after you choose a day or submit a search; DOM content remains unloaded until you select an individual snapshot.

## Where it lives

The primary archive is `data/seen.sqlite`. Other applications can read it directly with SQLite. See [database schema and examples](docs/database.md).

Chrome IndexedDB is only a temporary durable delivery queue for new DOM captures. A queued capture is removed only after SQLite acknowledges a verified, committed snapshot. Foreground-time updates use a separate lightweight queue in extension-local storage. If the helper is unavailable, both remain local and retry on the one-minute delivery alarm.

Settings also shows a local diagnostic ring buffer with at most 60 structured main-document capture events. The same bounded records are written atomically as JSON Lines to `logs/capture.jsonl`, where local tools can inspect them directly. The log records injection, startup, explicit rejection reasons, errors, and the latest successful SQLite delivery per document. It stores URLs and pipeline metadata, never DOM content, and can be cleared independently of the archive.

Old 0.1.x text records remain untouched in Chrome. They cannot be reconstructed into DOM. Use Settings → Export previous text archive to preserve them separately.

## Boundaries

1. No capture-generated HTTP requests, resource downloads, automatic scrolling, clicks, expansion, or page modifications. Chrome communicates with the helper through local process pipes, not localhost HTTP. Normal website requests from your own browsing continue as usual. This is not a universal undetectability claim.
2. DOM is not an offline copy of every browser resource. Referenced image/video bytes, iframe contents, external stylesheet bodies, canvas pixels, JavaScript heap state, attached file contents, and browser UI are not captured or downloaded.
3. Capture is checkpoint-based, not a recording of every intermediate mutation. Very short-lived content, navigation during a checkpoint, tab closure, forced termination, disk exhaustion, or inaccessible frames can create gaps. Closing a tab cannot recover its DOM afterward.
4. No automatic expiration, count cap, or size-based archive deletion. Disk use grows. Failures are reported; stored snapshots are not silently truncated. Source previews are capped at 200,000 characters for responsiveness, but the database and selected-DOM export contain the complete captured record.
5. SQLite is not separately encrypted. Captured DOM can contain passwords, session values, private messages, and other sensitive information, as explicitly requested. Exported JSON preserves it. Treat the database and exports accordingly.
6. Delete removes the selected snapshot; shared DOM payloads remain until their last reference is deleted. Clear DOM archive pauses capture and removes SQLite snapshots, page visits, and delivery queues, but does not erase legacy text records.

## Use in other applications

Query the `snapshots` table and `documents` view for DOM, and `page_visits` for foreground time. The canonical payload is stored once as JSON in `doms.payload`; the view exposes HTML without duplicating it. SQLite WAL mode allows readers alongside capture. For a consistent backup while Chrome is running, use SQLite's backup operation, not a bare file copy.

## Search with Codex or the command line

The [Seen Search skill](skills/seen-search/SKILL.md) and its Python helper are included in this repository. Link the `skills/seen-search` directory into your agent's skill directory to keep its helper connected to the checkout. The helper resolves symlinks and defaults to this checkout's `data/seen.sqlite`. If you copy the skill elsewhere, pass `--database /absolute/path/to/seen.sqlite` before the subcommand. The bundled skill is sufficient; no separate repository is required.

```sh
python3 skills/seen-search/scripts/search.py stats
python3 skills/seen-search/scripts/search.py search 'AI orchestras'
python3 skills/seen-search/scripts/search.py search 'leadership' --since yesterday --before today
python3 skills/seen-search/scripts/search.py show SNAPSHOT_ID --query 'distinctive phrase'
```

Search is read-only and local: no page visits, extra website requests, or persistent text index. It parses DOM on demand, returns provenance and bounded excerpts, and reports scan limits. `--mode raw` includes markup and supplementary form state. Use `--database /absolute/path/to/seen.sqlite` before the subcommand for another database. A capture proves content was present in the archived DOM, not that you read it.

Search results include `verification.deep_links` with same-record context and link evidence, plus `related_links` for ambiguous saved hrefs. Codex should cite the specific post where the saved DOM establishes its link. Profile links and outgoing references are not automatically post permalinks; if no reliable post link is found, the result falls back to the captured page and snapshot ID. Links are extracted generically across sites and are never opened or live-checked during retrieval.

## Build the item-level FTS index

For faster and more precise work across the growing archive, build the optional local sidecar:

```sh
python3 scripts/items.py build
python3 scripts/items.py search '"earned autonomy"'
python3 scripts/items.py search 'agent verification' --domain linkedin.com
python3 scripts/items.py semantic 'earned trust and agent oversight'
python3 scripts/items.py show ITEM_ID
python3 scripts/items.py bundle --since 2026-08-24 --before 2026-08-31
python3 scripts/items.py stats
```

Search dates default to UTC. For local date ranges, pass an IANA timezone such as `--timezone America/New_York` to the search, semantic, show, or bundle command.

The builder separates LinkedIn and X feed posts, extracts primary content from other pages, deduplicates
repeated captures, preserves changed versions and source-snapshot provenance, and indexes title, author,
and complete body with SQLite FTS5. Apple Natural Language creates local English and German vectors;
no captured text leaves the Mac. The sidecar updates automatically after capture delivery. It writes only the ignored
`data/seen-analysis.sqlite`; the raw archive stays unchanged. Search returns one
logical item rather than one whole feed snapshot and still requires complete-item reading before a
topic is treated as central. See [the item-index guide](docs/analysis.md).

## Repository and data boundary

Git contains the extension, native-helper source, search skill, synthetic tests, and documentation—not the browsing archive. `.gitignore` excludes `data/`, `exports/`, database sidecars, `dist/`, `logs/`, caches, and the generated `native/launch-host`. Keep any new exports or screenshots in those ignored directories and never force-add them. A private GitHub repository is still an external upload and is not a database backup.

The Chrome native-host manifest lives outside this checkout and is regenerated by the installer. Database backup instructions are in [the database guide](docs/database.md). [Verification evidence](docs/verification.md) describes tested behavior and remaining limits.

## Development

No npm packages. The extension runs directly from its folder. The helper uses Python's standard library, including SQLite. Icon generation uses macOS Swift/AppKit at build time only; PNGs are already checked in.

```sh
npm test
python3 -m unittest discover -s skills/seen-search/scripts -p 'test_*.py'
npm run check
npm run preview
npm run package
```

The preview at `http://127.0.0.1:4318/extension/archive.html` and the fixture at `http://127.0.0.1:4318/tests/fixture.html` use synthetic data and mocked Chrome/native APIs. They do not install the extension or write your real browsing into SQLite. Native SQLite behavior is separately tested against temporary databases. The extension-only ZIP and the ZIP with helper contain no tests, captured data, or credentials.
