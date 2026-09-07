"""Conservative, site-independent link evidence from saved HTML. Never fetch URLs."""
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
BLOCK = {'p', 'div', 'section', 'article', 'li', 'ul', 'ol', 'br', 'hr', 'header', 'footer', 'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


def http_url(value, base):
    """Only resolve actual saved hrefs; no URL templates or ID-to-URL guessing."""
    if not value or re.search(r'[\x00-\x20<>\\]', value):
        return None
    try:
        url = urljoin(base, value)
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        parsed.port  # Validate malformed ports too.
        return url
    except ValueError:
        return None


def record_element(tag, attrs):
    types = attrs.get('itemtype', '').split()
    return (tag in {'article', 'li'} or attrs.get('role') in {'article', 'listitem'}
            or 'h-entry' in attrs.get('class', '').split()
            or ('itemscope' in attrs and any(t.rsplit('/', 1)[-1].endswith(('Article', 'Posting', 'Comment')) for t in types))
            or (tag in {'div', 'section'} and any(attrs.get(key) for key in ('data-id', 'data-urn'))))


class LinkParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.has_base = base, False
        self.records = [{'parts': [], 'links': [], 'structured': False}]
        self.stack = []
        self.skip = None

    def current(self):
        return self.stack[-1]['record'] if self.stack else self.records[0]

    def handle_starttag(self, tag, pairs):
        if self.skip:
            return
        if tag in {'script', 'style', 'template'}:
            self.skip = tag
            return
        attrs = dict(pairs)
        attrs = {key: value or '' for key, value in attrs.items()}
        if tag == 'base' and not self.has_base and 'href' in attrs:
            self.has_base = True
            self.base = http_url(attrs['href'], self.base) or self.base
        record = self.current()
        if record_element(tag, attrs):
            record = {'parts': [], 'links': [], 'structured': True}
            self.records.append(record)
        if tag in BLOCK:
            record['parts'].append('\n')
        link = None
        if tag in {'a', 'link'} and attrs.get('href'):
            link = {'href': attrs['href'], 'parts': [], 'bookmark': 'bookmark' in attrs.get('rel', '').split(),
                    'item_url': 'url' in attrs.get('itemprop', '').split(), 'timestamp': False,
                    'aria_label': attrs.get('aria-label', ''), 'title': attrs.get('title', ''),
                    'download': 'download' in attrs}
            record['links'].append(link)
        if tag == 'time':
            for parent in reversed(self.stack):
                if parent['record'] is not record:
                    break
                if parent['link']:
                    parent['link']['timestamp'] = True
                    break
        if tag not in VOID:
            self.stack.append({'tag': tag, 'record': record, 'link': link})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip:
                self.skip = None
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                if tag in BLOCK:
                    self.stack[index]['record']['parts'].append('\n')
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.skip:
            return
        record = self.current()
        record['parts'].append(data)
        for parent in reversed(self.stack):
            if parent['record'] is not record:
                break
            if parent['link']:
                parent['link']['parts'].append(data)
                break


def verification_links(payload, page_url, terms, patterns, excerpt_fn, width=1000, limit=5):
    """Return scoped evidence, not a claim that any destination was live-checked."""
    dom = json.loads(payload)
    parsers = []
    main = LinkParser(page_url)
    main.feed(dom.get('html', ''))
    main.close()
    parsers.append(main)
    for shadow in dom.get('shadowRoots', []):
        parser = LinkParser(main.base)
        parser.feed(shadow.get('html', ''))
        parser.close()
        parsers.append(parser)
    found, candidates = [], []
    for parser in parsers:
        for record in parser.records:
            text = ''.join(record['parts'])
            folded = ' '.join(text.casefold().split())
            if patterns and not all(p.search(folded) for p in patterns):
                continue
            for link in record['links']:
                url = http_url(link['href'], parser.base)
                if not url or link['download']:
                    continue
                label = ' '.join(''.join(link['parts']).split())
                relation = None
                if record['structured']:
                    if link['bookmark']:
                        relation = 'bookmark_in_matching_record'
                    elif link['item_url']:
                        relation = 'item_url_in_matching_record'
                    elif link['timestamp']:
                        relation = 'timestamp_in_matching_record'
                if relation is None and patterns and all(p.search(label.casefold()) for p in patterns):
                    relation = 'matching_link_text'
                if relation is None:
                    if record['structured']:
                        candidates.append({'url': url, 'label': label[:240], 'aria_label': link['aria_label'][:240],
                                           'title': link['title'][:240], 'evidence': 'href_in_matching_record',
                                           'context': excerpt_fn(text, terms, width, 1), 'live_checked': False})
                    continue
                context = text if record['structured'] else label
                kind = 'content_link' if relation == 'matching_link_text' else 'post_link'
                if urlsplit(url)._replace(fragment='') == urlsplit(page_url)._replace(fragment='') and urlsplit(url).fragment:
                    kind = 'page_fragment'
                found.append({'url': url, 'kind': kind, 'evidence': relation, 'label': label[:240],
                              'context': excerpt_fn(context, terms, width, 1), 'live_checked': False})
    order = {'bookmark_in_matching_record': 0, 'item_url_in_matching_record': 1,
             'timestamp_in_matching_record': 2, 'matching_link_text': 3}
    found.sort(key=lambda item: order[item['evidence']])
    unique = {}
    for item in found:
        unique.setdefault(item['url'], item)
    links = list(unique.values())
    related = {}
    for item in candidates:
        if item['url'] not in unique:
            related.setdefault(item['url'], item)
    return {'deep_links': links[:limit], 'links_truncated': len(links) > limit,
            'related_links': list(related.values())[:limit * 2], 'related_links_truncated': len(related) > limit * 2,
            'fallback_url': http_url(page_url, page_url),
            'fallback_reason': None if any(item['kind'] == 'post_link' for item in links[:limit]) else 'no_post_permalink_established_from_saved_dom',
            'live_checked': False}
