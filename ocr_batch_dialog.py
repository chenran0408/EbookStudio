"""Discover, monitor, and resume locally checkpointed OCR jobs."""
from threading import Event

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from ocr_batch import ENGINE_NAMES


PAGE_LABELS = {"pending": "待识别", "running": "识别中", "done": "已完成", "failed": "失败"}


class BatchTask(QThread):
    returned = Signal(object)
    failed = Signal(str)
    page_changed = Signal(object)

    def __init__(self, parent, action):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            result = self.action(self.page_changed.emit)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.returned.emit(result)


class OcrBatchDialog(QDialog):
    def __init__(self, parent, store, *, new=None):
        super().__init__(parent)
        self.store = store
        self.task = None
        self.batch = None
        self.book = None
        self.states = {}
        self.rows = {}
        self.pause_requested = Event()
        self.running = False
        self.close_after_task = False
        self.setWindowTitle("OCR 批次任务")
        self.resize(690, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 15)
        layout.setSpacing(9)
        jobs = QHBoxLayout()
        label = QLabel("扫描批次", objectName="section")
        self.jobs = QComboBox()
        self.jobs.setAccessibleName("扫描批次")
        self.jobs.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.jobs.setMinimumContentsLength(18)
        label.setBuddy(self.jobs)
        self.relocate = QPushButton("定位原件…")
        jobs.addWidget(label)
        jobs.addWidget(self.jobs, 1)
        jobs.addWidget(self.relocate)
        layout.addLayout(jobs)

        self.summary = QLabel(objectName="muted")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("已保存页数")
        self.progress.setFormat("%v / %m 页")
        layout.addWidget(self.progress)

        self.pages = QTreeWidget()
        self.pages.setAccessibleName("扫描页及识别状态")
        self.pages.setHeaderLabels(["页码", "状态", "问题摘要"])
        self.pages.setRootIsDecorated(False)
        self.pages.setUniformRowHeights(True)
        self.pages.setAllColumnsShowFocus(True)
        header = self.pages.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.pages, 1)

        self.details = QPlainTextEdit(readOnly=True)
        self.details.setAccessibleName("所选页的失败详情")
        self.details.setMaximumHeight(84)
        layout.addWidget(self.details)
        self.notice = QLabel(objectName="muted")
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

        actions = QHBoxLayout()
        self.resume = QPushButton("继续识别")
        self.retry = QPushButton("重试失败页")
        self.retry_page = QPushButton("重试所选页")
        self.open_book = QPushButton("打开书稿", objectName="primary")
        self.close_button = QPushButton("关闭")
        for button in (self.resume, self.retry, self.retry_page):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.open_book)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)

        self.jobs.currentIndexChanged.connect(self.show_job)
        self.pages.currentItemChanged.connect(self.show_page)
        self.resume.clicked.connect(self.resume_or_pause)
        self.retry.clicked.connect(lambda: self.run_batch(retry=True))
        self.retry_page.clicked.connect(lambda: self.run_batch(retry=True, page=self.selected_page()))
        self.open_book.clicked.connect(self.open_result)
        self.close_button.clicked.connect(self.reject)
        self.relocate.clicked.connect(self.relocate_source)
        self.load_jobs()
        self.jobs.setFocus()
        if new:
            QTimer.singleShot(0, lambda: self.create_batch(*new))

    def load_jobs(self, selected=None):
        batches = self.store.batches()
        with QSignalBlocker(self.jobs):
            self.jobs.clear()
            index = 0
            for row, batch in enumerate(batches):
                self.jobs.addItem(f"{batch.source.name} · {ENGINE_NAMES[batch.engine]}", batch)
                self.jobs.setItemData(row, str(batch.source), Qt.ItemDataRole.ToolTipRole)
                if batch.directory == selected:
                    index = row
            if batches:
                self.jobs.setCurrentIndex(index)
        self.show_job()

    def show_job(self, _index=None):
        self.notice.clear()
        self.batch = self.jobs.currentData()
        self.reload_pages()

    def reload_pages(self):
        selected = self.selected_page()
        self.states.clear()
        self.rows.clear()
        with QSignalBlocker(self.pages):
            self.pages.clear()
            if self.batch:
                for state in self.batch.states():
                    row = QTreeWidgetItem([str(state.number), "", ""])
                    row.setData(0, Qt.ItemDataRole.UserRole, state.number)
                    self.pages.addTopLevelItem(row)
                    self.rows[state.number] = row
                    self.update_row(state)
                if self.rows:
                    self.pages.setCurrentItem(self.rows.get(selected, next(iter(self.rows.values()))))
        self.show_page()

    def selected_page(self):
        item = self.pages.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def update_row(self, state):
        self.states[state.number] = state
        row = self.rows[state.number]
        row.setText(1, PAGE_LABELS[state.status])
        row.setText(2, state.error.partition("\n")[0])
        row.setToolTip(2, state.error)

    @Slot(object)
    def page_changed(self, state):
        self.update_row(state)
        self.show_page()

    def show_page(self, *_):
        state = self.states.get(self.selected_page())
        detail = state.error if state else ""
        self.details.setVisible(bool(detail))
        self.details.setPlainText(detail)
        self.sync_controls()

    def sync_controls(self):
        state = self.states.get(self.selected_page())
        counts = {status: 0 for status in PAGE_LABELS}
        for page in self.states.values():
            counts[page.status] += 1
        busy = self.task is not None
        self.progress.setRange(0, max(len(self.states), 1))
        self.progress.setValue(counts["done"])
        self.progress.setVisible(self.batch is not None)
        if self.batch:
            idle = counts["pending"] + counts["running"]
            engine = self.batch.model
            self.summary.setText(f"{engine} · {counts['done']} 页已完成 · {idle} 页待处理 · {counts['failed']} 页失败")
        else:
            self.summary.setText("扫描件 OCR 创建的批次会保存在这里，可继续处理未完成的页。")
        self.jobs.setEnabled(not busy and self.jobs.count() > 0)
        self.relocate.setEnabled(bool(self.batch) and not busy)
        self.resume.setText("暂停" if self.running else "继续识别")
        self.resume.setEnabled((self.running and not self.pause_requested.is_set()) or
                               (not busy and counts["pending"] > 0))
        self.retry.setEnabled(not busy and counts["failed"] > 0)
        self.retry_page.setEnabled(not busy and state is not None and state.status == "failed")
        self.open_book.setEnabled(not busy and counts["done"] > 0)
        self.open_book.setText("打开书稿" if counts["done"] == len(self.states) else "打开已识别书稿")

    def start_task(self, action, complete, text, *, running=False):
        if self.task:
            return
        self.response = None
        self.error = ""
        self.complete = complete
        self.running = running
        self.pause_requested.clear()
        self.notice.setText(text)
        self.task = BatchTask(self, action)
        self.task.page_changed.connect(self.page_changed)
        self.task.returned.connect(self.save_response)
        self.task.failed.connect(self.save_error)
        self.task.finished.connect(self.finish_task)
        self.sync_controls()
        self.task.start()

    @Slot(object)
    def save_response(self, result):
        self.response = result

    @Slot(str)
    def save_error(self, error):
        self.error = error

    @Slot()
    def finish_task(self):
        self.task.deleteLater()
        self.task = None
        self.running = False
        self.reload_pages()
        if self.close_after_task:
            super().reject()
        elif self.error:
            self.notice.setText(self.error)
        else:
            self.complete(self.response)

    def create_batch(self, source, pages, engine):
        def created(batch):
            self.load_jobs(batch.directory)
            self.run_batch()

        self.start_task(lambda _: self.store.create(source, pages, engine), created, "正在核对扫描原件…")

    def run_batch(self, *, retry=False, page=None):
        if self.batch is None:
            return
        batch = self.batch

        def completed(_):
            if self.pause_requested.is_set():
                self.notice.setText("已暂停。已完成的页面已保存，可以稍后继续。")
            elif any(state.status == "failed" for state in self.states.values()):
                self.notice.setText("可查看失败详情、重试失败页或打开已识别的书稿。")
            else:
                self.notice.setText("页面已保存，可以打开书稿继续校对。")

        self.start_task(lambda notify: batch.run(stop=self.pause_requested.is_set, notify=notify,
                                                 retry=retry, page=page), completed,
                        "正在启动识别。每完成一页即保存。", running=True)

    def resume_or_pause(self):
        if self.running:
            self.pause_requested.set()
            self.notice.setText("等当前页处理完毕后暂停…")
            self.sync_controls()
        else:
            self.run_batch()

    def relocate_source(self):
        file, _ = QFileDialog.getOpenFileName(self, "重新定位扫描原件", str(self.batch.source))
        if not file:
            return
        batch = self.batch

        def relocated(updated):
            self.load_jobs(updated.directory)
            self.notice.setText("原件已定位，可继续识别。")

        self.start_task(lambda _: batch.relocate(file), relocated, "正在验证文件内容…")

    def open_result(self):
        batch = self.batch

        def opened(book):
            self.book = book
            self.accept()

        self.start_task(lambda _: batch.build_book(), opened, "正在合并已识别的页…")

    def reject(self):
        if self.task is not None:
            self.close_after_task = True
            if self.running:
                self.resume_or_pause()
            return
        super().reject()
