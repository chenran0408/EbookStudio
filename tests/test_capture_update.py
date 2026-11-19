from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import capture
from capture_update import CaptureProfile, WebUpdate, apply_updates, collect_updates, profiles_for, remember_profile
from ebook_core import Asset, Book, Chapter, resolve_href
from epub_io import write_epub
from lxml import html
from workspace import Workspace


SITE = 'https://example.com'


def page(text):
    return f'<html><head><title>原始书页</title></head><body><div id="maintext"><p>{text}</p></div></body></html>'


class CaptureUpdateTests(unittest.TestCase):
    def test_directory_detects_updates_without_overwriting_local_edits(self):
        old_pages = {SITE + '/a': page('初稿'), SITE + '/b': page('未改动')}
        def load_old(url, _rendered):
            return old_pages[url], url
        with patch.object(capture, '_load_page', side_effect=load_old):
            first = capture.capture(SITE + '/a', article_xpath='//*[@id="maintext"]')[0]
            same = capture.capture(SITE + '/b', article_xpath='//*[@id="maintext"]')[0]
        first.html = '<p>手工校对后的版本。</p>'
        first.level = 2
        book = Book(chapters=[first, same])
        preset = CaptureProfile([SITE + '/a', SITE + '/b', SITE + '/error'], directory=SITE + '/index',
                                skipped=[SITE + '/skip'], article_xpath='//*[@id="maintext"]',
                                directory_xpath='//*[@id="chosen"]')
        current_pages = {
            SITE + '/index': '<nav><a href="/ignore">外围</a></nav><div id="chosen">'
                            '<a href="/a">原来</a><a href="/b">已有</a><a href="/skip">主动跳过</a>'
                            '<a href="/c">新篇</a></div>',
            SITE + '/a': page('网站更新'), SITE + '/b': page('未改动'), SITE + '/c': page('新增正文'),
        }
        def load_current(url, _rendered):
            if url == SITE + '/error':
                raise capture.CaptureError('暂时不可用')
            return current_pages[url], url
        with patch.object(capture, '_load_page', side_effect=load_current):
            updates, notice = collect_updates(book, preset)
        self.assertEqual(notice, '')
        self.assertEqual([entry.state for entry in updates], ['changed', 'same', 'failed', 'new'])
        self.assertTrue(updates[0].locally_edited)
        self.assertEqual(updates[2].notices, ('暂时不可用',))
        updated, index = apply_updates(book, [updates[-1]], preset)
        self.assertEqual(index, 2)
        self.assertIn('手工校对', updated.chapters[0].html)
        self.assertIn('新增正文', updated.chapters[2].html)
        self.assertEqual(len(book.chapters), 2)
        explicitly_updated, index = apply_updates(updated, [updates[0]], preset)
        self.assertEqual(index, 0)
        self.assertEqual(explicitly_updated.chapters[0].path, first.path)
        self.assertEqual(explicitly_updated.chapters[0].level, 2)
        self.assertIn('网站更新', explicitly_updated.chapters[0].html)

    def test_asset_conflict_is_relocated_instead_of_overwriting_shared_image(self):
        old = Chapter('旧书页', '<p><img src="images/a.png"/></p>', path='book/a.xhtml')
        book = Book(chapters=[old], assets={'book/images/a.png': Asset(b'local', 'image/png')})
        incoming = Chapter('新书页', old.html, source=SITE + '/a', web={'url': SITE + '/a', 'remote': 'remote'})
        update = WebUpdate(SITE + '/a', 'changed', incoming, {'images/a.png': Asset(b'remote', 'image/png')}, 0)
        result, _ = apply_updates(book, [update], CaptureProfile([SITE + '/a']))
        chapter = result.chapters[0]
        address = html.fromstring(chapter.html).xpath('.//img/@src')[0]
        self.assertEqual(result.assets['book/images/a.png'].data, b'local')
        self.assertEqual(result.assets[resolve_href(chapter.path, address)].data, b'remote')
        self.assertEqual(chapter.web['local'], capture.web_revision(chapter))

    def test_explicit_update_preserves_generated_toc_and_existing_anchor_links(self):
        old = Chapter('旧版', '<h2 id="introduction">开头</h2><p id="download">阅读原文</p>', path='a.xhtml')
        unchanged = Chapter('其他章', '<p><a href="a.xhtml#download">引用原文</a></p>', path='b.xhtml')
        book = Book(chapters=[old, unchanged], toc_entries=[
            {'title': '开头', 'path': 'a.xhtml', 'fragment': 'introduction', 'level': 1},
            {'title': '阅读原文', 'path': 'a.xhtml', 'fragment': 'download', 'level': 2},
        ])
        incoming = Chapter('新版', '<div><h2>开头</h2><p>新文章正文。</p></div>', source=SITE + '/a',
                           web={'url': SITE + '/a', 'remote': 'remote'})
        row = WebUpdate(SITE + '/a', 'changed', incoming, chapter_index=0)
        edited, _ = apply_updates(book, [row], CaptureProfile([row.url]))
        root = html.fromstring(edited.chapters[0].html)
        self.assertEqual(root.xpath('.//h2/@id'), ['introduction'])
        self.assertEqual(root.xpath('.//span/@id'), ['download'])
        self.assertEqual(edited.chapters[1].html, unchanged.html)
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'updated.epub'
            write_epub(edited, path)
            with ZipFile(path) as epub:
                self.assertIn(b'a.xhtml#download', epub.read('nav.xhtml'))

    def test_rules_and_chapter_origin_round_trip(self):
        scan = {'path': 'scan.pdf', 'page': 4, 'engine': 'paddle'}
        web = {'url': SITE + '/article', 'remote': 'original', 'local': 'applied'}
        book = Book(chapters=[Chapter('来源', scan=scan, web=web)])
        profile = CaptureProfile([SITE + '/article'], directory=SITE, article_xpath='//article')
        remember_profile(book, profile)
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'book.bookproj'
            book.save(path)
            written = Book.load(path)
        self.assertEqual(profiles_for(written), [profile])
        self.assertEqual(written.chapters[0].scan, scan)
        self.assertEqual(written.chapters[0].web, web)

    def test_checkpoint_restores_scan_rule_and_anchored_toc_together(self):
        original = Chapter('第一章', '<h2 id="intro">开头</h2>', path='intro.xhtml',
                           scan={'path': 'scan.pdf', 'page': 1, 'engine': 'xiaomi'},
                           web={'url': SITE + '/a', 'remote': 'before', 'local': 'before'})
        book = Book(chapters=[original], toc_entries=[
            {'title': '开头', 'path': 'intro.xhtml', 'fragment': 'intro', 'level': 1},
        ])
        profile = CaptureProfile([SITE + '/a'])
        remember_profile(book, profile)
        with TemporaryDirectory() as temp:
            workspace = Workspace(temp)
            checkpoint = workspace.save_checkpoint(book, '修改前')
            book.chapters.clear()
            book.capture_profiles.clear()
            book.toc_entries = None
            restored = workspace.load_checkpoint(book, checkpoint)
            workspace.close()
        self.assertEqual(restored.chapters, [original])
        self.assertEqual(profiles_for(restored), [profile])
        self.assertEqual(restored.toc_entries[0]['fragment'], 'intro')

    def test_bad_article_rule_is_reported(self):
        with self.assertRaisesRegex(capture.CaptureError, '正文 XPath 无效'):
            capture.capture_html(page('本地'), SITE + '/a', article_xpath='//[')
