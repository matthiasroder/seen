#!/usr/bin/env python3
"""Build and search a rebuildable item-level FTS index for Seen."""

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import struct
import subprocess
import sys
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / 'data' / 'seen.sqlite'
DEFAULT_INDEX = ROOT / 'data' / 'seen-analysis.sqlite'
SCHEMA_VERSION = 2
EXTRACTOR_VERSION = '2'
MIN_TEXT = 80
MIN_POST_TEXT = 20
BLOCK_TAGS = {
    'address', 'article', 'aside', 'blockquote', 'br', 'dd', 'div', 'dl', 'dt',
    'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4',
    'h5', 'h6', 'header', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section',
    'table', 'td', 'th', 'tr', 'ul',
}
SKIP_TAGS = {'script', 'style', 'template', 'noscript', 'svg', 'nav', 'footer', 'form'}
VOID_TAGS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
    'param', 'source', 'track', 'wbr',
}
TRACKING_QUERY_KEYS = {'fbclid', 'gclid', 'mc_cid', 'mc_eid', 'trk', 'trackingid'}


SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    item_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    domain TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    extraction_method TEXT NOT NULL,
    extraction_confidence REAL NOT NULL,
    attention_key TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_domain ON items(domain);
CREATE INDEX IF NOT EXISTS items_seen ON items(last_seen_at DESC);
CREATE TABLE IF NOT EXISTS item_versions (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    text_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    source_snapshot_id TEXT NOT NULL,
    UNIQUE(item_id, text_hash)
);
CREATE INDEX IF NOT EXISTS item_versions_item ON item_versions(item_id, last_seen_at DESC);
CREATE TABLE IF NOT EXISTS item_occurrences (
    item_version_id INTEGER NOT NULL REFERENCES item_versions(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL,
    captured_at INTEGER NOT NULL,
    dom_hash TEXT NOT NULL,
    source_url TEXT NOT NULL,
    locator TEXT NOT NULL,
    PRIMARY KEY(item_version_id, snapshot_id)
);
CREATE INDEX IF NOT EXISTS item_occurrences_time ON item_occurrences(captured_at DESC);
CREATE INDEX IF NOT EXISTS item_occurrences_snapshot ON item_occurrences(snapshot_id);
CREATE TABLE IF NOT EXISTS dom_items (
    dom_hash TEXT NOT NULL,
    source_url TEXT NOT NULL,
    item_version_id INTEGER NOT NULL REFERENCES item_versions(id) ON DELETE CASCADE,
    locator TEXT NOT NULL,
    PRIMARY KEY(dom_hash, source_url, item_version_id)
);
CREATE TABLE IF NOT EXISTS indexed_documents (
    dom_hash TEXT NOT NULL,
    source_url TEXT NOT NULL,
    PRIMARY KEY(dom_hash, source_url)
);
CREATE TABLE IF NOT EXISTS indexed_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    dom_hash TEXT NOT NULL,
    captured_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS item_chunks (
    id INTEGER PRIMARY KEY,
    item_version_id INTEGER NOT NULL REFERENCES item_versions(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    UNIQUE(item_version_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS item_chunks_version ON item_chunks(item_version_id);
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER NOT NULL REFERENCES item_chunks(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY(chunk_id, model)
);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title,
    author,
    body,
    content='item_versions',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS item_versions_ai AFTER INSERT ON item_versions BEGIN
    INSERT INTO items_fts(rowid,title,author,body)
    VALUES (new.id,new.title,new.author,new.body);
END;
CREATE TRIGGER IF NOT EXISTS item_versions_ad AFTER DELETE ON item_versions BEGIN
    INSERT INTO items_fts(items_fts,rowid,title,author,body)
    VALUES ('delete',old.id,old.title,old.author,old.body);
END;
CREATE TRIGGER IF NOT EXISTS item_versions_au AFTER UPDATE ON item_versions BEGIN
    INSERT INTO items_fts(items_fts,rowid,title,author,body)
    VALUES ('delete',old.id,old.title,old.author,old.body);
    INSERT INTO items_fts(rowid,title,author,body)
    VALUES (new.id,new.title,new.author,new.body);
END;
'''


@dataclass
class ExtractedItem:
    item_key: str
    kind: str
    domain: str
    canonical_url: str
    title: str
    author: str
    body: str
    method: str
    confidence: float
    locator: str
    attention_key: str


class Node:
    __slots__ = ('tag', 'attrs', 'parent', 'parts')

    def __init__(self, tag, attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.parts = []


class TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].parts.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].parts.append(Node(tag, attrs, self.stack[-1]))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].parts.append(data)


def nodes(root):
    yield root
    for part in root.parts:
        if isinstance(part, Node):
            yield from nodes(part)


def hidden(node):
    if 'hidden' in node.attrs or node.attrs.get('aria-hidden', '').casefold() == 'true':
        return True
    style = re.sub(r'\s+', '', node.attrs.get('style', '').casefold())
    return 'display:none' in style or 'visibility:hidden' in style


def node_text(root, skip_nested_listitems=False):
    parts = []

    def visit(node, is_root=False):
        if node.tag in SKIP_TAGS or hidden(node):
            return
        if skip_nested_listitems and not is_root and node.attrs.get('role') == 'listitem':
            return
        if node.tag in BLOCK_TAGS:
            parts.append('\n')
        for part in node.parts:
            if isinstance(part, Node):
                visit(part)
            else:
                parts.append(part)
        if node.tag in BLOCK_TAGS:
            parts.append('\n')

    visit(root, True)
    lines = []
    for line in ''.join(parts).splitlines():
        clean = re.sub(r'\s+', ' ', line).strip()
        if clean:
            lines.append(clean)
    return '\n'.join(lines)


def plain_node_text(root):
    parts = []

    def visit(node):
        for part in node.parts:
            if isinstance(part, Node):
                visit(part)
            else:
                parts.append(part)

    visit(root)
    return re.sub(r'\s+', ' ', ''.join(parts)).strip()


def attention_hash(node):
    return hashlib.sha256(plain_node_text(node).encode('utf-8')).hexdigest()


def parse_html(html):
    parser = TreeParser()
    parser.feed(html)
    parser.close()
    return parser.root


def domain_for(url):
    return (urlsplit(url).hostname or '').casefold().removeprefix('www.')


def canonicalize(url):
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        folded = key.casefold()
        if folded.startswith('utm_') or folded in TRACKING_QUERY_KEYS:
            continue
        query.append((key, value))
    path = parsed.path or '/'
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, urlencode(query, doseq=True), ''))


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError('Truncated varint')
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            if value >= 1 << 64:
                raise ValueError('Varint exceeds 64 bits')
            return value, offset
    raise ValueError('Varint exceeds 64 bits')


def linkedin_urn(encoded):
    """Decode only LinkedIn comment-control shapes validated in saved DOM."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', encoded):
        return None
    try:
        data = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        urn_type = {0x0A: 'activity', 0x12: 'ugcPost'}.get(data[0] if data else None)
        if not urn_type:
            return None
        length, offset = varint(data, 1)
        if offset + length != len(data) or data[offset:offset + 1] != b'\x08':
            return None
        unsigned, end = varint(data, offset + 1)
        ident = (unsigned >> 1) ^ -(unsigned & 1)
        if end != len(data) or ident <= 0:
            return None
        return f'urn:li:{urn_type}:{ident}'
    except (ValueError, IndexError):
        return None


def attr_values(root):
    for node in nodes(root):
        yield from node.attrs.values()


def linkedin_identity(item):
    control = None
    locator = ''
    for value in attr_values(item):
        if not isinstance(value, str):
            continue
        if '-replaceableCommentTools' in value:
            encoded, suffix = value.split('-replaceableCommentTools', 1)
            candidate_locator = suffix.split('FeedType_', 1)[0]
            if candidate_locator and not locator:
                locator = candidate_locator
            decoded = linkedin_urn(encoded)
            if decoded and control is None:
                control = decoded
    if control:
        return 'linkedin:' + control, f'https://www.linkedin.com/feed/update/{control}/', locator or control
    if locator:
        return 'linkedin:component:' + locator, '', locator
    return None, '', ''


def exact_text_present(root, values):
    wanted = {value.casefold() for value in values}
    for node in nodes(root):
        for part in node.parts:
            if isinstance(part, str) and part.strip().casefold() in wanted:
                return True
    return False


def linkedin_items(root, source_url):
    result = []
    seen = set()
    for item in nodes(root):
        if item.attrs.get('role') != 'listitem':
            continue
        authors = []
        for node in nodes(item):
            label = node.attrs.get('aria-label', '')
            match = re.fullmatch(r'Open control menu for post by (.+)', label)
            if match:
                authors.append(match.group(1).strip())
        if not authors or exact_text_present(item, {'Promoted', 'Sponsored'}):
            continue
        bodies = []
        for node in nodes(item):
            if node.attrs.get('data-testid') == 'expandable-text-box':
                text = node_text(node)
                if len(text) >= MIN_POST_TEXT and text not in bodies:
                    bodies.append(text)
        if not bodies:
            continue
        body = '\n\n[Embedded post]\n'.join(bodies)
        key, canonical, locator = linkedin_identity(item)
        if not key:
            digest = hashlib.sha256((authors[0] + '\0' + body).encode()).hexdigest()
            key, locator = 'linkedin:text:' + digest, 'text:' + digest
        version_key = (key, hashlib.sha256(body.encode()).hexdigest())
        if version_key in seen:
            continue
        seen.add(version_key)
        lead = re.sub(r'\s+', ' ', bodies[0]).strip()
        title = lead[:157] + ('…' if len(lead) > 157 else '')
        result.append(ExtractedItem(
            item_key=key,
            kind='linkedin_post',
            domain='linkedin.com',
            canonical_url=canonical,
            title=title,
            author=authors[0],
            body=body,
            method='linkedin:listitem',
            confidence=1.0 if canonical else 0.8,
            locator=locator,
            attention_key=attention_hash(item),
        ))
    return result


def x_items(root):
    result = []
    seen = set()
    for article in (node for node in nodes(root) if node.tag == 'article'):
        if exact_text_present(article, {'Ad', 'Promoted'}):
            continue
        status = None
        handle = ''
        for node in nodes(article):
            if node.tag != 'a' or not node.attrs.get('href'):
                continue
            match = re.match(r'^/([^/]+)/status/(\d+)(?:$|[/?#])', node.attrs['href'])
            if match:
                handle, status = match.groups()
                break
        if not status or status in seen:
            continue
        seen.add(status)
        bodies = []
        for node in nodes(article):
            if node.attrs.get('data-testid') == 'tweetText':
                text = node_text(node)
                if len(text) >= MIN_POST_TEXT and text not in bodies:
                    bodies.append(text)
        if bodies:
            body = '\n\n[Quoted post]\n'.join(bodies)
        else:
            body = node_text(article)
        if len(body) < MIN_POST_TEXT:
            continue
        lines = node_text(article).splitlines()
        author = ''
        wanted_handle = '@' + handle.casefold()
        for index, line in enumerate(lines):
            if line.casefold() == wanted_handle and index:
                author = lines[index - 1]
                if author.casefold().endswith(' reposted') and index >= 2:
                    author = lines[index - 2]
                break
        canonical = f'https://x.com/{handle}/status/{status}'
        lead = re.sub(r'\s+', ' ', bodies[0] if bodies else body).strip()
        title = lead[:157] + ('…' if len(lead) > 157 else '')
        result.append(ExtractedItem(
            item_key='x:status:' + status,
            kind='x_post',
            domain='x.com',
            canonical_url=canonical,
            title=title,
            author=author or '@' + handle,
            body=body,
            method='x:article',
            confidence=1.0,
            locator=status,
            attention_key=attention_hash(article),
        ))
    return result


def meta_value(root, names):
    wanted = {name.casefold() for name in names}
    for node in nodes(root):
        if node.tag != 'meta':
            continue
        name = (node.attrs.get('name') or node.attrs.get('property') or '').casefold()
        if name in wanted and node.attrs.get('content', '').strip():
            return node.attrs['content'].strip()
    return ''


def generic_item(root, source_url, source_title):
    canonical = ''
    for node in nodes(root):
        rel = node.attrs.get('rel', '').casefold().split()
        if node.tag == 'link' and 'canonical' in rel and node.attrs.get('href'):
            canonical = node.attrs['href']
            break
    canonical = urljoin(source_url, canonical or meta_value(root, {'og:url'}) or source_url)
    if urlsplit(canonical).scheme.casefold() not in {'http', 'https'}:
        canonical = source_url
    canonical = canonicalize(canonical)
    candidates = [node for node in nodes(root) if node.tag == 'article']
    method = 'html:article'
    confidence = 1.0
    if not candidates:
        candidates = [node for node in nodes(root) if node.tag == 'main']
        method, confidence = 'html:main', 0.9
    if not candidates:
        candidates = [node for node in nodes(root) if node.tag == 'body']
        method, confidence = 'html:body', 0.6
    texts = [(node_text(node), node) for node in candidates]
    texts = [(text, node) for text, node in texts if len(text) >= MIN_TEXT]
    if not texts:
        return []
    body, primary = max(texts, key=lambda pair: len(pair[0]))
    title = meta_value(root, {'og:title'}) or source_title
    author = meta_value(root, {'author', 'article:author'})
    key = 'url:' + canonical
    return [ExtractedItem(
        item_key=key,
        kind='article' if method == 'html:article' else 'page',
        domain=domain_for(canonical or source_url),
        canonical_url=canonical,
        title=title.strip(),
        author=author,
        body=body,
        method=method,
        confidence=confidence,
        locator=method,
        attention_key=attention_hash(primary) if primary.tag == 'article' else '',
    )]


def extract_items(html, source_url, source_title):
    root = parse_html(html)
    domain = domain_for(source_url)
    if domain == 'linkedin.com':
        return linkedin_items(root, source_url)
    if domain in {'x.com', 'twitter.com'}:
        return x_items(root)
    return generic_item(root, source_url, source_title)


def connect_source(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Seen database does not exist: ' + str(path))
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    if db.execute('PRAGMA user_version').fetchone()[0] not in {1, 2, 3}:
        db.close()
        raise ValueError('Unsupported Seen database schema; expected version 1, 2, or 3.')
    return db


def connect_index(path, create=False):
    path = Path(path).expanduser().resolve()
    if not create:
        if not path.is_file():
            raise ValueError('Seen analysis index does not exist; run build first: ' + str(path))
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            db.close()
            raise ValueError('Unsupported Seen analysis schema; rebuild the index.')
        return db
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in {0, 1, SCHEMA_VERSION}:
        db.close()
        raise ValueError('Unsupported Seen analysis schema; no changes made.')
    if version == 1:
        db.execute("ALTER TABLE items ADD COLUMN attention_key TEXT NOT NULL DEFAULT ''")
    db.executescript(SCHEMA)
    db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    db.commit()
    os.chmod(path, 0o600)
    return db


def item_hash(item):
    content = '\0'.join((item.title, item.author, item.body)).encode('utf-8')
    return hashlib.sha256(content).hexdigest()


def store_item(db, item, snapshot):
    db.execute('''INSERT INTO items
        (item_key,kind,domain,canonical_url,title,author,first_seen_at,last_seen_at,extraction_method,extraction_confidence,attention_key)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(item_key) DO UPDATE SET
            canonical_url=CASE WHEN excluded.canonical_url<>'' THEN excluded.canonical_url ELSE items.canonical_url END,
            title=excluded.title, author=excluded.author,
            first_seen_at=min(items.first_seen_at,excluded.first_seen_at),
            last_seen_at=max(items.last_seen_at,excluded.last_seen_at),
            extraction_confidence=max(items.extraction_confidence,excluded.extraction_confidence),
            attention_key=excluded.attention_key''',
        (item.item_key, item.kind, item.domain, item.canonical_url, item.title, item.author,
         snapshot['captured_at'], snapshot['captured_at'], item.method, item.confidence,
         item.attention_key))
    item_id = db.execute('SELECT id FROM items WHERE item_key=?', (item.item_key,)).fetchone()[0]
    digest = item_hash(item)
    db.execute('''INSERT INTO item_versions
        (item_id,text_hash,title,author,body,first_seen_at,last_seen_at,source_snapshot_id)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(item_id,text_hash) DO UPDATE SET
            first_seen_at=min(item_versions.first_seen_at,excluded.first_seen_at),
            last_seen_at=max(item_versions.last_seen_at,excluded.last_seen_at)''',
        (item_id, digest, item.title, item.author, item.body, snapshot['captured_at'],
         snapshot['captured_at'], snapshot['id']))
    version_id = db.execute('SELECT id FROM item_versions WHERE item_id=? AND text_hash=?',
                            (item_id, digest)).fetchone()[0]
    store_chunks(db, version_id, item.title, item.author, item.body)
    return version_id


def chunk_text(body, words_per_chunk=180, overlap=30):
    words = body.split()
    if not words:
        return []
    result = []
    step = max(1, words_per_chunk - overlap)
    for start in range(0, len(words), step):
        chunk = ' '.join(words[start:start + words_per_chunk])
        if chunk:
            result.append(chunk)
        if start + words_per_chunk >= len(words):
            break
    return result


def store_chunks(db, version_id, title, author, body):
    existing = db.execute('SELECT 1 FROM item_chunks WHERE item_version_id=? LIMIT 1',
                          (version_id,)).fetchone()
    if existing:
        return
    prefix = '\n'.join(value for value in (title, author) if value).strip()
    for index, body_chunk in enumerate(chunk_text(body)):
        text = (prefix + '\n\n' + body_chunk).strip()
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        db.execute('''INSERT INTO item_chunks(item_version_id,chunk_index,text,text_hash)
            VALUES (?,?,?,?)''', (version_id, index, text, digest))


def attach_occurrence(db, version_id, locator, snapshot):
    db.execute('''INSERT OR IGNORE INTO item_occurrences
        (item_version_id,snapshot_id,captured_at,dom_hash,source_url,locator)
        VALUES (?,?,?,?,?,?)''',
        (version_id, snapshot['id'], snapshot['captured_at'], snapshot['dom_hash'],
         snapshot['url'], locator))
    row = db.execute('SELECT item_id FROM item_versions WHERE id=?', (version_id,)).fetchone()
    if row:
        db.execute('''UPDATE items SET first_seen_at=min(first_seen_at,?),last_seen_at=max(last_seen_at,?)
            WHERE id=?''', (snapshot['captured_at'], snapshot['captured_at'], row['item_id']))
        db.execute('''UPDATE item_versions SET first_seen_at=min(first_seen_at,?),last_seen_at=max(last_seen_at,?)
            WHERE id=?''', (snapshot['captured_at'], snapshot['captured_at'], version_id))


def build(source_path=DEFAULT_SOURCE, index_path=DEFAULT_INDEX):
    source_path = Path(source_path).expanduser().resolve()
    index_path = Path(index_path).expanduser().resolve()
    source = connect_source(source_path)
    index = connect_index(index_path, create=True)
    parsed = 0
    added_snapshots = 0
    try:
        configured = index.execute("SELECT value FROM metadata WHERE key='source_database'").fetchone()
        if configured and configured[0] != str(source_path):
            raise ValueError('Analysis index belongs to a different Seen database.')
        extractor = index.execute("SELECT value FROM metadata WHERE key='extractor_version'").fetchone()
        if index.execute('SELECT count(*) FROM indexed_snapshots').fetchone()[0] and (
                not extractor or extractor[0] != EXTRACTOR_VERSION):
            index.execute('DELETE FROM indexed_snapshots')
            index.execute('DELETE FROM indexed_documents')
            index.execute('DELETE FROM dom_items')
            index.execute('DELETE FROM items')
        index.execute("INSERT OR REPLACE INTO metadata VALUES ('source_database',?)", (str(source_path),))
        index.execute("INSERT OR REPLACE INTO metadata VALUES ('extractor_version',?)", (EXTRACTOR_VERSION,))
        snapshots = source.execute('''SELECT id,url,top_url,title,captured_at,frame_id,dom_hash
            FROM snapshots WHERE frame_id=0 ORDER BY captured_at,id''').fetchall()
        indexed = {row[0] for row in index.execute('SELECT snapshot_id FROM indexed_snapshots')}
        for snapshot in snapshots:
            if snapshot['id'] in indexed:
                continue
            added_snapshots += 1
            parsed_before = index.execute('''SELECT 1 FROM indexed_documents
                WHERE dom_hash=? AND source_url=?''', (snapshot['dom_hash'], snapshot['url'])).fetchone()
            mapped = index.execute('''SELECT item_version_id,locator FROM dom_items
                WHERE dom_hash=? AND source_url=?''', (snapshot['dom_hash'], snapshot['url'])).fetchall()
            if not parsed_before:
                row = source.execute('SELECT payload FROM doms WHERE hash=?', (snapshot['dom_hash'],)).fetchone()
                if not row:
                    continue
                parsed += 1
                try:
                    document = json.loads(row['payload'])
                    extracted = extract_items(document.get('html', ''), snapshot['url'], snapshot['title'])
                except (json.JSONDecodeError, UnicodeError, ValueError):
                    extracted = []
                for item in extracted:
                    version_id = store_item(index, item, snapshot)
                    index.execute('''INSERT OR IGNORE INTO dom_items
                        (dom_hash,source_url,item_version_id,locator) VALUES (?,?,?,?)''',
                        (snapshot['dom_hash'], snapshot['url'], version_id, item.locator))
                    attach_occurrence(index, version_id, item.locator, snapshot)
                index.execute('INSERT OR IGNORE INTO indexed_documents VALUES (?,?)',
                              (snapshot['dom_hash'], snapshot['url']))
            else:
                for mapping in mapped:
                    attach_occurrence(index, mapping['item_version_id'], mapping['locator'], snapshot)
            index.execute('INSERT INTO indexed_snapshots VALUES (?,?,?)',
                          (snapshot['id'], snapshot['dom_hash'], snapshot['captured_at']))
            if added_snapshots % 50 == 0:
                index.commit()
        index.commit()
        index.execute("INSERT OR REPLACE INTO metadata VALUES ('last_build_at',?)",
                      (str(int(datetime.now().timestamp() * 1000)),))
        index.commit()
        counts = {
            'source_database': str(source_path),
            'index_database': str(index_path),
            'snapshots_added': added_snapshots,
            'documents_parsed': parsed,
        }
        for table, label in [('indexed_snapshots', 'snapshots_indexed'),
                             ('indexed_documents', 'documents_indexed'), ('items', 'items'),
                             ('item_versions', 'versions'), ('item_occurrences', 'occurrences')]:
            counts[label] = index.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        return counts
    finally:
        index.close()
        source.close()


def embedder_path(index_path):
    return Path(index_path).expanduser().resolve().parent / '.seen-embed'


def ensure_embedder(index_path):
    if sys.platform != 'darwin':
        raise ValueError('Semantic search needs Apple Natural Language on macOS.')
    compiler = shutil.which('swiftc')
    if not compiler:
        raise ValueError('Swift compiler not found; exact FTS search still works.')
    source = ROOT / 'scripts' / 'embed.swift'
    target = embedder_path(index_path)
    if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_name(target.name + '.tmp')
        process = subprocess.run([compiler, str(source), '-o', str(temporary)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if process.returncode:
            raise ValueError((process.stderr or 'Could not compile local embedder.').strip())
        os.chmod(temporary, 0o700)
        temporary.replace(target)
    return target


def embed_texts(index_path, records, language='auto'):
    if not records:
        return []
    payload = ''.join(json.dumps({'id': ident, 'text': text}, ensure_ascii=False) + '\n'
                      for ident, text in records)
    process = subprocess.run([str(ensure_embedder(index_path)), language], input=payload,
                             capture_output=True, text=True)
    if process.returncode:
        raise ValueError((process.stderr or 'Local embedding failed.').strip())
    try:
        return [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise ValueError('Local embedder returned invalid data.') from error


def pack_vector(values):
    return struct.pack('<' + 'f' * len(values), *values)


def unpack_vector(blob, dimensions):
    return struct.unpack('<' + 'f' * dimensions, blob)


def update_vectors(index_path=DEFAULT_INDEX, batch_size=100):
    index_path = Path(index_path).expanduser().resolve()
    db = connect_index(index_path, create=True)
    added = 0
    try:
        while True:
            rows = db.execute('''SELECT c.id,c.text FROM item_chunks c
                WHERE NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.chunk_id=c.id)
                ORDER BY c.id LIMIT ?''', (batch_size,)).fetchall()
            if not rows:
                break
            for output in embed_texts(index_path, [(row['id'], row['text']) for row in rows]):
                vector = output['vector']
                dimensions = int(output['dimensions'])
                if len(vector) != dimensions or dimensions not in {512, 640}:
                    raise ValueError('Unexpected Apple embedding dimensions.')
                db.execute('''INSERT OR REPLACE INTO embeddings(chunk_id,model,dimensions,vector)
                    VALUES (?,?,?,?)''', (output['id'], output['model'], dimensions,
                                          pack_vector(vector)))
                added += 1
            db.commit()
        return {'vectors_added': added,
                'vectors': db.execute('SELECT count(*) FROM embeddings').fetchone()[0],
                'models': {row['model']: row['count'] for row in db.execute(
                    'SELECT model,count(*) AS count FROM embeddings GROUP BY model')}}
    finally:
        db.close()


def cosine(left, right):
    numerator = sum(a * b for a, b in zip(left, right))
    left_size = math.sqrt(sum(value * value for value in left))
    right_size = math.sqrt(sum(value * value for value in right))
    return numerator / (left_size * right_size) if left_size and right_size else 0.0


def semantic_search(index_path, query, timezone='UTC', since=None, before=None,
                    domain=None, kind=None, author=None, limit=20):
    if not query.strip():
        raise ValueError('Semantic search needs a query.')
    db = connect_index(index_path)
    zone = ZoneInfo(timezone)
    start, end = boundary(since, zone), boundary(before, zone)
    conditions = []
    values = []
    if domain:
        clean = domain.casefold().removeprefix('www.')
        conditions.append('(i.domain=? OR i.domain LIKE ?)')
        values.extend((clean, '%.' + clean))
    if kind:
        conditions.append('i.kind=?')
        values.append(kind)
    if author:
        conditions.append('iv.author LIKE ?')
        values.append('%' + author + '%')
    if start is not None:
        conditions.append('i.last_seen_at>=?')
        values.append(start)
    if end is not None:
        conditions.append('i.first_seen_at<?')
        values.append(end)
    where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
    try:
        models = [row[0] for row in db.execute('SELECT DISTINCT model FROM embeddings')]
        if not models:
            raise ValueError('No semantic vectors yet; run build first.')
        query_vectors = {}
        for model in models:
            language = model.rsplit('-', 1)[-1]
            output = embed_texts(index_path, [(0, query)], language)[0]
            query_vectors[model] = output['vector']
        rows = db.execute('''SELECT i.id AS item_id,iv.id AS version_id,i.kind,i.domain,
            i.canonical_url,iv.title,iv.author,i.first_seen_at,i.last_seen_at,
            iv.source_snapshot_id,c.text,e.model,e.dimensions,e.vector
            FROM embeddings e JOIN item_chunks c ON c.id=e.chunk_id
            JOIN item_versions iv ON iv.id=c.item_version_id JOIN items i ON i.id=iv.item_id''' +
            where, values).fetchall()
        best = {}
        for row in rows:
            score = cosine(query_vectors[row['model']], unpack_vector(row['vector'], row['dimensions']))
            current = best.get(row['item_id'])
            if current is None or score > current['score']:
                current = {key: row[key] for key in ('item_id', 'version_id', 'kind', 'domain',
                    'canonical_url', 'title', 'author', 'first_seen_at', 'last_seen_at',
                    'source_snapshot_id')}
                current.update(score=score, excerpt=row['text'][:500], model=row['model'])
                best[row['item_id']] = current
        ranked = sorted(best.values(), key=lambda item: (item['score'], item['last_seen_at']),
                        reverse=True)[:limit]
        for item in ranked:
            item['first_seen_at'] = stamp(item['first_seen_at'], zone)
            item['last_seen_at'] = stamp(item['last_seen_at'], zone)
        return {'index_database': str(Path(index_path).expanduser().resolve()), 'query': query,
                'timezone': timezone, 'count': len(ranked), 'results': ranked}
    finally:
        db.close()


def attention_totals(source_path, keys, start=None, end=None):
    if not keys:
        return {}
    source = connect_source(source_path)
    try:
        exists = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='item_attention'").fetchone()
        if not exists:
            return {}
        placeholders = ','.join('?' for _ in keys)
        conditions = [f'item_hash IN ({placeholders})']
        values = list(keys)
        if start is not None:
            conditions.append('last_seen_at>=?')
            values.append(start)
        if end is not None:
            conditions.append('started_at<?')
            values.append(end)
        return {row['item_hash']: {'focused_visible_ms': row['focused_visible_ms'],
                'observations': row['observations'], 'max_ratio': row['max_ratio']}
                for row in source.execute(f'''SELECT item_hash,sum(visible_ms) AS focused_visible_ms,
                    count(*) AS observations,max(max_ratio) AS max_ratio FROM item_attention
                    WHERE {' AND '.join(conditions)} GROUP BY item_hash''', values)}
    finally:
        source.close()


def audit(index_path, sample=50):
    db = connect_index(index_path)
    try:
        rows = db.execute('''WITH latest AS (
            SELECT i.id AS item_id,i.kind,i.canonical_url,i.attention_key,iv.title,iv.author,iv.body,
            row_number() OVER (PARTITION BY i.id ORDER BY iv.last_seen_at DESC,iv.id DESC) AS n
            FROM items i JOIN item_versions iv ON iv.item_id=i.id)
            SELECT * FROM latest WHERE n=1 ORDER BY kind,item_id''').fetchall()
        groups = {}
        for row in rows:
            groups.setdefault(row['kind'], []).append(row)
        selected = []
        while len(selected) < sample and any(groups.values()):
            for name in sorted(groups):
                if groups[name] and len(selected) < sample:
                    selected.append(groups[name].pop(0))
        result = []
        ad_words = re.compile(r'\b(sponsored|promoted|advertisement)\b', re.I)
        for row in selected:
            flags = []
            if row['kind'].endswith('_post') and not row['author']:
                flags.append('missing-author')
            if row['kind'].endswith('_post') and not row['canonical_url']:
                flags.append('missing-permalink')
            if ad_words.search(row['body'][:300]):
                flags.append('possible-ad')
            result.append({**{key: row[key] for key in ('item_id', 'kind', 'canonical_url',
                'title', 'author')}, 'text_chars': len(row['body']), 'excerpt': row['body'][:500],
                'flags': flags})
        return {'requested': sample, 'count': len(result), 'items': result}
    finally:
        db.close()


def bundle(index_path, source_path, timezone='UTC', since=None, before=None, limit=500):
    db = connect_index(index_path)
    zone = ZoneInfo(timezone)
    start, end = boundary(since, zone), boundary(before, zone)
    conditions, values = [], []
    if start is not None:
        conditions.append('i.last_seen_at>=?')
        values.append(start)
    if end is not None:
        conditions.append('i.first_seen_at<?')
        values.append(end)
    where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
    try:
        rows = db.execute('''WITH latest AS (
            SELECT i.id AS item_id,i.kind,i.domain,i.canonical_url,i.attention_key,
            iv.title,iv.author,iv.body,i.first_seen_at,i.last_seen_at,iv.source_snapshot_id,
            row_number() OVER (PARTITION BY i.id ORDER BY iv.last_seen_at DESC,iv.id DESC) AS n
            FROM items i JOIN item_versions iv ON iv.item_id=i.id''' + where + ''')
            SELECT * FROM latest WHERE n=1 ORDER BY last_seen_at DESC LIMIT ?''', [*values, limit]).fetchall()
        attention = attention_totals(source_path, {row['attention_key'] for row in rows if row['attention_key']},
                                     start, end)
        result = []
        for row in rows:
            item = dict(row)
            item.pop('n', None)
            item['first_seen_at'] = stamp(item['first_seen_at'], zone)
            item['last_seen_at'] = stamp(item['last_seen_at'], zone)
            item['attention'] = attention.get(item.pop('attention_key'), None)
            result.append(item)
        return {'count': len(result), 'timezone': timezone, 'items': result}
    finally:
        db.close()


def boundary(value, timezone):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return int(parsed.timestamp() * 1000)


def stamp(value, timezone):
    return datetime.fromtimestamp(value / 1000, timezone).isoformat(timespec='seconds')


def fts_expression(value, any_terms=False):
    try:
        terms = shlex.split(value)
    except ValueError as error:
        raise ValueError('Invalid search quoting: ' + str(error)) from error
    terms = [term.strip() for term in terms if term.strip()]
    if not terms:
        return ''
    quoted = ['"' + term.replace('"', '""') + '"' for term in terms]
    return (' OR ' if any_terms else ' AND ').join(quoted)


def search(index_path, query, timezone='UTC', since=None, before=None,
           domain=None, kind=None, author=None, limit=20, any_terms=False, raw_query=False):
    db = connect_index(index_path)
    zone = ZoneInfo(timezone)
    start, end = boundary(since, zone), boundary(before, zone)
    expression = query if raw_query else fts_expression(query, any_terms)
    conditions = []
    values = []
    if domain:
        clean_domain = domain.casefold().removeprefix('www.')
        conditions.append('(i.domain=? OR i.domain LIKE ?)')
        values.extend((clean_domain, '%.' + clean_domain))
    if kind:
        conditions.append('i.kind=?')
        values.append(kind)
    if author:
        conditions.append('iv.author LIKE ?')
        values.append('%' + author + '%')
    occurrence_conditions = ['o.item_version_id=iv.id']
    occurrence_values = []
    if start is not None:
        occurrence_conditions.append('o.captured_at>=?')
        occurrence_values.append(start)
    if end is not None:
        occurrence_conditions.append('o.captured_at<?')
        occurrence_values.append(end)
    if occurrence_values:
        conditions.append('EXISTS (SELECT 1 FROM item_occurrences o WHERE ' +
                          ' AND '.join(occurrence_conditions) + ')')
        values.extend(occurrence_values)
    where = (' AND ' + ' AND '.join(conditions)) if conditions else ''
    try:
        if expression:
            sql = '''WITH matches AS (
                SELECT i.id AS item_id,iv.id AS version_id,i.kind,i.domain,i.canonical_url,
                iv.title,iv.author,i.first_seen_at,i.last_seen_at,iv.source_snapshot_id,
                length(iv.body) AS text_chars,
                snippet(items_fts,2,'','',' … ',36) AS excerpt,
                bm25(items_fts,5.0,3.0,1.0) AS rank,
                (SELECT count(*) FROM item_occurrences o JOIN item_versions ov
                    ON ov.id=o.item_version_id WHERE ov.item_id=i.id) AS occurrences,
                (SELECT count(*) FROM item_versions versions WHERE versions.item_id=i.id) AS versions
                FROM items_fts JOIN item_versions iv ON iv.id=items_fts.rowid
                JOIN items i ON i.id=iv.item_id
                WHERE items_fts MATCH ?''' + where + '''), ranked AS (
                SELECT *,row_number() OVER
                    (PARTITION BY item_id ORDER BY rank,last_seen_at DESC,version_id DESC) AS result_rank
                FROM matches)
                SELECT item_id,version_id,kind,domain,canonical_url,title,author,first_seen_at,last_seen_at,
                    source_snapshot_id,text_chars,excerpt,rank,occurrences,versions
                FROM ranked WHERE result_rank=1 ORDER BY rank,last_seen_at DESC LIMIT ?'''
            rows = db.execute(sql, [expression, *values, limit]).fetchall()
        else:
            sql = '''WITH ranked AS (
                SELECT i.id AS item_id,iv.id AS version_id,i.kind,i.domain,i.canonical_url,
                iv.title,iv.author,i.first_seen_at,i.last_seen_at,iv.source_snapshot_id,
                length(iv.body) AS text_chars,substr(iv.body,1,500) AS excerpt,NULL AS rank,
                (SELECT count(*) FROM item_occurrences o JOIN item_versions ov
                    ON ov.id=o.item_version_id WHERE ov.item_id=i.id) AS occurrences,
                (SELECT count(*) FROM item_versions versions WHERE versions.item_id=i.id) AS versions,
                row_number() OVER (PARTITION BY i.id ORDER BY iv.last_seen_at DESC,iv.id DESC) AS result_rank
                FROM item_versions iv JOIN items i ON i.id=iv.item_id WHERE 1=1''' + where + ''')
                SELECT item_id,version_id,kind,domain,canonical_url,title,author,first_seen_at,last_seen_at,
                    source_snapshot_id,text_chars,excerpt,rank,occurrences,versions
                FROM ranked WHERE result_rank=1 ORDER BY last_seen_at DESC LIMIT ?'''
            rows = db.execute(sql, [*values, limit]).fetchall()
    except sqlite3.OperationalError as error:
        raise ValueError('Invalid FTS query: ' + str(error)) from error
    finally:
        db.close()
    results = []
    for row in rows:
        item = dict(row)
        item['first_seen_at'] = stamp(item['first_seen_at'], zone)
        item['last_seen_at'] = stamp(item['last_seen_at'], zone)
        results.append(item)
    return {
        'index_database': str(Path(index_path).expanduser().resolve()),
        'query': query,
        'fts_query': expression,
        'timezone': timezone,
        'count': len(results),
        'results': results,
    }


def stats(index_path):
    db = connect_index(index_path)
    try:
        result = {'index_database': str(Path(index_path).expanduser().resolve())}
        for table, label in [('indexed_snapshots', 'snapshots'),
                             ('indexed_documents', 'documents'), ('items', 'items'),
                             ('item_versions', 'versions'), ('item_occurrences', 'occurrences')]:
            result[label] = db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        result['kinds'] = {row['kind']: row['count'] for row in db.execute(
            'SELECT kind,count(*) AS count FROM items GROUP BY kind ORDER BY count DESC')}
        result['vectors'] = db.execute('SELECT count(*) FROM embeddings').fetchone()[0]
        result['models'] = {row['model']: row['count'] for row in db.execute(
            'SELECT model,count(*) AS count FROM embeddings GROUP BY model')}
        built = db.execute("SELECT value FROM metadata WHERE key='last_build_at'").fetchone()
        result['last_build_at'] = int(built[0]) if built else None
        return result
    finally:
        db.close()


def show(index_path, item_id, version_id=None, timezone='UTC'):
    db = connect_index(index_path)
    zone = ZoneInfo(timezone)
    try:
        values = [item_id]
        version_filter = ''
        if version_id is not None:
            version_filter = ' AND iv.id=?'
            values.append(version_id)
        row = db.execute('''SELECT i.id AS item_id,iv.id AS version_id,i.item_key,i.kind,i.domain,
            i.canonical_url,iv.title,iv.author,iv.body,i.attention_key,i.first_seen_at,i.last_seen_at,
            iv.first_seen_at AS version_first_seen_at,iv.last_seen_at AS version_last_seen_at,
            iv.source_snapshot_id,i.extraction_method,i.extraction_confidence
            FROM items i JOIN item_versions iv ON iv.item_id=i.id
            WHERE i.id=?''' + version_filter +
            ' ORDER BY iv.last_seen_at DESC,iv.id DESC LIMIT 1', values).fetchone()
        if not row:
            raise ValueError('Item or version not found.')
        result = dict(row)
        for key in ('first_seen_at', 'last_seen_at', 'version_first_seen_at', 'version_last_seen_at'):
            result[key] = stamp(result[key], zone)
        occurrences = db.execute('''SELECT o.snapshot_id,o.captured_at,o.source_url,o.locator,o.dom_hash
            FROM item_occurrences o WHERE o.item_version_id=? ORDER BY o.captured_at''',
            (row['version_id'],)).fetchall()
        result['occurrences'] = [
            {**dict(occurrence), 'captured_at': stamp(occurrence['captured_at'], zone)}
            for occurrence in occurrences
        ]
        result['versions'] = [dict(version) for version in db.execute('''SELECT id AS version_id,
            first_seen_at,last_seen_at,length(body) AS text_chars,text_hash
            FROM item_versions WHERE item_id=? ORDER BY last_seen_at DESC,id DESC''', (item_id,))]
        for version in result['versions']:
            version['first_seen_at'] = stamp(version['first_seen_at'], zone)
            version['last_seen_at'] = stamp(version['last_seen_at'], zone)
        configured = db.execute("SELECT value FROM metadata WHERE key='source_database'").fetchone()
        result['attention'] = attention_totals(configured[0], {row['attention_key']}).get(
            row['attention_key']) if configured and row['attention_key'] else None
        result.pop('attention_key', None)
        return result
    finally:
        db.close()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--database', default=str(DEFAULT_SOURCE), help='Source Seen SQLite database')
    result.add_argument('--index', default=str(DEFAULT_INDEX), help='Derived analysis SQLite database')
    commands = result.add_subparsers(dest='command', required=True)
    build_parser = commands.add_parser('build', help='Incrementally index new top-level snapshots')
    build_parser.add_argument('--no-vectors', action='store_true', help='Skip local semantic vectors')
    search_parser = commands.add_parser('search', help='Search extracted items')
    search_parser.add_argument('query', nargs='?', default='')
    search_parser.add_argument('--timezone', default='UTC')
    search_parser.add_argument('--since')
    search_parser.add_argument('--before')
    search_parser.add_argument('--domain')
    search_parser.add_argument('--kind')
    search_parser.add_argument('--author')
    search_parser.add_argument('--limit', type=int, default=20)
    search_parser.add_argument('--any', action='store_true', dest='any_terms')
    search_parser.add_argument('--raw-query', action='store_true', help='Use FTS5 query syntax directly')
    semantic_parser = commands.add_parser('semantic', help='Search by meaning with local Apple vectors')
    semantic_parser.add_argument('query')
    semantic_parser.add_argument('--timezone', default='UTC')
    semantic_parser.add_argument('--since')
    semantic_parser.add_argument('--before')
    semantic_parser.add_argument('--domain')
    semantic_parser.add_argument('--kind')
    semantic_parser.add_argument('--author')
    semantic_parser.add_argument('--limit', type=int, default=20)
    show_parser = commands.add_parser('show', help='Read one complete extracted item')
    show_parser.add_argument('item_id', type=int)
    show_parser.add_argument('--version-id', type=int)
    show_parser.add_argument('--timezone', default='UTC')
    audit_parser = commands.add_parser('audit', help='Create a balanced extraction-review sample')
    audit_parser.add_argument('--sample', type=int, default=50)
    bundle_parser = commands.add_parser('bundle', help='Export complete recent items for thinking review')
    bundle_parser.add_argument('--timezone', default='UTC')
    bundle_parser.add_argument('--since')
    bundle_parser.add_argument('--before')
    bundle_parser.add_argument('--limit', type=int, default=500)
    commands.add_parser('stats', help='Show index counts')
    return result


def run_build(source_path, index_path, vectors=True):
    index_path = Path(index_path).expanduser().resolve()
    lock_path = index_path.with_suffix(index_path.suffix + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        output = build(source_path, index_path)
        if vectors:
            try:
                output.update(update_vectors(index_path))
            except (ValueError, OSError, sqlite3.Error) as error:
                output['embedding_error'] = str(error)
        return output


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'build':
            output = run_build(args.database, args.index, not args.no_vectors)
        elif args.command == 'search':
            if args.limit < 1 or args.limit > 100:
                raise ValueError('Limit must be between 1 and 100.')
            output = search(args.index, args.query, args.timezone, args.since, args.before,
                            args.domain, args.kind, args.author, args.limit,
                            args.any_terms, args.raw_query)
        elif args.command == 'show':
            output = show(args.index, args.item_id, args.version_id, args.timezone)
        elif args.command == 'semantic':
            if args.limit < 1 or args.limit > 100:
                raise ValueError('Limit must be between 1 and 100.')
            output = semantic_search(args.index, args.query, args.timezone, args.since, args.before,
                                     args.domain, args.kind, args.author, args.limit)
        elif args.command == 'audit':
            if args.sample < 1 or args.sample > 500:
                raise ValueError('Sample must be between 1 and 500.')
            output = audit(args.index, args.sample)
        elif args.command == 'bundle':
            if args.limit < 1 or args.limit > 2000:
                raise ValueError('Limit must be between 1 and 2000.')
            output = bundle(args.index, args.database, args.timezone, args.since, args.before, args.limit)
        else:
            output = stats(args.index)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, sqlite3.Error, OSError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
