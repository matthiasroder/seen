#!/usr/bin/env python3
"""Remove captures rejected by Seen's top-level meaningful-state policy."""
import argparse
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urljoin


NOISE_TOKEN = re.compile(r'(^|[\s_-])(ad|ads|advert|advertisement|sponsored|cookie|consent|toast)(?=$|[\s_-])', re.I)
IGNORED_TAGS = {'script', 'style', 'noscript', 'template', 'iframe', 'svg', 'canvas'}
IGNORED_ROLES = {'status', 'alert', 'timer', 'marquee'}
VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class Node:
    def __init__(self, tag='', attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []


class TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag.lower(), attrs)
        self.stack[-1].children.append(node)
        if node.tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag.lower(), attrs))

    def handle_endtag(self, tag):
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def text_content(node):
    if isinstance(node, str):
        return node
    return ''.join(text_content(child) for child in node.children)


def ignored(node):
    attrs = node.attrs
    if node.tag in IGNORED_TAGS or 'hidden' in attrs or attrs.get('aria-hidden') == 'true':
        return True
    if attrs.get('role') in IGNORED_ROLES:
        return True
    live = attrs.get('aria-live')
    if live and live != 'off' and len(text_content(node)) < 256:
        return True
    if any(name in attrs for name in ('data-ad', 'data-ad-slot', 'data-ad-unit')):
        return True
    labels = ' '.join(attrs.get(name, '') or '' for name in ('id', 'class', 'aria-label'))
    return bool(NOISE_TOKEN.search(labels))


def semantic_parts(html, url):
    parser = TreeParser()
    parser.feed(html)
    parser.close()
    parts = []

    def walk(node):
        if isinstance(node, str):
            value = ' '.join(node.split())
            if value:
                parts.extend(('text', value))
            return
        if node.tag and ignored(node):
            return
        if node.tag == 'a':
            parts.extend(('link', urljoin(url, node.attrs.get('href', ''))))
        elif node.tag == 'input':
            kind = (node.attrs.get('type') or 'text').lower()
            if kind not in {'password', 'file', 'hidden'}:
                parts.extend(('input', kind, node.attrs.get('value', ''), '1' if 'checked' in node.attrs else '0'))
        elif node.tag == 'select':
            selected = [str(index) for index, child in enumerate(c for c in node.children if isinstance(c, Node) and c.tag == 'option') if 'selected' in child.attrs]
            parts.extend(('select', ','.join(selected)))
        for child in node.children:
            walk(child)

    walk(parser.root)
    return parts


def fingerprint(row):
    dom = json.loads(row['payload'])
    parts = ['url', row['url'], 'title', row['title']]
    parts.extend(semantic_parts(dom['html'], row['url']))
    for shadow in dom.get('shadowRoots', []):
        parts.extend(semantic_parts(shadow.get('html', ''), row['url']))
    # Live values are appended without DOM paths so noise inserting a sibling
    # cannot make an unchanged control look meaningful. Including all values is
    # deliberately conservative where an old payload cannot identify its type.
    for state in dom.get('formState', []):
        for name in ('value', 'checked', 'indeterminate', 'selected'):
            if name in state:
                parts.extend(('state', name, json.dumps(state[name], ensure_ascii=False, sort_keys=True)))
    return hashlib.sha256('\x1f'.join(parts).encode()).digest()


def candidates(db):
    rows = db.execute('''SELECT s.id,s.url,s.title,s.captured_at,s.tab_id,s.frame_id,s.document_id,d.payload
        FROM snapshots s JOIN doms d ON d.hash=s.dom_hash ORDER BY s.captured_at,s.id''')
    previous = {}
    remove = []
    reasons = Counter()
    days = Counter()
    for row in rows:
        day = datetime.fromtimestamp(row['captured_at'] / 1000).date().isoformat()
        if row['frame_id'] != 0:
            remove.append(row['id']); reasons['subframe'] += 1; days[(day, 'subframe')] += 1
            continue
        current = fingerprint(row)
        key = (row['tab_id'], row['document_id'])
        prior = previous.get(key)
        if prior and prior == (row['url'], current):
            remove.append(row['id']); reasons['unchanged_meaning'] += 1; days[(day, 'unchanged_meaning')] += 1
        else:
            previous[key] = (row['url'], current)
    return remove, reasons, days


def backup_database(db, source):
    directory = source.parent / 'backups'
    directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')
    target = directory / f'seen-before-noise-prune-{stamp}.sqlite'
    with sqlite3.connect(target) as copy:
        db.backup(copy)
    os.chmod(target, 0o600)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[1] / 'data' / 'seen.sqlite')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = args.database.expanduser().resolve()
    uri = path.as_uri() + ('' if args.apply else '?mode=ro')
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    if not args.apply:
        db.execute('PRAGMA query_only=ON')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (1, 2):
        raise SystemExit(f'Unsupported Seen schema version: {version}')
    before = db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
    remove, reasons, days = candidates(db)
    result = {'database': str(path), 'apply': args.apply, 'before': before, 'remove': dict(reasons),
              'keep': before - len(remove), 'days': {f'{day}:{reason}': count for (day, reason), count in sorted(days.items())}}
    if args.apply:
        backup = backup_database(db, path)
        db.execute('BEGIN IMMEDIATE')
        db.executemany('DELETE FROM snapshots WHERE id=?', ((item,) for item in remove))
        orphaned = db.execute('DELETE FROM doms WHERE NOT EXISTS (SELECT 1 FROM snapshots WHERE snapshots.dom_hash=doms.hash)').rowcount
        db.commit()
        result.update({'backup': str(backup), 'removed_snapshots': len(remove), 'removed_orphan_doms': orphaned,
                       'after': db.execute('SELECT count(*) FROM snapshots').fetchone()[0]})
    db.close()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
