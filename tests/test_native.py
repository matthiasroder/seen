import base64
from datetime import datetime, timedelta
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import uuid

spec = importlib.util.spec_from_file_location('seen_host', Path(__file__).resolve().parents[1] / 'native/host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='seen-test-')
        self.archive = host.Archive(Path(self.directory.name) / 'seen.sqlite')

    def tearDown(self):
        self.archive.close()
        self.directory.cleanup()

    def payload(self, html='<html><script>doNotExecute()</script><p hidden>hidden</p></html>', **overrides):
        dom = {'html': html, 'shadowRoots': [{'path': [0, 1], 'mode': 'closed', 'html': '<b>inside</b>'}],
               'formState': [{'path': [0, 2], 'value': 'synthetic password'}]}
        data = json.dumps(dom, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        item = {'id': str(uuid.uuid4()), 'url': 'https://user:pass@example.test/login?q=value#part',
                'topUrl': 'https://example.test/', 'title': 'Grüße 🐻', 'capturedAt': 1788177600000,
                'tabId': 1, 'frameId': 0, 'documentId': 'doc', 'hash': hashlib.sha256(data).hexdigest(),
                'bytes': len(data), 'chunks': (len(data) + host.CHUNK_BYTES - 1) // host.CHUNK_BYTES}
        item.update(overrides)
        return item, data

    def upload(self, item, data, commit=True):
        self.archive.handle({'op': 'begin', 'snapshot': item})
        for index, start in enumerate(range(0, len(data), host.CHUNK_BYTES)):
            self.archive.handle({'op': 'chunk', 'snapshotId': item['id'], 'index': index,
                                 'data': base64.b64encode(data[start:start + host.CHUNK_BYTES]).decode()})
        if commit:
            return self.archive.handle({'op': 'commit', 'snapshotId': item['id']})

    def test_full_dom_and_metadata_unmodified(self):
        item, data = self.payload()
        self.assertTrue(self.upload(item, data)['saved'])
        row = self.archive.db.execute('SELECT payload FROM doms').fetchone()
        self.assertEqual(row[0].encode('utf-8'), data)
        meta = self.archive.handle({'op': 'get', 'snapshotId': item['id']})
        self.assertEqual(meta['url'], item['url'])
        self.assertEqual(meta['title'], item['title'])
        self.assertIn('<script>', self.archive.db.execute('SELECT html FROM documents').fetchone()[0])

    def test_bounded_diagnostics_write_an_atomic_local_jsonl_file(self):
        records = [{'at': 1788240000000, 'event': 'capture-rejected', 'reason': 'url-mismatch',
                    'url': 'https://example.test/article', 'senderUrl': 'https://example.test/',
                    'tabId': 1, 'frameId': 0, 'documentId': 'doc', 'html': 'never write DOM'}]
        result = self.archive.handle({'op': 'diagnostics', 'records': records})
        self.assertEqual(result['records'], 1)
        self.assertEqual(result['path'], str(self.archive.diagnostic_path))
        stored = [json.loads(line) for line in self.archive.diagnostic_path.read_text().splitlines()]
        self.assertEqual(stored[0]['reason'], 'url-mismatch')
        self.assertNotIn('html', stored[0])
        self.assertEqual(self.archive.handle({'op': 'diagnostics', 'records': []})['records'], 0)
        self.assertEqual(self.archive.diagnostic_path.read_text(), '')
        with self.assertRaises(ValueError):
            self.archive.handle({'op': 'diagnostics', 'records': records * (host.DIAGNOSTIC_LIMIT + 1)})

    def test_page_attention_is_separate_idempotent_and_cleared_with_archive(self):
        visit = {'id': str(uuid.uuid4()), 'url': 'https://example.test/article', 'title': 'Article',
                 'startedAt': 1000, 'endedAt': None, 'focusedMs': 500, 'tabId': 7}
        self.assertTrue(self.archive.handle({'op': 'attention', 'visit': visit})['saved'])
        self.archive.handle({'op': 'attention', 'visit': {**visit, 'endedAt': 3000, 'focusedMs': 1800}})
        row = self.archive.db.execute('SELECT * FROM page_visits').fetchone()
        self.assertEqual((row['focused_ms'], row['ended_at']), (1800, 3000))
        self.archive.handle({'op': 'attention', 'visit': visit})
        row = self.archive.db.execute('SELECT * FROM page_visits').fetchone()
        self.assertEqual((row['focused_ms'], row['ended_at']), (1800, 3000))
        self.assertEqual(self.archive.handle({'op': 'status'})['visits'], 1)
        self.archive.handle({'op': 'clear'})
        self.assertEqual(self.archive.handle({'op': 'status'})['visits'], 0)

    def test_version_one_archive_migrates_without_rewriting_snapshots(self):
        item, data = self.payload()
        self.upload(item, data)
        path = self.archive.path
        self.archive.db.execute('DROP TABLE page_visits')
        self.archive.db.execute('PRAGMA user_version=1')
        self.archive.db.commit()
        self.archive.close()
        self.archive = host.Archive(path)
        self.assertEqual(self.archive.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(self.archive.handle({'op': 'status'}), {
            'path': str(path), 'snapshots': 1, 'documents': 1, 'visits': 0,
            'diagnosticLog': str(path.with_suffix('.log.jsonl'))
        })

    def test_visible_item_attention_is_idempotent_and_index_launch_is_separate(self):
        observation = {'id': str(uuid.uuid4()), 'url': 'https://example.test/feed', 'title': 'Feed',
                       'itemHash': 'a' * 64, 'container': 'article', 'startedAt': 1000,
                       'lastSeenAt': 3000, 'visibleMs': 1500, 'maxRatio': 0.75,
                       'tabId': 7, 'documentId': 'doc'}
        self.assertTrue(self.archive.handle({'op': 'item-attention', 'observation': observation})['saved'])
        self.archive.handle({'op': 'item-attention', 'observation': {**observation,
                            'lastSeenAt': 5000, 'visibleMs': 3200, 'maxRatio': 0.9}})
        row = self.archive.db.execute('SELECT * FROM item_attention').fetchone()
        self.assertEqual((row['visible_ms'], row['last_seen_at'], row['max_ratio']), (3200, 5000, 0.9))
        with mock.patch.object(host.subprocess, 'Popen') as launch:
            self.assertTrue(self.archive.handle({'op': 'index'})['started'])
            launch.assert_called_once()
        self.archive.handle({'op': 'clear'})
        self.assertEqual(self.archive.db.execute('SELECT count(*) FROM item_attention').fetchone()[0], 0)

    def test_unicode_across_many_chunks_and_chunked_read(self):
        item, data = self.payload('<html>' + 'Österreich 🐻' * 90000 + '</html>')
        self.upload(item, data)
        recovered = b''
        while len(recovered) < len(data):
            message = self.archive.handle({'op': 'read', 'snapshotId': item['id'], 'offset': len(recovered)})
            recovered += base64.b64decode(message['data'])
        self.assertEqual(data, recovered)

    def test_incomplete_upload_never_visible(self):
        item, _ = self.payload('x' * 200000)
        self.archive.handle({'op': 'begin', 'snapshot': item})
        with self.assertRaises(ValueError):
            self.archive.handle({'op': 'commit', 'snapshotId': item['id']})
        self.assertEqual(self.archive.handle({'op': 'status'})['snapshots'], 0)

    def test_hash_mismatch_never_committed(self):
        item, data = self.payload(hash='0' * 64)
        self.upload(item, data, commit=False)
        with self.assertRaises(ValueError):
            self.archive.handle({'op': 'commit', 'snapshotId': item['id']})
        self.assertEqual(self.archive.handle({'op': 'status'})['documents'], 0)

    def test_retry_idempotent_and_content_deduplicated(self):
        item, data = self.payload()
        self.upload(item, data)
        self.assertTrue(self.archive.handle({'op': 'begin', 'snapshot': item})['saved'])
        another = {**item, 'id': str(uuid.uuid4()), 'frameId': 2}
        self.upload(another, data)
        status = self.archive.handle({'op': 'status'})
        self.assertEqual((status['snapshots'], status['documents']), (2, 1))

    def test_disconnect_restart_retry(self):
        item, data = self.payload()
        self.upload(item, data, commit=False)
        path = self.archive.path
        self.archive.close()
        self.archive = host.Archive(path)
        self.assertEqual(self.archive.handle({'op': 'status'})['snapshots'], 0)
        self.assertTrue(self.upload(item, data)['saved'])

    def test_search_pagination_and_no_automatic_retention(self):
        for index in range(103):
            item, data = self.payload(capturedAt=index, url='https://example.test/' + str(index))
            self.upload(item, data)
        first = self.archive.handle({'op': 'list', 'query': 'doNotExecute'})
        second = self.archive.handle({'op': 'list', 'offset': 100})
        self.assertEqual(len(first['items']), 100)
        self.assertTrue(first['more'])
        self.assertEqual(len(second['items']), 3)
        self.assertFalse(second['more'])
        self.assertEqual(self.archive.handle({'op': 'status'})['snapshots'], 103)
        self.assertEqual(self.archive.handle({'op': 'list', 'query': "' OR 1=1 --"})['items'], [])

    def test_days_are_lightweight_and_snapshots_load_only_for_the_selected_day(self):
        first = datetime(2026, 8, 30, 12)
        for index, captured in enumerate((first, first + timedelta(hours=1), first + timedelta(days=1))):
            item, data = self.payload(capturedAt=int(captured.timestamp() * 1000), url=f'https://example.test/{index}')
            self.upload(item, data)
        days = self.archive.handle({'op': 'days'})['items']
        self.assertEqual([(row['day'], row['snapshots']) for row in days], [('2026-08-31', 1), ('2026-08-30', 2)])
        selected = self.archive.handle({'op': 'list', 'day': '2026-08-30'})
        self.assertEqual(len(selected['items']), 2)
        self.assertTrue(all('/2' not in row['url'] for row in selected['items']))
        with self.assertRaises(ValueError):
            self.archive.handle({'op': 'list', 'day': '2026-99-99'})

    def test_delete_preserves_shared_document_until_last_reference(self):
        item, data = self.payload()
        another = {**item, 'id': str(uuid.uuid4())}
        self.upload(item, data)
        self.upload(another, data)
        self.archive.handle({'op': 'delete', 'snapshotId': item['id']})
        self.assertEqual(self.archive.handle({'op': 'status'})['documents'], 1)
        self.archive.handle({'op': 'clear'})
        self.assertEqual(self.archive.handle({'op': 'status'})['documents'], 0)

    def test_protocol_framing_and_error_recovery(self):
        messages = [{'id': 'first', 'op': 'unknown'}, {'id': 'second', 'op': 'status'}]
        stream = b''
        for message in messages:
            data = json.dumps(message).encode()
            stream += struct.pack('=I', len(data)) + data
        output = io.BytesIO()
        host.serve(self.archive, io.BytesIO(stream), output)
        output.seek(0)
        first = json.loads(host.read_exact(output, struct.unpack('=I', output.read(4))[0]))
        second = json.loads(host.read_exact(output, struct.unpack('=I', output.read(4))[0]))
        self.assertFalse(first['ok'])
        self.assertTrue(second['ok'])
        self.assertEqual(second['id'], 'second')

    def test_chunk_order_and_size_enforced(self):
        item, data = self.payload()
        self.archive.handle({'op': 'begin', 'snapshot': item})
        for index, content in [(1, data), (0, b'bad')]:
            with self.assertRaises(ValueError):
                self.archive.handle({'op': 'chunk', 'snapshotId': item['id'], 'index': index,
                                     'data': base64.b64encode(content).decode()})


if __name__ == '__main__':
    unittest.main()
