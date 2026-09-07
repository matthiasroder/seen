import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    'seen_items', Path(__file__).resolve().parents[1] / 'scripts' / 'items.py')
items = importlib.util.module_from_spec(spec)
spec.loader.exec_module(items)


def encode_linkedin_id(ident, outer):
    value = ident << 1
    encoded = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        encoded.append(byte | (128 if value else 0))
        if not value:
            break
    inner = b'\x08' + bytes(encoded)
    payload = bytes((outer, len(inner))) + inner
    return base64.urlsafe_b64encode(payload).decode().rstrip('=')


def linkedin_post(author, body, ident, outer=0x12, sponsored=False):
    encoded = encode_linkedin_id(ident, outer)
    marker = '<span>Sponsored</span>' if sponsored else ''
    return f'''<div role="listitem">{marker}
        <button aria-label="Open control menu for post by {author}"></button>
        <p><span data-testid="expandable-text-box">{body}</span></p>
        <div id="{encoded}-replaceableCommentToolslocal-keyFeedType_MAIN_FEED_RECENT"></div>
    </div>'''


class ItemIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='seen-items-test-')
        self.source_path = Path(self.temp.name) / 'seen.sqlite'
        self.index_path = Path(self.temp.name) / 'seen-analysis.sqlite'
        self.writer = sqlite3.connect(self.source_path)
        self.writer.executescript('''PRAGMA user_version=3;
            CREATE TABLE doms(hash TEXT PRIMARY KEY,payload TEXT,bytes INTEGER);
            CREATE TABLE snapshots(id TEXT PRIMARY KEY,url TEXT,top_url TEXT,title TEXT,
            captured_at INTEGER,tab_id INTEGER,frame_id INTEGER,document_id TEXT,dom_hash TEXT);
            CREATE TABLE page_visits(id TEXT PRIMARY KEY,url TEXT,title TEXT,started_at INTEGER,
            ended_at INTEGER,focused_ms INTEGER,tab_id INTEGER);
            CREATE TABLE item_attention(id TEXT PRIMARY KEY,url TEXT,title TEXT,item_hash TEXT,
            container TEXT,started_at INTEGER,last_seen_at INTEGER,visible_ms INTEGER,max_ratio REAL,
            tab_id INTEGER,document_id TEXT);''')

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def add(self, ident, html, url='https://www.linkedin.com/feed/', at=1788177600000,
            title='Feed | LinkedIn', frame=0):
        payload = json.dumps({'html': html, 'shadowRoots': [], 'formState': []},
                             ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self.writer.execute('INSERT OR IGNORE INTO doms VALUES (?,?,?)',
                            (digest, payload, len(payload.encode())))
        self.writer.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)',
                            (ident, url, url, title, at, 1, frame, 'doc', digest))
        self.writer.commit()

    def index(self):
        return items.build(self.source_path, self.index_path)

    def search(self, query, **options):
        return items.search(self.index_path, query, **options)

    def test_linkedin_posts_are_split_deduplicated_and_linked_by_urn_type(self):
        ugc_id = 7500000000000000000
        activity_id = 7500000000000000001
        html = '<html><body><div role="list">' + linkedin_post(
            'Sif Example',
            'A hacker cohort presented many projects. One agent caught another agent lying during a demo.',
            ugc_id,
        ) + linkedin_post(
            'Ada Example',
            'Trustworthy systems require verification, evidence, and explicit uncertainty throughout deployment.',
            activity_id,
            outer=0x0A,
        ) + linkedin_post(
            'Advertisement',
            'Buy this unrelated product because the promotional copy is deliberately long enough to index.',
            7500000000000000002,
            sponsored=True,
        ) + '</div></body></html>'
        self.add('one', html, at=1000)
        self.add('two', html, at=2000)
        source_before = self.source_path.read_bytes()

        result = self.index()

        self.assertEqual(result['snapshots_indexed'], 2)
        self.assertEqual(result['items'], 2)
        self.assertEqual(result['versions'], 2)
        self.assertEqual(result['occurrences'], 4)
        self.assertEqual(source_before, self.source_path.read_bytes())
        incidental = self.search('"caught another agent"')['results']
        self.assertEqual(len(incidental), 1)
        self.assertEqual(incidental[0]['author'], 'Sif Example')
        self.assertEqual(incidental[0]['occurrences'], 2)
        self.assertEqual(
            incidental[0]['canonical_url'],
            f'https://www.linkedin.com/feed/update/urn:li:ugcPost:{ugc_id}/',
        )
        complete = items.show(self.index_path, incidental[0]['item_id'])
        self.assertIn('hacker cohort presented many projects', complete['body'])
        self.assertEqual(len(complete['occurrences']), 2)
        self.assertEqual(len(complete['versions']), 1)
        central = self.search('verification uncertainty')['results']
        self.assertEqual([row['author'] for row in central], ['Ada Example'])
        self.assertEqual(
            central[0]['canonical_url'],
            f'https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/',
        )

    def test_build_is_incremental_and_preserves_changed_versions(self):
        ident = 7500000000000000000
        self.add('one', '<html><body>' + linkedin_post(
            'Author', 'A sufficiently long first version of this post discusses orchestral creativity in detail.', ident,
        ) + '</body></html>', at=1000)
        first = self.index()
        self.assertEqual((first['items'], first['versions'], first['snapshots_added']), (1, 1, 1))
        self.add('two', '<html><body>' + linkedin_post(
            'Author', 'A sufficiently long revised version discusses orchestral creativity and machine agency.', ident,
        ) + '</body></html>', at=2000)
        second = self.index()
        self.assertEqual((second['items'], second['versions'], second['snapshots_added']), (1, 2, 1))
        third = self.index()
        self.assertEqual(third['snapshots_added'], 0)
        matches = self.search('orchestral creativity')['results']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['versions'], 2)
        self.assertEqual(matches[0]['occurrences'], 2)

    def test_generic_article_uses_primary_content_and_canonical_url(self):
        html = '''<html><head>
            <title>Fallback title</title>
            <meta property="og:title" content="A useful essay">
            <meta name="author" content="Example Writer">
            <link rel="canonical" href="https://example.test/essay?utm_source=feed&amp;edition=1#part">
            </head><body><nav>Navigationneedle</nav><article>
            <h1>A useful essay</h1><p>Careful reasoning requires evidence, counterarguments,
            and enough context to distinguish the main claim from a passing example.</p>
            <script>Scriptneedle</script></article></body></html>'''
        self.add('article', html, url='https://example.test/essay?utm_source=other',
                 title='Fallback title', at=3000)
        result = self.index()
        self.assertEqual(result['items'], 1)
        matches = self.search('counterarguments context')['results']
        self.assertEqual(matches[0]['kind'], 'article')
        self.assertEqual(matches[0]['title'], 'A useful essay')
        self.assertEqual(matches[0]['author'], 'Example Writer')
        self.assertEqual(matches[0]['canonical_url'], 'https://example.test/essay?edition=1')
        self.assertEqual(self.search('Scriptneedle')['count'], 0)
        self.assertEqual(self.search('Navigationneedle')['count'], 0)

    def test_x_feed_articles_become_distinct_posts(self):
        html = '''<html><body>
            <article><a href="/one/status/123"><time>now</time></a><div>Writer One</div><div>@one</div>
            <div data-testid="tweetText">A complete post about creative practice and careful listening.</div></article>
            <article><a href="/two/status/456">Writer Two</a><div>@two</div>
            <div data-testid="tweetText">A separate post about machine agency and human judgment.</div>
            <div data-testid="tweetText">Quoted material remains visibly separated from the main post.</div></article>
            <article><span>Ad</span><a href="/seller/status/789">Seller</a><div>@seller</div>
            <div data-testid="tweetText">Promotional material is long enough but must stay out.</div></article>
            </body></html>'''
        self.add('x', html, url='https://x.com/home', title='Home / X', at=4000)
        result = self.index()
        self.assertEqual(result['items'], 2)
        creative = self.search('creative listening')['results'][0]
        self.assertEqual(creative['kind'], 'x_post')
        self.assertEqual(creative['author'], 'Writer One')
        self.assertEqual(creative['canonical_url'], 'https://x.com/one/status/123')
        agency = items.show(self.index_path, self.search('machine judgment')['results'][0]['item_id'])
        self.assertIn('[Quoted post]', agency['body'])
        self.assertEqual(self.search('promotional material')['count'], 0)

    def test_subframes_and_short_or_malformed_items_are_not_indexed(self):
        malformed = '''<html><body><div role="listitem">
            <button aria-label="Open control menu for post by Brief Author"></button>
            <span data-testid="expandable-text-box">Too short.</span>
            <div id="nonsense-replaceableCommentToolslocalFeedType_MAIN_FEED_RECENT"></div>
            </div></body></html>'''
        self.add('top', malformed, at=1000)
        self.add('frame', '<html><body><article>' + 'Frame content ' * 20 + '</article></body></html>',
                 url='https://example.test/frame', at=1001, frame=2)
        result = self.index()
        self.assertEqual(result['snapshots_indexed'], 1)
        self.assertEqual(result['items'], 0)

    def test_search_filters_dates_domains_and_authors(self):
        self.add('one', '<html><body><article>' + 'Learning evidence ' * 10 + '</article></body></html>',
                 url='https://notes.example.test/one', title='One', at=1000)
        self.add('two', '<html><body><article>' + 'Learning evidence ' * 10 + '</article></body></html>',
                 url='https://other.test/two', title='Two', at=2000)
        self.index()
        result = self.search('learning', domain='example.test', since='1970-01-01T00:00:01+00:00',
                             before='1970-01-01T00:00:01.500+00:00')
        self.assertEqual([row['title'] for row in result['results']], ['One'])

    def test_item_attention_matches_extracted_container_without_copying_text(self):
        html = '<html><body>' + linkedin_post(
            'Attentive Author', 'A sufficiently long post about deliberate practice and close reading.',
            7500000000000000000) + '</body></html>'
        self.add('one', html, at=1000)
        self.index()
        index = items.connect_index(self.index_path)
        row = index.execute('SELECT id,attention_key FROM items').fetchone()
        index.close()
        self.writer.execute('INSERT INTO item_attention VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            ('00000000-0000-0000-0000-000000000001', 'https://www.linkedin.com/feed/',
                             'Feed', row['attention_key'], 'div', 1000, 4000, 2500, 0.8, 1, 'doc'))
        self.writer.commit()
        shown = items.show(self.index_path, row['id'])
        self.assertEqual(shown['attention']['focused_visible_ms'], 2500)
        bundled = items.bundle(self.index_path, self.source_path)
        self.assertEqual(bundled['items'][0]['attention']['observations'], 1)

    def test_semantic_search_ranks_local_vectors_and_audit_is_bounded(self):
        self.add('one', '<html><body><article>' + 'Human judgment and careful oversight. ' * 6 +
                 '</article></body></html>', url='https://one.test/', title='Oversight', at=1000)
        self.add('two', '<html><body><article>' + 'Garden soil and tomato growing. ' * 6 +
                 '</article></body></html>', url='https://two.test/', title='Gardening', at=2000)
        self.index()
        index = items.connect_index(self.index_path, create=True)
        chunks = index.execute('''SELECT c.id,iv.title FROM item_chunks c
            JOIN item_versions iv ON iv.id=c.item_version_id''').fetchall()
        for chunk in chunks:
            vector = [1.0, 0.0] if chunk['title'] == 'Oversight' else [0.0, 1.0]
            index.execute('INSERT INTO embeddings VALUES (?,?,?,?)',
                          (chunk['id'], 'apple-nl-en', 2, items.pack_vector(vector)))
        index.commit(); index.close()
        with mock.patch.object(items, 'embed_texts', return_value=[{
                'id': 0, 'model': 'apple-nl-en', 'dimensions': 2, 'vector': [1.0, 0.0]}]):
            result = items.semantic_search(self.index_path, 'responsible decisions')
        self.assertEqual(result['results'][0]['title'], 'Oversight')
        self.assertEqual(items.audit(self.index_path, sample=1)['count'], 1)


if __name__ == '__main__':
    unittest.main()
