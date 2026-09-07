#!/usr/bin/env python3
"""Bounded, read-only retrieval from Seen. No browser, network, or persistent index."""
import argparse
from datetime import datetime, timedelta
from html.parser import HTMLParser
import json
from itertools import islice
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from deep_links import verification_links

DEFAULT_DB = str(Path(__file__).resolve().parents[3] / 'data' / 'seen.sqlite')
BLOCK_TAGS = {'p', 'div', 'section', 'article', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'hr', 'tr', 'td', 'blockquote', 'pre', 'header', 'footer'}


class ContentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = None

    def handle_starttag(self, tag, attrs):
        if self.skip:
            return
        if tag in {'script', 'style'}:
            self.skip = tag
        elif tag in BLOCK_TAGS:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip:
                self.skip = None
        elif tag in BLOCK_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return '\n'.join(line for line in (re.sub(r'\s+', ' ', line).strip() for line in ''.join(self.parts).splitlines()) if line)


def connect(path):
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


def boundary(value, timezone):
    if not value:
        return None
    if value in {'today', 'yesterday'}:
        day = datetime.now(timezone).date()
        if value == 'yesterday':
            day -= timedelta(days=1)
        parsed = datetime.combine(day, datetime.min.time(), timezone)
    else:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
    return int(parsed.timestamp() * 1000)


def stamp(value, timezone):
    return datetime.fromtimestamp(value / 1000, timezone).isoformat(timespec='seconds') if value is not None else None


def query_terms(value):
    return [' '.join(word.casefold().split()) for word in shlex.split(value) if word.strip()]


def term_pattern(term):
    left = r'(?<!\w)' if term[0].isalnum() or term[0] == '_' else ''
    right = r'(?!\w)' if term[-1].isalnum() or term[-1] == '_' else ''
    return re.compile(left + re.escape(term) + right)


def document_text(payload, mode):
    dom = json.loads(payload)
    if mode == 'raw':
        return json.dumps(dom, ensure_ascii=False, indent=2)
    fragments = [dom.get('html', '')] + [root.get('html', '') for root in dom.get('shadowRoots', [])]
    result = []
    for fragment in fragments:
        parser = ContentParser()
        parser.feed(fragment)
        parser.close()
        result.append(parser.text())
    return '\n'.join(result)


def excerpts(text, terms, width, count):
    lines = [line for line in text.splitlines() if line.strip()]
    ranked = []
    patterns = [term_pattern(term) for term in terms]
    words = [term_pattern(word) for term in terms for word in term.split()]
    for index, line in enumerate(lines):
        folded = ' '.join(line.casefold().split())
        score = sum(bool(pattern.search(folded)) for pattern in patterns + words)
        if score:
            ranked.append((-score, index))
    choices = [index for _, index in sorted(ranked)] or ([0] if lines else [])
    result, covered = [], set()
    for index in choices:
        if index in covered:
            continue
        chunk = '\n'.join(lines[max(0, index - 1):index + 2])
        if len(chunk) > width:
            # Offsets are used only to center an excerpt, never as source locations.
            positions = [match.start() for pattern in patterns if (match := pattern.search(chunk.casefold()))]
            at = min(positions) if positions else 0
            start = max(0, min(at - width // 3, len(chunk) - width))
            chunk = ('…' if start else '') + chunk[start:start + width] + ('…' if start + width < len(chunk) else '')
        result.append(chunk)
        covered.update(range(max(0, index - 1), index + 2))
        if len(result) >= count:
            break
    return result


def metadata(row, timezone):
    return {'snapshot_id': row['id'], 'url': row['url'], 'top_url': row['top_url'], 'title': row['title'],
            'captured_at': stamp(row['captured_at'], timezone), 'first_captured_at': stamp(row['first_ms'], timezone),
            'identical_captures_in_window': row['copies'], 'frame_id': row['frame_id'], 'dom_hash': row['dom_hash'], 'dom_bytes': row['bytes']}


def candidates(db, since=None, before=None, domain=None, snapshot_id=None):
    clauses, params = [], []
    if since is not None:
        clauses.append('s.captured_at >= ?'); params.append(since)
    if before is not None:
        clauses.append('s.captured_at < ?'); params.append(before)
    if snapshot_id:
        clauses.append('s.id = ?'); params.append(snapshot_id)
    if domain:
        # Cheap prefilter; exact host/subdomain validation occurs before parsing.
        clauses.append('instr(lower(s.url), ?) > 0'); params.append(domain.casefold())
    where = ' AND '.join(clauses) if clauses else '1'
    sql = '''WITH ranked AS (
        SELECT s.*, row_number() OVER (PARTITION BY s.url,s.dom_hash ORDER BY s.captured_at DESC,s.id DESC) AS rank,
        min(s.captured_at) OVER (PARTITION BY s.url,s.dom_hash) AS first_ms,
        count(*) OVER (PARTITION BY s.url,s.dom_hash) AS copies
        FROM snapshots s WHERE ''' + where + ''')
        SELECT r.*,d.bytes FROM ranked r JOIN doms d ON r.dom_hash=d.hash
        WHERE r.rank=1 ORDER BY r.captured_at DESC,r.id DESC'''
    return db.execute(sql, params)


def retrieve(db, args):
    timezone = ZoneInfo(args.timezone)
    since, before = boundary(args.since, timezone), boundary(args.before, timezone)
    if since is not None and before is not None and since >= before:
        raise ValueError('--since must be earlier than --before.')
    domain = args.domain.casefold().rstrip('.') if args.domain else None
    if domain and ('/' in domain or ':' in domain or not domain.strip()):
        raise ValueError('--domain expects a hostname, not a URL.')
    terms = query_terms(args.query)
    patterns = [term_pattern(term) for term in terms]
    examined = scanned = scanned_bytes = 0
    skipped, results = [], []
    stopped = None
    deadline = time.monotonic() + args.max_seconds
    for row in candidates(db, since, before, domain, getattr(args, 'snapshot_id', None)):
        if domain:
            hostname = (urlsplit(row['url']).hostname or '').casefold().rstrip('.')
            if hostname != domain and not hostname.endswith('.' + domain):
                continue
        if examined >= args.max_documents:
            stopped = 'document_limit'; break
        if time.monotonic() > deadline:
            stopped = 'time_limit'; break
        examined += 1
        if row['bytes'] > args.max_bytes - scanned_bytes:
            if row['bytes'] > args.max_bytes:
                skipped.append({'snapshot_id': row['id'], 'reason': 'document_exceeds_byte_budget', 'dom_bytes': row['bytes']})
                continue
            stopped = 'byte_limit'; break
        payload = db.execute('SELECT payload FROM doms WHERE hash=?', (row['dom_hash'],)).fetchone()[0]
        scanned += 1; scanned_bytes += row['bytes']
        try:
            text = document_text(payload, args.mode)
        except (ValueError, TypeError, AttributeError) as error:
            skipped.append({'snapshot_id': row['id'], 'reason': 'invalid_dom', 'detail': str(error)})
            continue
        body = ' '.join(text.casefold().split())
        fields = ' '.join((row['title'] + ' ' + row['url']).casefold().split())
        if all(pattern.search(body) or pattern.search(fields) for pattern in patterns):
            entry = metadata(row, timezone)
            entry['excerpts'] = excerpts(text, terms, args.context, args.excerpts)
            entry['match_in_dom_content'] = all(pattern.search(body) is not None for pattern in patterns)
            entry['score'] = sum(sum(1 for _ in islice(pattern.finditer(body), 20)) for pattern in patterns)
            results.append(entry)
    results.sort(key=lambda row: (row['score'], row['captured_at']), reverse=True)
    selected = results[:args.limit]
    # Resolve links only for returned snapshots, without widening the archive scan.
    for entry in selected:
        payload = db.execute('SELECT payload FROM doms WHERE hash=?', (entry['dom_hash'],)).fetchone()[0]
        entry['verification'] = verification_links(payload, entry['url'], terms, patterns, excerpts, args.context)
    return {'database': str(Path(args.database).resolve()), 'mode': args.mode, 'query': args.query,
            'timezone': args.timezone, 'since': stamp(since, timezone), 'before': stamp(before, timezone), 'domain': domain,
            'examined_documents': examined, 'scanned_documents': scanned, 'scanned_bytes': scanned_bytes, 'scan_complete': stopped is None and not skipped,
            'stopped_reason': stopped, 'skipped': skipped, 'matches_in_scan': len(results),
            'results_truncated': len(results) > args.limit, 'results': selected}


def stats(db, timezone):
    row = db.execute('SELECT count(*) AS snapshots,count(DISTINCT url) AS urls,min(captured_at) AS first,max(captured_at) AS last FROM snapshots').fetchone()
    dom = db.execute('SELECT count(*) AS documents,coalesce(sum(bytes),0) AS bytes FROM doms').fetchone()
    return {'snapshots': row['snapshots'], 'distinct_urls': row['urls'], 'unique_doms': dom['documents'],
            'payload_bytes': dom['bytes'], 'first_capture': stamp(row['first'], timezone), 'latest_capture': stamp(row['last'], timezone)}


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('Expected a positive integer.')
    return number


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=DEFAULT_DB)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('search', 'show'):
        sub = commands.add_parser(command)
        if command == 'search':
            sub.add_argument('query')
        else:
            sub.add_argument('snapshot_id'); sub.add_argument('--query', default='')
        sub.add_argument('--mode', choices=('content', 'raw'), default='content')
        sub.add_argument('--domain'); sub.add_argument('--since'); sub.add_argument('--before')
        sub.add_argument('--timezone', default='UTC')
        sub.add_argument('--limit', type=positive, default=10)
        sub.add_argument('--max-documents', type=positive, default=200)
        sub.add_argument('--max-bytes', type=positive, default=64 * 1024 * 1024)
        sub.add_argument('--max-seconds', type=positive, default=20)
        sub.add_argument('--context', type=positive, default=1000)
        sub.add_argument('--excerpts', type=positive, default=3)
    sub = commands.add_parser('stats'); sub.add_argument('--timezone', default='UTC')
    args = parser.parse_args()
    db = None
    try:
        db = connect(args.database)
        result = stats(db, ZoneInfo(args.timezone)) if args.command == 'stats' else retrieve(db, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, sqlite3.Error, OSError, KeyError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 1
    finally:
        if db is not None:
            db.close()
    return 0


if __name__ == '__main__':
    sys.exit(cli())
