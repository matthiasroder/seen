import json
import unittest

import search
from deep_links import verification_links


class DeepLinkTests(unittest.TestCase):
    def links(self, html, query='needle', url='https://example.test/feed', shadows=None):
        terms = search.query_terms(query)
        return verification_links(json.dumps({'html': html, 'shadowRoots': shadows or []}), url,
                                  terms, [search.term_pattern(term) for term in terms], search.excerpts)

    def test_bookmark_is_associated_with_its_own_post(self):
        result = self.links('<article><a rel="bookmark" href="/posts/wrong">Yesterday</a><p>Other topic</p></article>'
                            '<article><a rel="bookmark" href="/posts/right">Today</a><p>The needle report.</p></article>')
        self.assertEqual([x['url'] for x in result['deep_links']], ['https://example.test/posts/right'])
        self.assertIn('needle report', result['deep_links'][0]['context'][0])
        self.assertEqual(result['deep_links'][0]['kind'], 'post_link')
        self.assertFalse(result['live_checked'])

    def test_missing_link_does_not_borrow_from_another_post(self):
        result = self.links('<article><a rel="bookmark" href="/other">Read</a><p>Other topic</p></article>'
                            '<article><p>Needle without a link</p></article>')
        self.assertEqual(result['deep_links'], [])
        self.assertEqual(result['fallback_url'], 'https://example.test/feed')
        self.assertIsNotNone(result['fallback_reason'])

    def test_unclassified_links_keep_same_post_context_for_verification(self):
        result = self.links('<article><a href="/posts/wrong">2h</a>Other topic</article>'
                            '<article><a href="/authors/writer">Writer</a><a href="/posts/right" aria-label="Post published two hours ago">2h</a><p>Needle report</p></article>')
        self.assertEqual(result['deep_links'], [])
        candidates = result['related_links']
        self.assertEqual([x['url'] for x in candidates], ['https://example.test/authors/writer', 'https://example.test/posts/right'])
        self.assertEqual(candidates[1]['aria_label'], 'Post published two hours ago')
        self.assertTrue(all('Needle report' in x['context'][0] for x in candidates))

    def test_related_link_cap_is_explicit(self):
        result = self.links('<article><p>Needle</p>' + ''.join(f'<a href="/{i}">Link</a>' for i in range(20)) + '</article>')
        self.assertEqual(len(result['related_links']), 10)
        self.assertTrue(result['related_links_truncated'])

    def test_nested_posts_are_not_misattributed(self):
        result = self.links('<article><a rel="bookmark" href="/parent">Parent</a><p>Commentary</p>'
                            '<article><a rel="bookmark" href="/quoted">Quoted</a><p>Needle</p></article></article>')
        self.assertEqual([x['url'] for x in result['deep_links']], ['https://example.test/quoted'])

    def test_query_words_split_between_posts_do_not_prove_a_permalink(self):
        result = self.links('<article><a rel="bookmark" href="/one">Read</a>Artificial</article>'
                            '<article><a rel="bookmark" href="/two">Read</a>Orchestras</article>', 'artificial orchestras')
        self.assertEqual(result['deep_links'], [])

    def test_timestamp_and_role_article(self):
        result = self.links('<div role="article"><a href="/entry/42"><time datetime="2026-08-31">Today</time></a>'
                            '<p>Needle</p><a href="/profile">Author</a></div>')
        self.assertEqual(result['deep_links'][0]['url'], 'https://example.test/entry/42')
        self.assertEqual(result['deep_links'][0]['evidence'], 'timestamp_in_matching_record')
        self.assertEqual(len(result['deep_links']), 1)

    def test_schema_item_url(self):
        result = self.links('<div itemscope itemtype="https://schema.org/SocialMediaPosting">'
                            '<link itemprop="url" href="/entry/42"><p>Needle</p></div>')
        self.assertEqual(result['deep_links'][0]['evidence'], 'item_url_in_matching_record')

    def test_relative_href_and_captured_base(self):
        result = self.links('<head><base href="https://archive.test/articles/"></head>'
                            '<article><p>Needle</p><a rel="bookmark" href="42?lang=en&amp;view=full">Read</a></article>')
        self.assertEqual(result['deep_links'][0]['url'], 'https://archive.test/articles/42?lang=en&view=full')

    def test_shadow_fragments_are_scoped_independently(self):
        result = self.links('<article><a rel="bookmark" href="/wrong">Read</a>Unrelated</article>',
                            shadows=[{'html': '<article>Needle <a rel="bookmark" href="/shadow">Read</a></article>'}])
        self.assertEqual([x['url'] for x in result['deep_links']], ['https://example.test/shadow'])

    def test_non_web_unsafe_and_download_links_are_excluded(self):
        for href in ['javascript:alert(1)', 'data:text/html,Needle', 'file:///tmp/page', 'mailto:a@example.test',
                     'https://user:pass@example.test/post', 'https://example.test:bad/post', 'https://example.test/\\post']:
            with self.subTest(href=href):
                result = self.links(f'<article>Needle <a rel="bookmark" href="{href}">Read</a></article>')
                self.assertEqual(result['deep_links'], [])
        self.assertEqual(self.links('<article>Needle <a rel="bookmark" href="/file" download>Get</a></article>')['deep_links'], [])

    def test_linked_text_is_not_mislabeled_as_a_post_permalink(self):
        result = self.links('<p>See <a href="/reference">Needle research</a> for details.</p>')
        self.assertEqual(result['deep_links'][0]['kind'], 'content_link')
        self.assertIsNotNone(result['fallback_reason'])

    def test_same_page_fragment_is_not_mislabeled_as_a_post_permalink(self):
        result = self.links('<article id="entry">Needle <a rel="bookmark" href="#entry">Read</a></article>')
        self.assertEqual(result['deep_links'][0]['kind'], 'page_fragment')
        self.assertIsNotNone(result['fallback_reason'])

    def test_scripts_templates_and_canonical_feed_url_do_not_supply_post_links(self):
        result = self.links('<link rel="canonical" href="/feed"><script>Needle /posts/123</script>'
                            '<template><article>Needle <a rel="bookmark" href="/hidden">Read</a></article></template>'
                            '<article data-urn="urn:anything:123">Needle</article>')
        self.assertEqual(result['deep_links'], [])

    def test_no_domain_specific_url_construction(self):
        html = '<div data-id="opaque-record">Needle <a rel="bookmark" href="/whatever/42">Read</a></div>'
        for domain in ['one.test', 'two.example', 'three.invalid']:
            result = self.links(html, url='https://' + domain + '/feed')
            self.assertEqual(result['deep_links'][0]['url'], 'https://' + domain + '/whatever/42')

    def test_deduplication_and_output_cap(self):
        html = ''.join(f'<article>Needle <a rel="bookmark" href="/p/{i}">Read</a>'
                       f'<a href="/p/{i}"><time>Today</time></a></article>' for i in range(7))
        result = self.links(html)
        self.assertEqual(len(result['deep_links']), 5)
        self.assertTrue(result['links_truncated'])
        self.assertTrue(all(x['evidence'] == 'bookmark_in_matching_record' for x in result['deep_links']))

    def test_raw_script_only_query_does_not_acquire_a_body_permalink(self):
        result = self.links('<article><script>needle</script>Other content <a rel="bookmark" href="/other">Read</a></article>')
        self.assertEqual(result['deep_links'], [])

    def test_inline_unicode_and_unclosed_markup(self):
        result = self.links('<article><a rel="bookmark" href="/unicode">Read</a><p>Artifi<b>cial</b> Straße', 'artificial strasse')
        self.assertEqual(result['deep_links'][0]['url'], 'https://example.test/unicode')


if __name__ == '__main__':
    unittest.main()
