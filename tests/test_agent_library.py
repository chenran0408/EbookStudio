import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_mcp import PROTOCOL_VERSIONS, SERVER_VERSION, _Session
from book_library import BookLibrary
from ebook_core import Book, Chapter
from epub_io import write_epub
from studio_agent import ACTIONS, execute


class AgentLibraryTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.workspace = self.root / '桌面书库'
        env = patch.dict(os.environ, EBOOK_STUDIO_WORKSPACE=str(self.workspace))
        env.start()
        self.addCleanup(env.stop)

    def index(self, *sources):
        return execute({'action': 'library_index', **({'sources': list(map(str, sources))} if sources else {})})

    def search(self, phrase='晨光', **page):
        return execute({'action': 'library_search', 'query': phrase, **page})

    def save_issue(self):
        file = self.root / '合订本.epub'
        body = Chapter('春季', '<h2 id="甲">山岗</h2><p>晨光穿过树林。</p>'
                            '<h2 id="乙">溪流</h2><p>晨光照在河面。</p>')
        book = Book(title='季刊合订本', author='编委', chapters=[Chapter('扉页', '<p></p>'), body],
                    toc_entries=[dict(title='2025年第1期', path=body.path, fragment='', level=1),
                                 dict(title='山岗', path=body.path, fragment='甲', level=2),
                                 dict(title='溪流', path=body.path, fragment='乙', level=2)])
        write_epub(book, file)
        return file

    def test_shared_gui_catalog_paged_search_and_safe_registration(self):
        file = self.save_issue()
        self.assertEqual(self.search()['catalog_books'], 0)
        self.assertFalse(self.workspace.exists())
        # A desktop entry can predate its text index.
        self.workspace.mkdir()
        desktop = BookLibrary(self.workspace)
        desktop.add([file])
        entry, = desktop.entries()
        desktop.edit(entry.id, '我的合订本', '我的作者', ('我的标签',))
        self.assertEqual(self.search()['unavailable'][0]['state'], 'unindexed')
        new = self.root / '札记.bookproj'
        Book(title='郊野札记', chapters=[Chapter('清晨', '<p>晨光落在石阶上。</p>')]).save(new)
        result = self.index(file, new, self.root / '缺失.epub')
        self.assertEqual((result['added'], result['refreshed_metadata'], result['updated']), (1, 1, 2))
        self.assertEqual(len(result['import_errors']), 1)
        self.assertFalse(result['index_errors'])
        retained = next(book for book in desktop.entries() if book.id == entry.id)
        self.assertEqual((retained.title, retained.author, retained.tags), ('我的合订本', '我的作者', ('我的标签',)))
        first = self.search(limit=1)
        self.assertEqual((first['catalog_books'], first['searchable_books'], first['next_offset']), (2, 2, 1))
        hit, = first['hits']
        self.assertEqual((hit['source'], hit['book_title'], hit['issue'], hit['chapter'], hit['chapter_title']),
                         (str(file), '我的合订本', '2025年第1期', 2, '山岗'))
        self.assertEqual(hit['fragment'], '甲')
        self.assertIn('晨光穿过树林', hit['snippet'])
        self.assertTrue(hit['chapter_path'].endswith('.xhtml'))
        chapter = execute({'action': 'inspect', 'source': hit['source'], 'chapter': hit['chapter']})
        self.assertIn('晨光穿过树林', chapter['selected_chapter']['text'])
        second = self.search(offset=first['next_offset'], limit=2)
        self.assertEqual([(row['issue'], row['fragment']) for row in second['hits']],
                         [('2025年第1期', '乙'), ('', '')])
        self.assertIsNone(second['next_offset'])
        self.assertEqual(self.index()['reused'], 2)
        self.assertFalse(self.search('不同的句子')['hits'])

    def test_old_index_stale_and_missing_sources_are_actionable(self):
        file = self.root / '编辑稿.bookproj'
        book = Book(chapters=[Chapter('早晨', '<p>晨光洒在石桥上。</p>')])
        book.save(file)
        self.index(file)
        desktop = BookLibrary(self.workspace)
        with desktop._connect() as db:
            db.execute('ALTER TABLE text_chapters DROP COLUMN chapter_number')
        legacy = self.search()
        self.assertFalse(legacy['hits'])
        self.assertEqual(legacy['unavailable'][0]['state'], 'unindexed')
        self.assertEqual(self.index()['updated'], 1)
        self.assertEqual(self.search()['hits'][0]['chapter'], 1)
        book.chapters[0].html = '<p>雨滴在屋檐下汇成水流。</p>'
        book.save(file)
        stale = self.search()
        self.assertFalse(stale['hits'])
        self.assertEqual(stale['unavailable'][0]['state'], 'stale')
        self.index()
        self.assertIn('雨滴', self.search('雨滴')['hits'][0]['snippet'])
        file.rename(self.root / '挪动后.bookproj')
        missing = self.search('雨滴')
        self.assertEqual(missing['searchable_books'], 0)
        self.assertEqual(missing['unavailable'][0]['state'], 'missing')
        self.assertFalse(missing['hits'])
        self.assertTrue(self.index()['index_errors'])
        self.assertEqual(len(desktop.entries()), 1)

    def test_mcp_search_response_in_all_protocol_versions(self):
        file = self.save_issue()
        for version in PROTOCOL_VERSIONS:
            with self.subTest(version=version):
                session = _Session(execute, ACTIONS)
                negotiated = session.request('initialize', {'protocolVersion': version})
                self.assertEqual(negotiated['serverInfo']['version'], SERVER_VERSION)
                self.assertIn('ebook_library_search', negotiated['instructions'])
                session.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
                register = session.request('tools/call', {'name': 'ebook_library_index',
                                                           'arguments': {'sources': [str(file)]}})
                self.assertFalse(register['isError'])
                response = session.request('tools/call', {'name': 'ebook_library_search',
                                                           'arguments': {'query': '晨光'}})
                result = json.loads(response['content'][0]['text'])
                self.assertEqual(result['searchable_books'], 1)
                self.assertEqual(len(result['hits']), 2)
                if version != '2024-11-05':
                    self.assertEqual(response['structuredContent'], result)
                else:
                    self.assertNotIn('structuredContent', response)
                invalid = session.request('tools/call', {'name': 'ebook_library_search',
                                                          'arguments': {'query': '晨光', 'limit': 0}})
                self.assertTrue(invalid['isError'])
