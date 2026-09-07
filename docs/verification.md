---
type: "Verification"
title: "Seen verification"
description: "Current DOM/SQLite evidence, installation boundary, and earlier release checks."
---

# Seen verification

## 0.5.0 automatic analysis, semantic search, and visible-item time

Raw snapshot delivery now launches a separate locked analysis process. A live native-host launch brought
the sidecar into exact snapshot parity without blocking capture; a later incremental pass handled one new
snapshot and vector in 0.15 seconds. The full one-time backfill produced 4,371 Apple-local vectors. Both
raw and analysis databases pass SQLite integrity checks.

Exact FTS remains unchanged. Semantic search uses Apple Natural Language with 512-number English and
640-number German vectors; no captured text is sent over a network. `bundle` returns complete bounded
items plus matching attention evidence for weekly analysis. A balanced audit of 50 real items found no
mixed post boundaries, wrong authors, or marked promoted ads. Two LinkedIn items correctly remain flagged
without a reliable permalink rather than receiving guessed links.

Visible-item timing observes only top-level `article` and list-item containers, requires at least 50%
visibility, and is enabled only while the tab/window is focused and Chrome reports the user active. It
stores cumulative time and a local text hash, not another text copy. Historical viewport time is not
reconstructed. Live timing still requires the user to reload the unpacked extension.

Eleven Node tests, 25 Python/SQLite tests, all 35 Seen Search tests, eleven mobile JavaScript tests, 42
mobile Swift checks, the package audit, and the static safety audit pass. Extension runtime: 87,462 bytes.
The helper bundle includes the item index and Apple embedding source. A weekly thinking audit is active
for Monday mornings and requires complete-source reading with central/secondary/incidental labels.

## 2026-09-02 — Item-level FTS5 sidecar

Seen now has an optional local analysis sidecar that extracts individual LinkedIn and X posts and primary
content from generic pages, deduplicates repeated captures, retains changed versions and raw-snapshot
provenance, and indexes title, author, and complete body with SQLite FTS5. The builder opens the raw
archive read-only and writes only the ignored sidecar. Synthetic checks cover LinkedIn item boundaries,
`activity` and `ugcPost` link recovery, advertisement and subframe exclusion, repeated occurrence
deduplication, changed versions, generic article extraction, canonical tracking cleanup, exact FTS
search, result collapsing, complete-item reads, and date/domain filters. A clean live local build indexed
1,269 top-level snapshots into 378 logical items, 809 versions, and 4,167 occurrences without modifying
the raw archive. The item mix was 82 LinkedIn posts, 97 X posts, 71 articles, and 128 other pages.

## 0.4.0 meaningful top-level capture and foreground time

Capture is now limited to the top-level document. A temporary semantic fingerprint suppresses scroll-only changes and mutations confined to technical/noisy UI while retaining complete DOM for accepted checkpoints. Blur and hidden-tab events no longer masquerade as page departure; route changes and `pagehide` remain checkpoint boundaries. Desktop Chrome foreground time is recorded separately in `page_visits`, paused across tab/window changes and idle/lock, and never creates a DOM snapshot.

Ten Node checks, sixteen Python/SQLite and cleanup checks, all 35 Seen Search checks, the 18-check browser fixture, eleven mobile JavaScript checks, 42 mobile Swift checks, and the static runtime audit pass. The browser fixture verifies top-frame-only registration, subframe rejection, scroll/ad-noise suppression, meaningful-content capture, a last-second `pagehide` checkpoint, and foreground timing without a DOM write. Attention unit tests verify activation, pause, route finalization, checkpointing, offline queuing, and DOM independence. The SQLite checks include an in-place v1-to-v2 migration that preserves existing snapshots; cleanup checks verify that ad and explicitly hidden churn compares unchanged while visible content and link changes remain meaningful. Extension runtime: 82,126 bytes.

The installed unpacked extension still requires a reload, and already-open pages require one refresh. Real-site reduction and focus-time accuracy across abrupt browser/process termination remain to be observed after activation.

## 0.3.0 lazy day navigation

The archive opens on a lightweight day index with capture counts. Choosing a day loads only that day's snapshot metadata; DOM content remains unloaded until a snapshot is selected. Global search runs only when submitted rather than after every keystroke.

Against the live archive, the two-day index returns in about 28 ms; selecting the current day returns the first 100 of 425 snapshots in about 85 ms. Eight Node checks, twelve Python/SQLite checks, all 34 Seen Search checks, the 15-check browser fixture, lazy day/search browser flows, visual inspection, and the static runtime audit pass. Extension runtime: 72,466 bytes.

## 0.2.7 stale Chrome contexts

`capture.js` now exits quietly when Chrome has invalidated an old page's extension APIs after an unpacked-extension reload. This prevents repeated `chrome.runtime.onMessage` exceptions across already-open tabs; refreshing a page creates a valid context for the new build.

Eight Node checks, eleven Python/SQLite checks, all 34 Seen Search checks, the 15-check browser fixture, and the static runtime audit pass. The added regression check executes `capture.js` with an unavailable Chrome runtime. Extension runtime: 68,356 bytes.

## 0.2.6 responsive archive reads

Trusted read-only archive requests no longer wait in the capture-handler queue. Status, listing, metadata, DOM reads and legacy export can proceed between native upload messages while capture mutations remain serialized. Date formatting is also shared across list rows instead of rebuilding a formatter for every snapshot.

Seven Node checks, eleven Python/SQLite checks, all 34 Seen Search checks, the 15-check browser fixture, archive startup preview, and static runtime audit pass. Extension runtime: 68,095 bytes.

## 0.2.5 archive startup

The archive starts its snapshot list immediately instead of waiting for status, coalesces overlapping focus/status refreshes, and no longer scans every DOM payload to calculate an unused byte total. This keeps startup work bounded as the SQLite archive grows.

Against the live 438 MB archive, a fresh native-helper status round trip dropped from about 381 ms to 25–30 ms. Seven Node checks, eleven Python/SQLite checks, all 34 Seen Search checks, the 15-check browser fixture, archive startup preview, and static runtime audit pass. Extension runtime: 67,855 bytes.

## 0.2.4 pre-injection navigation evidence

The extension background now records active HTTP(S) navigation before content-script injection. A refresh therefore leaves a `navigation-observed` event even if `capture.js` never starts, closing the diagnostic blind spot found during the live PressReader test.

Seven Node checks, eleven Python/SQLite checks, all 34 Seen Search checks, the 15-check browser fixture and the static runtime audit pass. Extension runtime: 67,552 bytes.

## 0.2.3 local diagnostic file

Seen mirrors its bounded diagnostic ring atomically into the ignored local file `logs/capture.jsonl`. The native helper validates field types and lengths, drops unknown fields including DOM content, caps the log at 60 records and uses owner-only file permissions. Clearing diagnostics rewrites the file empty.

Seven Node checks, eleven Python/SQLite checks, all 34 Seen Search checks, the 14-check browser fixture and the static runtime audit pass. Extension runtime: 67,293 bytes.

## 0.2.2 lean main-document diagnostics

Verified on 2026-09-01 after a live extension reload showed expected `window-unfocused` startup rejections from many background frames. Diagnostics now retain only main-document lifecycle, rejection, capture-error and successful-delivery events; frame capture behavior is unchanged. This prevents embedded widgets and blank frames from burying the active page's evidence. The 14-check browser fixture, automated tests and static audit pass; extension runtime: 66,809 bytes.

## 0.2.1 local capture diagnostics

Verified on 2026-09-01 with synthetic content only. Seen now keeps a bounded, local ring buffer of 60 structured events for main-frame injection, capture startup, explicit rejection reasons, capture errors and the latest successful SQLite delivery per document. The log stores URLs and pipeline metadata but no DOM content, is visible and independently clearable in archive Settings, and does not interrupt capture if diagnostic storage itself fails. The popup distinguishes permission from an actually saved checkpoint.

Seven Node checks, ten Python/SQLite checks, all 34 Seen Search checks, the 14-check browser fixture and the static runtime audit pass. The browser fixture specifically verifies that spoofed URL rejection records both URLs and the `url-mismatch` reason. Extension runtime: 66,733 bytes. Activation in the installed unpacked extension still requires a user reload and a refresh of already-open pages.

## 0.2.0 DOM and SQLite update

Verified on 2026-08-31. Automated capture tests used synthetic pages and temporary databases. After native-host installation and an extension reload, the installed extension wrote live captures to the local database. Read-only checks confirmed valid JSON, matching byte counts, continued capture, and SQLite integrity. No captured DOM payload is included in this repository.

1. Six Node checks: unredacted URLs; identical rules across domains and formerly excluded routes; exact grants; bounded base64 chunks; document/frame ownership; all-web/native-messaging permissions and Incognito prohibition.
2. Ten Python/SQLite checks: exact payload/metadata preservation; large Unicode payloads split across chunks; rejection of incomplete/corrupt commits; idempotent retries and content deduplication; retry after process restart; search/pagination without expiry; shared-document deletion; framed protocol/error recovery; chunk ordering and size validation.
3. Fourteen Chrome fixture checks using the actual collector, service-worker handler, and IndexedDB queue with a synthetic native adapter: entire HTML tree; hidden/offscreen/collapsed/script/template/comment/attribute inclusion; live form state and shadow roots; no added resource entries or source DOM changes; equal website/route handling; Incognito/background rejection; all-frame registration and inherited-origin support; read/clear privilege boundaries; URL-spoof rejection; pause; offline queuing and idempotent redelivery; large Unicode DOM integrity; preservation of earlier snapshots; unredacted SPA URLs; legacy store access.
4. Manual local-preview UI checks: snapshot list, inert HTML source reader, database status, queue status, and settings. No captured scripts or remote assets were executed in the reader.
5. Static and packaging checks: JavaScript syntax, no site-specific runtime filters, no network APIs, network-denying extension-page CSP, no HTML-assignment rendering, PNG sizes, extension runtime below 100 KB, and ZIP integrity. Extension runtime: 59,992 bytes. Python helper uses only standard-library modules.

### Verification boundary

The native adapter in browser tests is synthetic; the Python tests use real SQLite in temporary directories. Actual Chrome-to-native-host delivery is now verified from the live database, but this is not a comprehensive test of every website, cross-origin frame, closed shadow tree, browser shutdown, or production-page performance. Browser policy blocks agent navigation to chrome://extensions; the user performed the reload. Checkpoint gaps on brief mutations or teardown remain possible.

### Seen Search

The read-only search skill has 35 passing synthetic tests for content/raw retrieval, filters and dates, deduplication, scan limits, schema-v1/v2 handling, database non-mutation, and generic deep-link extraction. Link checks cover same-post association, neighbouring/nested posts, ambiguous candidates, relative URLs, shadow DOM, unsafe schemes, output caps, and explicit page fallbacks. Statistics, bounded search, and snapshot retrieval were also checked against the live archive without visiting source pages. A bounded live link check returned context-linked candidates; synthetic tests establish the explicit permalink cases, not universal real-site coverage. Its source and tests are included under `skills/seen-search`; run `python3 -m unittest discover -s skills/seen-search/scripts -p 'test_*.py'` from the checkout.

Run `npm test`, `npm run check`, and `npm run preview` from the repository root. In Chrome open `http://127.0.0.1:4318/tests/fixture.html` and click **Run DOM and delivery checks**. No actual website content is used.

The following sections are historical evidence for the earlier text-only versions, not descriptions of current capture behavior.

## 0.1.2 icon update

Added green-and-ivory eye icons for the toolbar (16/24/32 px), extension listing and installation (16/32/48/128 px), and archive tab favicon. Inspected generated PNGs at 16 and 128 px. All nine Node tests, static checks, PNG signature/dimension checks, and ZIP integrity checks pass; packaged runtime is 58,010 bytes. Capture code and permissions are unchanged. Existing installed-extension appearance still requires the user to reload Seen.

Assets follow Chrome's [manifest icon requirements](https://developer.chrome.com/docs/extensions/reference/manifest/icons) and [toolbar action icon requirements](https://developer.chrome.com/docs/extensions/reference/api/action). Rebuild the checked-in PNGs on macOS with `npm run icons`; users need no build tools.

## 0.1.1 capture verification

Verified on 2026-08-31 in the user's existing Chrome through the Chrome plugin, using synthetic localhost fixtures only. No extension installation or production-site recording was performed.

## Passing checks

1. Nine Node unit tests: safe URL handling, sensitive-route exclusions, exact-origin access, all-web wildcard grants and protocol restrictions, checkpoint deduplication, day grouping and Unicode, explicit size truncation, inert HTML-looking text, and literal multi-word search.
2. Seventeen browser-fixture checks against the actual collector, background handler, and native IndexedDB: visible and CSS-clipped text; offscreen/hidden/form/editable exclusions; no new resource entries or source DOM mutations during capture; dynamically inserted content; preservation after DOM removal; SPA transitions; pause; new HTTP and HTTPS origins accepted without per-site approval; Incognito authorization and storage denied on an otherwise allowed site; non-web/sensitive routes/subframes denied; privilege boundary for archive reads and clearing; URL spoofing rejection; concurrent storage updates; search/delete; age retention; page count eviction; byte-budget eviction.
3. Manual Chrome UI checks: updated popup, pause, and all-sites settings. Archive list, reader, search and highlighting were also verified in 0.1.0 and are unchanged. Preview uses synthetic records and mocked Chrome permission APIs.
4. Static checks: JavaScript syntax, required HTTP/HTTPS wildcard permissions, manifest-level Incognito prohibition, dynamic-only content-script registration, network-denying extension-page CSP, no fetch/XHR/WebSocket/EventSource/beacon calls, no HTML assignment rendering, under 100 KB runtime budget, zero runtime dependencies.

## Not yet verified

1. Loading the actual unpacked extension, real all-sites permission prompts and Incognito prohibition in Chrome's extension UI, and registering/injecting content scripts through Chrome's extension system. These require the user's installation approval.
2. Real service-worker suspension/restart, browser shutdown, full-navigation teardown, and real-site performance. The implementation checkpoints before departure but cannot guarantee the final in-flight write survives termination.
3. No universal undetectability claim. A synthetic no-extra-resource test and source audit are narrower evidence than a production network trace.

## Reproduce

Run `npm test`, `npm run check`, and `npm run preview` from the repository root. Open `http://127.0.0.1:4318/tests/fixture.html` in Chrome, keep the test tab focused, and choose **Run capture and storage checks**. The fixture uses and clears only its own localhost test archive, never the extension's storage.
