---
type: "Documentation"
title: "Seen on iPhone"
description: "Safari DOM capture and encrypted local delivery to the existing Mac SQLite archive."
---

# Seen on iPhone

Run shell and Python examples from the Seen repository root unless a step says otherwise.

Safari on iOS 17+. No Chrome-on-iPhone capture, other-app capture, or import of old history. It shares the Mac's Seen archive; Google account sync plays no role.

**Verification status:** a signed app and Safari extension have been installed and launched on a test device after developer trust was enabled. Local signature verification, simulator launch, shared storage, and synthetic storage and transport tests pass. Safari capture, private-tab exclusion, pairing, and delivery over Wi-Fi remain unverified. The receiver starts manually; no automatic-startup service is installed.

## Install on iPhone

1. Connect and unlock the iPhone; approve **Trust This Computer**. Enable Developer Mode if Xcode requests it.
2. Open `mobile/apple/SeenMobile/SeenMobile.xcodeproj` in Xcode. Choose the iPhone as the run destination.
3. Under **Signing & Capabilities**, select your Apple development team for both `SeenMobile` and `SeenMobile Extension`. Both need App Group `group.com.matthiasroder.SeenMobile`. Let Xcode resolve development signing. No development team, signing identity, or provisioning profile is committed. If identifiers belong to another team, resolve them consistently; do not remove the shared-group capability.
4. Run `SeenMobile` to install the app and extension. If iOS reports an untrusted developer, open Settings → General → VPN & Device Management → your developer account, trust it, then open Seen. This trust decision must be made on the phone. No App Store publication is involved.
5. In Settings → Apps → Safari → Extensions → Seen, enable Seen and allow **All Websites**. On iOS 17, Safari is directly under Settings. Keep **Allow in Private Browsing off**. Missing website/profile permissions mean missing captures.
6. Open Seen. A working installation shows the local queue, not an App Group error. Its Capture Safari toggle pauses recording; it cannot enable Apple's extension permission for you.

Apple references: [App Groups configuration](https://developer.apple.com/documentation/xcode/configuring-app-groups), [capabilities by membership](https://developer.apple.com/help/account/reference/supported-capabilities-ios), [Safari native messaging](https://developer.apple.com/documentation/safariservices/messaging-between-the-app-and-javascript-in-a-safari-web-extension).

Select your own development team in Xcode. Keep signing keys and provisioning profiles outside Git. Apple's free Personal Team provisioning expires after seven days and requires rebuilding/reinstalling. This is a development installation, not a maintenance-free distribution channel. Account sign-in, signing and certificate verification use Apple services; DOM capture does not refetch browsed websites. See [Apple's Personal Team limits](https://developer.apple.com/help/account/basics/about-your-developer-account/) and [developer trust settings](https://developer.apple.com/documentation/devicemanagement/restrictions#allowEnterpriseAppTrust).

## Pair and sync

Build the small receiver, then run it with your Mac's exact private Wi-Fi IPv4 address (System Settings → Wi-Fi → Details → TCP/IP). Replace `YOUR_MAC_LAN_IP` with that address:

```sh
npm run mobile:receiver
./dist/SeenReceiver --bind YOUR_MAC_LAN_IP
```

1. Leave the process running while syncing. Allow incoming local connections if macOS prompts. No daemon, discovery, port forwarding, or cloud relay is installed.
2. Open `mobile-state/pairing.png` on the Mac. Scan with the iPhone Camera, open Seen, and confirm **Pair Mac**. Only trust your own receiver's QR. It contains the pairing key: never publish or commit it.
3. Keep both devices reachable on your trusted local network; allow Seen's Local Network permission. Open Seen and tap **Sync now**. Safari also attempts one native-queued delivery opportunistically while active, at most once per minute. The app drains its native queue for roughly 90 seconds per attempt; large queues may need another tap.
4. Snapshots remain on the phone until the Mac confirms the exact ID and SHA-256 after committing to SQLite. Existing Seen Search then reads those rows normally. No second cloud archive or text copy is created.

Default destination: `data/seen.sqlite`. For isolated tests, pass explicit `--database /absolute/test/seen.sqlite --state /absolute/test/pairing-state` paths. Only numeric private IPv4 endpoints are accepted—no public, wildcard, loopback, DNS, or IPv6 addresses. Port defaults to 8766. Ctrl-C stops the receiver without deleting queued captures.

Pairing survives restarts. If the Mac's IP changes, use a new `--state` directory and explicitly pair again; the receiver refuses to silently change an existing pairing. No key-rotation/revocation UI or automatic-startup service is included.

## Capture boundaries

1. The collector is byte-identical to desktop Seen. It runs only in the top-level document and uses the same meaningful-state gate. Accepted checkpoints retain the complete top-level HTML, including hidden/offscreen nodes, scripts, comments, templates, accessible shadow roots and live form properties. No site-specific handling or persistence-time sensitive-data filters.
2. Both the sender and active tab must report `incognito === false`; unknown privacy state fails closed. Safari ignores Chrome's `incognito` manifest setting, so runtime checks and keeping Safari's private-browsing permission off both matter.
3. Capture JS has no fetch, XHR, WebSocket or sendBeacon calls; extension-page network connections are disabled. Native transfer sends previously captured bytes only to the paired private IP. Normal browsing still makes website requests. This is not an undetectability guarantee.
4. Safari lacks Chrome's closed-shadow-root API. Hidden closed roots and browser-protected documents are inaccessible. Subframes are deliberately excluded. External media, canvas pixels and JavaScript heap state are not DOM and are not fetched.
5. Checkpoints are best effort, not lossless recording of every mutation. iOS can terminate Safari/native work on navigation, closure or backgrounding. Only persisted bytes survive. A close hook or continuing background execution cannot be guaranteed.
6. Away from the Mac, captures queue locally. Return to a regular Safari page to wake its browser outbox; open Seen to drain its native queue. Opening Seen cannot force a suspended Safari background to hand over IndexedDB records.
7. No automatic expiry, count limit or truncation is implemented. Storage exhaustion or browser eviction can still lose data. Incomplete transfers are retained/reported, not counted as complete DOM. Deleting the app, clearing extension storage or losing the phone can lose unsynced captures.

## Storage and transport

```text
Safari DOM → extension IndexedDB → native messaging → iPhone queue.sqlite
           → encrypted local TCP → existing Mac data/seen.sqlite
```

The phone queue is in App Group `group.com.matthiasroder.SeenMobile`, under `Library/Application Support/Seen/queue.sqlite`. Its directory is excluded from device backups and uses iOS data protection until first unlock. SQLite is not separately encrypted. Phone pairing state stays in that container. Mac pairing files are owner-only under the ignored `mobile-state/` directory; never export them with source.

The mobile queue/staging schema remains version 1. The shared Mac archive is version 2: `doms`, `snapshots`, `documents`, and desktop-only `page_visits`; additive `mobile_uploads`/`mobile_parts` tables retain transfer staging, and `mobile_settings` holds local state. Mobile uploads do not create foreground-time rows. Mobile `document_id` starts `safari-ios/<installation-uuid>/`; frame/tab IDs are installation-local and timestamps use the phone clock.

Transfers use 64 KiB chunks, UUIDs, exact UTF-8 bytes, SHA-256, SQLite WAL/FULL commits and durable resume offsets. Lost acknowledgements retry idempotently. A file lock coordinates phone app/extension delivery workers. A phone record is deleted only after the exact committed digest is acknowledged.

Wire format: four-byte big-endian length plus CryptoKit AES-256-GCM nonce/ciphertext/tag. Fresh nonces, direction-specific authenticated data and matched request IDs protect messages. This is a small custom framed protocol using standard cryptography, not HTTP/TLS. Authentication happens before opening the receiver database. Only begin/chunk/commit are exposed, never reads or deletion. Four concurrent connections and one-megabyte frame limits bound unauthenticated work. The shared pairing key authorizes archive writes; protect it.

## Development and evidence

No third-party packages: JavaScript, SwiftUI, SafariServices, Network, CryptoKit and system SQLite. Node.js and Xcode are build tools. Do not rerun Safari's converter over this customized Xcode project.

```sh
npm run mobile:test
npm run mobile:receiver
npm run mobile:simulator
npm test
npm run check
python3 -m unittest discover -s skills/seen-search/scripts -p 'test_*.py'
```

`scripts/mobile-resources.mjs` copies desktop capture/queue/core and bundles `mobile/background-source.mjs` into Safari's classic background script. Edit sources, not generated `mobile/extension/background.js`. Simulator builds use ad-hoc signing for shared storage; no physical-device identity is needed. Build products/screenshots stay in ignored `dist/`.

Verified with Xcode 26.4.1 and iOS 26.4.1 simulator:

1. Eleven browser-side tests: private/unknown and subframe exclusion, active-page scope, equal-site capture, pause, UI boundaries, ownership, chunk validation, acknowledgements, retention and no website network APIs.
2. Forty-two Swift checks: DOM/Unicode preservation, restart/resume, corrupt/incomplete rejection, deduplication, exact acknowledgements, pairing validation, crypto tampering/reflection, and real loopback delivery. Wrong-key clients never create a database; offline delivery retains snapshots. Synthetic data only.
3. App builds, launches, initializes shared storage and renders its single-screen UI. Mac receiver compiles. Desktop tests remain green: 10 JavaScript, 16 SQLite/cleanup and 35 search tests.

A signed physical-device build, installation, and launch were verified. Signature checks passed. These checks do not establish successful capture or transfer from Safari.

Still required: confirmation of Safari extension permissions, synthetic-page capture, private-tab exclusion, pairing and byte-verified transfer over actual Wi-Fi. Until those pass, mobile capture is not verified live.
