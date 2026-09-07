#!/usr/bin/env python3
"""Seen native-messaging host. Standard library only; no sockets or HTTP."""
import base64
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys

DEFAULT_DATABASE = Path(__file__).resolve().parent.parent / 'data' / 'seen.sqlite'
DEFAULT_DIAGNOSTIC_LOG = Path(__file__).resolve().parent.parent / 'logs' / 'capture.jsonl'
CHUNK_BYTES = 65536
MAX_MESSAGE = 1_000_000
DIAGNOSTIC_LIMIT = 60
DIAGNOSTIC_STRING_LIMITS = {
    'event': 64, 'stage': 32, 'reason': 80, 'error': 500,
    'url': 2048, 'senderUrl': 2048, 'documentId': 128, 'visibility': 16,
}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS doms (
    hash TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)), bytes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY, url TEXT NOT NULL, top_url TEXT NOT NULL, title TEXT NOT NULL,
    captured_at INTEGER NOT NULL, tab_id INTEGER NOT NULL, frame_id INTEGER NOT NULL,
    document_id TEXT NOT NULL, dom_hash TEXT NOT NULL REFERENCES doms(hash)
);
CREATE INDEX IF NOT EXISTS snapshots_time ON snapshots(captured_at DESC);
CREATE INDEX IF NOT EXISTS snapshots_url ON snapshots(url);
CREATE TABLE IF NOT EXISTS page_visits (
    id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
    started_at INTEGER NOT NULL, ended_at INTEGER,
    focused_ms INTEGER NOT NULL, tab_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS page_visits_time ON page_visits(started_at DESC);
CREATE INDEX IF NOT EXISTS page_visits_url ON page_visits(url);
CREATE TABLE IF NOT EXISTS item_attention (
    id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
    item_hash TEXT NOT NULL, container TEXT NOT NULL,
    started_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
    visible_ms INTEGER NOT NULL, max_ratio REAL NOT NULL,
    tab_id INTEGER NOT NULL, document_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS item_attention_time ON item_attention(started_at DESC);
CREATE INDEX IF NOT EXISTS item_attention_hash ON item_attention(item_hash);
CREATE VIEW IF NOT EXISTS documents AS
    SELECT hash, bytes, json_extract(payload, '$.html') AS html,
        json_extract(payload, '$.shadowRoots') AS shadow_roots_json,
        json_extract(payload, '$.formState') AS form_state_json FROM doms;
CREATE TEMP TABLE uploads (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
CREATE TEMP TABLE parts (id TEXT NOT NULL, part_index INTEGER NOT NULL, data BLOB NOT NULL,
    PRIMARY KEY(id, part_index));
'''


def integer(value, minimum=0):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError('Invalid integer.')
    return value


class Archive:
    def __init__(self, path=DEFAULT_DATABASE, diagnostic_path=None):
        self.path = Path(path).resolve()
        self.diagnostic_path = Path(diagnostic_path).resolve() if diagnostic_path else (
            DEFAULT_DIAGNOSTIC_LOG if self.path == DEFAULT_DATABASE.resolve() else self.path.with_suffix('.log.jsonl'))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.execute('PRAGMA journal_mode = WAL')
        self.db.execute('PRAGMA synchronous = FULL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2, 3):
            raise ValueError('Unknown database version; no changes made.')
        self.db.executescript(SCHEMA)
        self.db.execute('PRAGMA user_version = 3')
        os.chmod(self.path, 0o600)

    def close(self):
        self.db.close()

    def handle(self, message):
        op = message.get('op')
        if op == 'status':
            return {'path': str(self.path), 'snapshots': self.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],
                    'documents': self.db.execute('SELECT count(*) FROM doms').fetchone()[0],
                    'visits': self.db.execute('SELECT count(*) FROM page_visits').fetchone()[0],
                    'diagnosticLog': str(self.diagnostic_path)}
        if op == 'diagnostics':
            records = message.get('records')
            if not isinstance(records, list) or len(records) > DIAGNOSTIC_LIMIT:
                raise ValueError('Invalid diagnostics.')
            clean = []
            for record in records:
                if not isinstance(record, dict) or not isinstance(record.get('at'), int) or not isinstance(record.get('event'), str):
                    raise ValueError('Invalid diagnostic record.')
                item = {'at': integer(record['at']), 'event': record['event'][:DIAGNOSTIC_STRING_LIMITS['event']]}
                for key, limit in DIAGNOSTIC_STRING_LIMITS.items():
                    if key != 'event' and isinstance(record.get(key), str) and record[key]:
                        item[key] = record[key][:limit]
                for key in ('tabId', 'frameId', 'bytes'):
                    if key in record:
                        item[key] = integer(record[key])
                clean.append(item)
            self.diagnostic_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = ''.join(json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n' for item in clean).encode('utf-8')
            if len(data) > 300_000:
                raise ValueError('Diagnostic log is too large.')
            temporary = self.diagnostic_path.with_name(self.diagnostic_path.name + '.tmp')
            temporary.write_bytes(data)
            os.chmod(temporary, 0o600)
            temporary.replace(self.diagnostic_path)
            return {'path': str(self.diagnostic_path), 'records': len(clean)}
        if op == 'attention':
            visit = message.get('visit')
            if not isinstance(visit, dict) or not re.fullmatch(r'[a-f0-9-]{36}', visit.get('id', '')):
                raise ValueError('Invalid page visit ID.')
            for key in ('url', 'title'):
                if not isinstance(visit.get(key), str):
                    raise ValueError('Invalid page visit metadata.')
            started = integer(visit.get('startedAt'))
            ended = visit.get('endedAt')
            if ended is not None and integer(ended) < started:
                raise ValueError('Invalid page visit end time.')
            focused = integer(visit.get('focusedMs'))
            tab_id = integer(visit.get('tabId'))
            with self.db:
                self.db.execute('''INSERT INTO page_visits(id,url,title,started_at,ended_at,focused_ms,tab_id)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        url=excluded.url, title=excluded.title,
                        ended_at=coalesce(excluded.ended_at,page_visits.ended_at),
                        focused_ms=max(page_visits.focused_ms,excluded.focused_ms)''',
                    (visit['id'], visit['url'], visit['title'], started, ended, focused, tab_id))
            return {'saved': True}
        if op == 'item-attention':
            item = message.get('observation')
            if not isinstance(item, dict) or not re.fullmatch(r'[a-f0-9-]{36}', item.get('id', '')):
                raise ValueError('Invalid item-attention ID.')
            if not re.fullmatch(r'[a-f0-9]{64}', item.get('itemHash', '')):
                raise ValueError('Invalid item hash.')
            for key in ('url', 'title', 'container', 'documentId'):
                if not isinstance(item.get(key), str):
                    raise ValueError('Invalid item-attention metadata.')
            started = integer(item.get('startedAt'))
            last_seen = integer(item.get('lastSeenAt'))
            visible = integer(item.get('visibleMs'))
            tab_id = integer(item.get('tabId'))
            ratio = item.get('maxRatio')
            if last_seen < started or not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 1:
                raise ValueError('Invalid item-attention timing.')
            with self.db:
                self.db.execute('''INSERT INTO item_attention
                    (id,url,title,item_hash,container,started_at,last_seen_at,visible_ms,max_ratio,tab_id,document_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        url=excluded.url,title=excluded.title,item_hash=excluded.item_hash,
                        last_seen_at=max(item_attention.last_seen_at,excluded.last_seen_at),
                        visible_ms=max(item_attention.visible_ms,excluded.visible_ms),
                        max_ratio=max(item_attention.max_ratio,excluded.max_ratio)''',
                    (item['id'], item['url'], item['title'], item['itemHash'], item['container'][:32],
                     started, last_seen, visible, float(ratio), tab_id, item['documentId'][:128]))
            return {'saved': True}
        if op == 'index':
            script = Path(__file__).resolve().parent.parent / 'scripts' / 'items.py'
            if not script.is_file():
                raise ValueError('Item index helper is missing.')
            subprocess.Popen([sys.executable, str(script), '--database', str(self.path), 'build'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
            return {'started': True}
        if op == 'begin':
            item = message['snapshot']
            if not isinstance(item, dict) or not re.fullmatch(r'[a-f0-9-]{36}', item.get('id', '')):
                raise ValueError('Invalid snapshot ID.')
            if not re.fullmatch(r'[a-f0-9]{64}', item.get('hash', '')):
                raise ValueError('Invalid DOM hash.')
            integer(item['bytes'], 1)
            if integer(item['chunks'], 1) != (item['bytes'] + CHUNK_BYTES - 1) // CHUNK_BYTES:
                raise ValueError('Incorrect chunk count.')
            for key in ('url', 'topUrl', 'title', 'documentId'):
                if not isinstance(item[key], str):
                    raise ValueError('Invalid snapshot metadata.')
            for key in ('capturedAt', 'tabId', 'frameId'):
                integer(item[key])
            previous = self.db.execute('SELECT dom_hash FROM snapshots WHERE id=?', (item['id'],)).fetchone()
            if previous:
                if previous[0] != item['hash']:
                    raise ValueError('Snapshot ID collision.')
                return {'saved': True}
            with self.db:
                self.db.execute('DELETE FROM parts WHERE id=?', (item['id'],))
                self.db.execute('INSERT OR REPLACE INTO uploads VALUES (?,?)', (item['id'], json.dumps(item)))
            return {'saved': False}
        if op == 'chunk':
            snapshot_id, index = message['snapshotId'], integer(message['index'])
            row = self.db.execute('SELECT metadata FROM uploads WHERE id=?', (snapshot_id,)).fetchone()
            if not row:
                raise ValueError('Upload was not started.')
            metadata = json.loads(row[0])
            count = self.db.execute('SELECT count(*) FROM parts WHERE id=?', (snapshot_id,)).fetchone()[0]
            if index != count or index >= metadata['chunks']:
                raise ValueError('Unexpected chunk index.')
            data = base64.b64decode(message['data'], validate=True)
            expected = min(CHUNK_BYTES, metadata['bytes'] - index * CHUNK_BYTES)
            if len(data) != expected:
                raise ValueError('Incorrect chunk size.')
            with self.db:
                self.db.execute('INSERT INTO parts VALUES (?,?,?)', (snapshot_id, index, data))
            return {'received': index}
        if op == 'commit':
            snapshot_id = message['snapshotId']
            row = self.db.execute('SELECT metadata FROM uploads WHERE id=?', (snapshot_id,)).fetchone()
            if not row:
                if self.db.execute('SELECT 1 FROM snapshots WHERE id=?', (snapshot_id,)).fetchone():
                    return {'saved': True}
                raise ValueError('Upload was not started.')
            item = json.loads(row[0])
            parts = self.db.execute('SELECT data FROM parts WHERE id=? ORDER BY part_index', (snapshot_id,)).fetchall()
            data = b''.join(part[0] for part in parts)
            if len(parts) != item['chunks'] or len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['hash']:
                raise ValueError('Incomplete or corrupt DOM; snapshot not committed.')
            payload = data.decode('utf-8', errors='strict')
            dom = json.loads(payload)
            if not isinstance(dom, dict) or not isinstance(dom.get('html'), str) or not isinstance(dom.get('shadowRoots'), list) or not isinstance(dom.get('formState'), list):
                raise ValueError('Invalid DOM payload.')
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO doms VALUES (?,?,?)', (item['hash'], payload, len(data)))
                self.db.execute('''INSERT INTO snapshots
                    (id,url,top_url,title,captured_at,tab_id,frame_id,document_id,dom_hash)
                    VALUES (?,?,?,?,?,?,?,?,?)''',
                    (snapshot_id, item['url'], item['topUrl'], item['title'], item['capturedAt'], item['tabId'], item['frameId'], item['documentId'], item['hash']))
                self.db.execute('DELETE FROM parts WHERE id=?', (snapshot_id,))
                self.db.execute('DELETE FROM uploads WHERE id=?', (snapshot_id,))
            return {'saved': True}
        if op == 'list':
            offset = integer(message.get('offset', 0))
            query = str(message.get('query', ''))
            day = str(message.get('day', ''))
            if day and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise ValueError('Invalid day.')
            if day:
                try:
                    start = datetime.fromisoformat(day)
                except ValueError as error:
                    raise ValueError('Invalid day.') from error
                start_ms = int(start.timestamp() * 1000)
                end_ms = int((start + timedelta(days=1)).timestamp() * 1000)
            else:
                start_ms, end_ms = 0, 10**16
            # Search the sole DOM source, not a second text capture or index.
            rows = self.db.execute('''SELECT s.id, substr(s.url,1,1024) AS url, substr(s.title,1,240) AS title,
                s.captured_at AS lastSeen, s.frame_id AS frameId, d.bytes
                FROM snapshots s JOIN doms d ON s.dom_hash=d.hash
                WHERE s.captured_at>=? AND s.captured_at<?
                    AND (?='' OR instr(lower(s.url || s.title || d.payload), lower(?))>0)
                ORDER BY s.captured_at DESC, s.id DESC LIMIT 101 OFFSET ?''',
                (start_ms, end_ms, query, query, offset)).fetchall()
            return {'items': [dict(row) for row in rows[:100]], 'more': len(rows) > 100, 'offset': offset}
        if op == 'days':
            rows = self.db.execute('''SELECT strftime('%Y-%m-%d', captured_at/1000, 'unixepoch', 'localtime') AS day,
                count(*) AS snapshots, max(captured_at) AS lastSeen
                FROM snapshots GROUP BY day ORDER BY day DESC''').fetchall()
            return {'items': [dict(row) for row in rows]}
        if op == 'get':
            row = self.db.execute('''SELECT s.id, s.url, s.top_url AS topUrl, s.title, s.captured_at AS lastSeen,
                s.frame_id AS frameId, s.document_id AS documentId, s.dom_hash AS hash, d.bytes
                FROM snapshots s JOIN doms d ON s.dom_hash=d.hash WHERE s.id=?''', (message['snapshotId'],)).fetchone()
            if not row:
                raise ValueError('Snapshot not found.')
            return dict(row)
        if op == 'read':
            offset = integer(message.get('offset', 0))
            row = self.db.execute('''SELECT substr(CAST(d.payload AS BLOB), ?, ?) AS data, d.bytes
                FROM snapshots s JOIN doms d ON s.dom_hash=d.hash WHERE s.id=?''',
                (offset + 1, CHUNK_BYTES, message['snapshotId'])).fetchone()
            if not row:
                raise ValueError('Snapshot not found.')
            return {'data': base64.b64encode(row['data']).decode('ascii'), 'bytes': row['bytes']}
        if op == 'delete':
            with self.db:
                self.db.execute('DELETE FROM snapshots WHERE id=?', (message['snapshotId'],))
                self.db.execute('DELETE FROM doms WHERE NOT EXISTS (SELECT 1 FROM snapshots WHERE dom_hash=doms.hash)')
            return True
        if op == 'clear':
            with self.db:
                self.db.execute('DELETE FROM snapshots')
                self.db.execute('DELETE FROM doms')
                self.db.execute('DELETE FROM page_visits')
                self.db.execute('DELETE FROM item_attention')
                self.db.execute('DELETE FROM uploads')
                self.db.execute('DELETE FROM parts')
            return True
        raise ValueError('Unknown operation.')


def read_exact(stream, size):
    chunks = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise EOFError('Incomplete native message.')
        chunks.append(chunk)
        size -= len(chunk)
    return b''.join(chunks)


def serve(archive, source, target):
    while True:
        header = source.read(4)
        if not header:
            return
        if len(header) != 4:
            header += read_exact(source, 4 - len(header))
        size = struct.unpack('=I', header)[0]
        if size > MAX_MESSAGE:
            raise ValueError('Native message exceeds protocol limit.')
        message = json.loads(read_exact(source, size))
        try:
            result = {'id': message.get('id'), 'ok': True, 'value': archive.handle(message)}
            output = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            if len(output) > MAX_MESSAGE:
                raise ValueError('Response exceeds protocol limit; use chunked reads.')
        except Exception as error:
            output = json.dumps({'id': message.get('id'), 'ok': False, 'error': str(error)}).encode('utf-8')
        target.write(struct.pack('=I', len(output)))
        target.write(output)
        target.flush()


if __name__ == '__main__':
    os.umask(0o077)
    archive = Archive()
    try:
        serve(archive, sys.stdin.buffer, sys.stdout.buffer)
    finally:
        archive.close()
