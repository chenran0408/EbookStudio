"""Create an ordinary, readable spine page from the current EPUB navigation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath

from lxml import etree
from PySide6.QtWidgets import QMessageBox

from ebook_core import Book, Chapter, resolve_href
from epub_io import (
    EPUB, OPF, XHTML, _manifest, _unique, _xml, append_toc_list, current_toc,
    editable_chapter_document, is_fixed_layout, toc_tree,
)


@dataclass
class InlineTocEdit:
    book: Book
    index: int
    count: int
    created: bool
    changed: bool


def _nav_in_reading_order(book: Book) -> bool:
    if not book._epub_path:
        return False
    package = _xml(book._epub_members[book._epub_path])
    nav_ids = {identifier for identifier, (_, item) in _manifest(package, book._epub_path).items()
               if 'nav' in item.get('properties', '').split()}
    return any(ref.get('idref') in nav_ids and ref.get('linear', 'yes') != 'no'
               for ref in package.findall(f'{{{OPF}}}spine/{{{OPF}}}itemref'))


def _after_cover(book: Book) -> int:
    first = book.chapters[0]
    body = editable_chapter_document(book, first).find(f'{{{XHTML}}}body')
    if any('cover' in node.get(f'{{{EPUB}}}type', '').split() for node in body.iter()):
        return 1
    if book.cover and not ''.join(body.itertext()).strip():
        for image in body.iter(f'{{{XHTML}}}img'):
            if resolve_href(first.path, image.get('src', '')) == book.cover:
                return 1
    return 0


def _same_page(book, chapter, nav):
    body = editable_chapter_document(book, chapter).find(f'{{{XHTML}}}body')
    if (len(body) == 1 and body[0].tag == f'{{{XHTML}}}main' and not body[0].attrib
            and not (body.text or '').strip() and not (body[0].tail or '').strip()):
        body = body[0]  # New chapters are wrapped in main when first exported.
    return (len(body) == 1 and not (body.text or '').strip() and not (body[0].tail or '').strip()
            and etree.tostring(body[0], method='c14n', exclusive=True)
            == etree.tostring(nav, method='c14n', exclusive=True))


def prepare_inline_toc(book: Book) -> InlineTocEdit:
    if not book.chapters:
        raise ValueError('请先添加章节，再生成书内目录页。')
    if is_fixed_layout(book):
        raise ValueError('这本书使用固定版式。可在「目录和章节管理」中编辑阅读器目录，原有页面会保留。')
    if _nav_in_reading_order(book):
        raise ValueError('原书的阅读器目录已经是可翻阅的正文页。可在「目录和章节管理」修改条目，导出时会同步更新。')

    index = next((i for i, ch in enumerate(book.chapters) if ch.path == book.inline_toc_path), None)
    created = index is None
    if created:
        index = _after_cover(book)
        candidate = PurePosixPath(book.chapters[0].path).with_name('contents.xhtml').as_posix()
        occupied = {chapter.path for chapter in book.chapters} | book.assets.keys() | book._epub_members.keys()
        path = _unique(candidate, occupied)
    else:
        path = book.chapters[index].path
    entries = [entry for entry in current_toc(book) if entry['path'] != path]
    if not entries:
        raise ValueError('目录中没有可链接的正文，请先添加章节或目录条目。')

    nav = etree.Element(f'{{{XHTML}}}nav', nsmap={None: XHTML}, attrib={'aria-label': '目录'})
    etree.SubElement(nav, f'{{{XHTML}}}h1', id='contents-heading').text = '书内目录'
    append_toc_list(nav, path, toc_tree(entries))
    if not created and _same_page(book, book.chapters[index], nav):
        return InlineTocEdit(book, index, len(entries), False, False)

    revised = deepcopy(book)
    if created:
        if book._epub_path and book.toc_entries is None:
            # Inserting a spine page normally rebuilds the navigation from chapter titles.
            # Retain the imported navigation's sections and auxiliary-document links.
            revised.toc_entries = entries
        revised.chapters.insert(index, Chapter('书内目录', path=path))
    revised.inline_toc_path = path
    revised.chapters[index].html = etree.tostring(nav, encoding='unicode')
    return InlineTocEdit(revised, index, len(entries), created, True)


def show_inline_toc(studio):
    if not studio.book_started or studio.task is not None:
        return
    studio.flush_current_edits(lambda: _confirm_inline_toc(studio))


def _confirm_inline_toc(studio):
    original = studio.book
    try:
        edit = prepare_inline_toc(original)
    except (ValueError, etree.LxmlError) as exc:
        studio.error(str(exc))
        return
    if not edit.changed:
        studio.go_to_chapter(edit.index)
        studio.statusBar().showMessage('书内目录页已是最新。')
        return

    action = '生成目录页' if edit.created else '更新目录页'
    position = '封面之后' if edit.index else '书稿开头'
    text = (f'将在{position}插入「书内目录」，包含 {edit.count} 个跳转条目。' if edit.created else
            f'将用当前目录的 {edit.count} 个跳转条目替换已生成目录页的正文。')
    dialog = QMessageBox(QMessageBox.Icon.Question, '书内目录页', text, parent=studio)
    dialog.setInformativeText('应用前会保存书稿检查点。')
    confirm = dialog.addButton(action, QMessageBox.ButtonRole.AcceptRole)
    cancel = dialog.addButton('取消', QMessageBox.ButtonRole.RejectRole)
    dialog.setEscapeButton(cancel)
    dialog.setDefaultButton(confirm)
    dialog.exec()
    accepted = dialog.clickedButton() is confirm
    dialog.deleteLater()
    if not accepted:
        return
    revision = studio.edit_revision

    def apply(_checkpoint):
        if studio.book is not original or studio.edit_revision != revision:
            studio.error('书稿已更改，请重新生成目录页。')
            return
        studio.adopt_book_edit(edit.book, edit.index)
        studio.go_to_chapter(edit.index)
        studio.statusBar().showMessage(f'书内目录页已应用，包含 {edit.count} 个条目。')

    studio.start_task('正在保存修改前检查点…',
                      lambda: studio.workspace.save_checkpoint(original, '书内目录页修改前'), apply)
