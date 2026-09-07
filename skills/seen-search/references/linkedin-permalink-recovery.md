# Recovering LinkedIn permalinks from captured DOM

Use when the user needs a particular LinkedIn post link and generic `verification.deep_links` / `verification.related_links` do not establish one. This is a local, read-only fallback, not permission to open LinkedIn. Missing extracted links do not prove that a permalink cannot be recovered.

## Establish which post owns the identifier

1. Verify the author and distinctive post text with `show`. Read the archive's database documentation before structural SQL; use `search.py`'s `connect()` and parameterized queries to retrieve `doms.payload` for the selected snapshot.
2. Parse the saved HTML (and relevant shadow roots) without executing scripts. Find the enclosing feed item containing both author and text. Inspect its anchors, metadata, element IDs, and comment controls. Do not borrow an ID from a neighboring post, a nested repost, or the entire page. A generic feed `href` on an interactive control is not a permalink; its destination may depend on runtime behavior absent from the capture.
3. In the observed LinkedIn DOM, comment controls had `id` and `componentkey` values shaped like `<encoded>-replaceableCommentTools<item-key>FeedType_MAIN_FEED_RECENT`. The encoded prefix, rather than the opaque item key, contained the post identifier and its URN type. Confirm the control belongs to the target item. Multiple candidate IDs require disambiguation, not selecting the first.

## Observed encoding, not a universal LinkedIn schema

The prefix is URL-safe Base64 containing a protobuf-style envelope. Two outer fields have been validated:
field 1 identifies an `activity`; field 2 identifies a `ugcPost`. Each contains a length-delimited message
whose field 1 is a ZigZag-encoded varint. Decode only these observed shapes; reject changed or malformed
encodings rather than guessing offsets.

```python
import base64
import re

def decode_post_identifier(encoded):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise ValueError("Invalid encoded identifier")
    data = base64.b64decode(
        encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
    )

    def varint(offset):
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise ValueError("Truncated varint")
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                if value >= 1 << 64:
                    raise ValueError("Varint exceeds 64 bits")
                return value, offset
        raise ValueError("Varint exceeds 64 bits")

    urn_type = {0x0A: "activity", 0x12: "ugcPost"}.get(data[0] if data else None)
    if not urn_type:
        raise ValueError("Unexpected outer field")
    length, offset = varint(1)
    if offset + length != len(data) or data[offset:offset + 1] != b"\x08":
        raise ValueError("Unexpected message shape")
    unsigned, end = varint(offset + 1)
    post_id = (unsigned >> 1) ^ -(unsigned & 1)
    if end != len(data) or post_id <= 0:
        raise ValueError("Unexpected post identifier")
    return urn_type, post_id
```

These synthetic examples illustrate the two supported shapes. They are test values, not links to captured posts:

| Encoded prefix | Decoded type | Synthetic post ID |
| --- | --- | --- |
| `CgsIgIDw9uyQrZXQAQ` | `activity` | `7500000000000000000` |
| `EgsIgoDw9uyQrZXQAQ` | `ugcPost` | `7500000000000000001` |

The URN type matters. A field-2 identifier must be presented as `ugcPost`, not `activity`.

## Validate before presenting a reconstructed URL

Compare the method against independent feed items in the relevant capture that contain both an encoded
comment-control identifier and an explicit matching `activity` or `ugcPost` URN. Each decoded type and ID
must equal the explicit URN for that same item. For activity validation, the second number in
`urn:li:comment:(urn:li:activity:<activity>,<comment>)` is a comment ID, not the post ID. Repeated snapshots
of one item are not independent checks.

Cross-check the encoding against explicit URNs in the capture being examined. The synthetic examples above do not validate a real item. If ownership or decoding remains unvalidated, report a candidate or an unresolved link rather than calling it established.

Once the target's ownership and identifier decoding are established, reconstruct
`https://www.linkedin.com/feed/update/urn:li:<activity-or-ugcPost>:<post_id>/` using the validated type.
Say “reconstructed from the saved DOM; not checked live.” Retain snapshot ID and capture time for
provenance. Reconstruction confirms the archive association, not current availability, access
permissions, or a successful live redirect.

If recovery fails, say “No reliable post link recovered from the inspected captures,” not “the DOM contains no link.” State the actual limitation and provide the captured page as a fallback. Do not silently fall back to live browsing.
