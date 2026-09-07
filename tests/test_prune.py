import importlib.util
import json
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('seen_prune', Path(__file__).resolve().parents[1] / 'scripts/prune-noisy.py')
prune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prune)


class PruneTests(unittest.TestCase):
    def row(self, html):
        return {'url': 'https://example.test/article', 'title': 'Article',
                'payload': json.dumps({'html': html, 'shadowRoots': [], 'formState': []})}

    def test_ad_and_explicitly_hidden_churn_is_not_meaningful(self):
        first = self.row('<main>Story</main><aside class="advertisement">One</aside><p hidden>A</p>')
        second = self.row('<main>Story</main><aside class="advertisement">Two</aside><p hidden>B</p>')
        self.assertEqual(prune.fingerprint(first), prune.fingerprint(second))

    def test_visible_content_and_links_are_meaningful(self):
        first = self.row('<main>Story one <a href="/one">Read</a></main>')
        second = self.row('<main>Story two <a href="/two">Read</a></main>')
        self.assertNotEqual(prune.fingerprint(first), prune.fingerprint(second))


if __name__ == '__main__':
    unittest.main()
