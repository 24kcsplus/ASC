import json
from dataclasses import asdict
import os
from pathlib import Path
import re
import signal
import sys

from PySide6.QtCore import Qt, QProcess, QSettings, QTimer, QSortFilterProxyModel, QEvent
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QStandardItem, QStandardItemModel, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QToolBar, QDockWidget, QTreeView, QLineEdit, QTabWidget,
    QPlainTextEdit, QTableView, QHeaderView, QComboBox, QCheckBox, QProgressBar,
    QStackedWidget, QFileDialog, QListWidget, QListWidgetItem, QAbstractItemView,
    QFrame, QSizePolicy, QInputDialog, QMenu, QDialog, QScrollArea)

from .editor import CodeEditor
from .models import ResultModel
from .xref import infer_reference, python_offset, scoped_reference
from .navigation import ClassLookup, navigation_query, declarations, matching_declarations
from .variables import find_variable_definition
from .theme import apply_theme

ROOT = Path(__file__).resolve().parent.parent
ROLE = Qt.UserRole + 1
KINDS = [('字符串引用', 'string refs'), ('类型引用', 'type refs'),
         ('方法引用', 'method refs'), ('字段引用', 'field refs'),
         ('查找方法', 'method'), ('查找字段', 'field')]


def layout_widget(vertical=True, margins=0, spacing=8):
    widget = QWidget()
    layout = QVBoxLayout(widget) if vertical else QHBoxLayout(widget)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    return widget, layout


def badge(text, color):
    pix = QPixmap(20, 20)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    bg = QColor(color)
    bg.setAlpha(30)
    p.setBrush(bg)
    p.drawRoundedRect(1, 1, 18, 18, 4, 4)
    p.setPen(QColor(color))
    p.setFont(QFont('DejaVu Sans', 9, QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, text)
    p.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings or QSettings('ASC', 'Workbench')
        self.dark = self.settings.value('dark', True, type=bool)
        self.theme = apply_theme(QApplication.instance(), self.dark)
        self.font_size = self.settings.value('font_size', 12, type=int)
        self.setWindowTitle('ASC Workbench')
        self.setWindowIcon(badge('A', '#78a6ff'))
        self.resize(1440, 940)
        self.setMinimumSize(1000, 680)
        self.setAcceptDrops(True)
        self.proc = None
        self.buffer = b''
        self.busy = False
        self.loaded = False
        self.path = None
        self.classes = []
        self.class_lookup = ClassLookup([])
        self.packages = {}
        self.documents = {}
        self.history = []
        self.history_index = -1
        self.navigating = False
        self.pending_line = None
        self.pending_source_range = None
        self.pending_request = None
        self.active_page = None
        self.pending_origin = None
        self.pending_definition = None
        self.definition_origin = None
        self.definition_dialog = None
        self._build_actions()
        self._build_center()
        self._build_project()
        self._build_outline()
        self._build_results()
        self._build_toolbar()
        self._build_menus()
        self._build_status()
        self._update_enabled()
        self.tree_timer = QTimer(self)
        self.tree_timer.setSingleShot(True)
        self.tree_timer.setInterval(200)
        self.class_filter.textChanged.connect(lambda: self.tree_timer.start())
        self.tree_timer.timeout.connect(self._filter_tree)
        geometry = self.settings.value('geometry')
        state = self.settings.value('state')
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)

    def action(self, label, callback, shortcut=None):
        a = QAction(label, self)
        a.triggered.connect(callback)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        self.addAction(a)
        return a

    def _build_actions(self):
        self.open_action = self.action('打开 APK…', self.choose_apk, 'Ctrl+O')
        self.home_action = self.action('起始页 / 最近打开', self.show_home)
        self.back_action = self.action('←  后退', lambda: self.navigate(-1), 'Alt+Left')
        self.forward_action = self.action('前进  →', lambda: self.navigate(1), 'Alt+Right')
        self.search_action = self.action('搜索引用', self.focus_search, 'Ctrl+Shift+F')
        self.class_action = self.action('搜索类', self.focus_classes, 'Ctrl+N')
        self.find_action = self.action('文件内查找', self.show_find, 'Ctrl+F')
        self.goto_action = self.action('跳转到行…', self.goto_line, 'Ctrl+G')
        self.export_action = self.action('导出当前源码…', self.export_source, 'Ctrl+Shift+S')
        self.theme_action = self.action('切换深色 / 浅色', self.toggle_theme)
        self.close_tab_action = self.action('关闭标签', lambda: self.close_tab(self.tabs.currentIndex()), 'Ctrl+W')
        self.escape_action = self.action('返回上一次访问 / 关闭查找栏', self.escape_back, 'Escape')
        self.escape_action.setAutoRepeat(False)
        self.action('增大代码字体', lambda: self.zoom(1), 'Ctrl+=')
        self.action('减小代码字体', lambda: self.zoom(-1), 'Ctrl+-')
        self.action('下一个标签', lambda: self.cycle_tab(1), 'Ctrl+Tab')
        self.action('上一个标签', lambda: self.cycle_tab(-1), 'Ctrl+Shift+Tab')

    def _build_toolbar(self):
        toolbar = QToolBar('主工具栏', self)
        toolbar.setObjectName('mainToolbar')
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        brand = QLabel(' ASC / WORKBENCH  ')
        brand.setObjectName('brand')
        toolbar.addWidget(brand)
        toolbar.addSeparator()
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.home_action)
        toolbar.addSeparator()
        toolbar.addAction(self.back_action)
        toolbar.addAction(self.forward_action)
        toolbar.addSeparator()
        toolbar.addAction(self.class_action)
        toolbar.addAction(self.search_action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        self.apk_label = QLabel('未打开 APK')
        self.apk_label.setObjectName('muted')
        toolbar.addWidget(self.apk_label)
        toolbar.addSeparator()
        toolbar.addAction(self.theme_action)

    def _build_menus(self):
        file = self.menuBar().addMenu('文件')
        file.addAction(self.open_action)
        file.addAction(self.home_action)
        self.recent_menu = file.addMenu('最近打开')
        self._refresh_recent()
        file.addAction(self.export_action)
        file.addAction(self.close_tab_action)
        file.addSeparator()
        file.addAction(self.action('退出', self.close, 'Ctrl+Q'))
        view = self.menuBar().addMenu('视图')
        for dock in (self.project_dock, self.outline_dock, self.results_dock):
            view.addAction(dock.toggleViewAction())
        view.addSeparator()
        view.addAction(self.theme_action)
        view.addAction(self.action('恢复默认布局', self.reset_layout))
        nav = self.menuBar().addMenu('导航')
        for a in (self.back_action, self.forward_action, self.class_action, self.goto_action):
            nav.addAction(a)
        search = self.menuBar().addMenu('搜索')
        search.addAction(self.find_action)
        search.addAction(self.search_action)
        help_menu = self.menuBar().addMenu('帮助')
        help_menu.addAction(self.action('使用说明', self.show_help))

    def _build_center(self):
        central, layout = layout_widget()
        self.stack = QStackedWidget()
        welcome_content, welcome_layout = layout_widget(margins=24, spacing=10)
        self.welcome = QScrollArea()
        self.welcome.setWidgetResizable(True)
        self.welcome.setFrameShape(QFrame.NoFrame)
        self.welcome.setWidget(welcome_content)
        welcome_layout.addStretch(2)
        eyebrow = QLabel('ANDROID SOURCE EXPLORER')
        eyebrow.setObjectName('brand')
        welcome_layout.addWidget(eyebrow)
        hero = QLabel('从一个 APK，开始探索。')
        hero.setObjectName('hero')
        welcome_layout.addWidget(hero)
        subtitle = QLabel('浏览类与方法，按需反编译，追踪代码引用。\n将 APK 拖入窗口，或选择本地文件。')
        subtitle.setObjectName('muted')
        subtitle.setWordWrap(True)
        welcome_layout.addWidget(subtitle)
        open_button = QPushButton('打开 APK     Ctrl+O')
        open_button.setObjectName('primary')
        open_button.setFixedWidth(210)
        open_button.clicked.connect(self.choose_apk)
        open_row, open_layout = layout_widget(False)
        open_layout.addWidget(open_button)
        self.resume_button = QPushButton('返回代码')
        self.resume_button.clicked.connect(self.resume_workspace)
        open_layout.addWidget(self.resume_button)
        open_layout.addStretch()
        welcome_layout.addWidget(open_row)
        recent_header, recent_header_layout = layout_widget(False)
        label = QLabel('最近打开')
        label.setObjectName('brand')
        recent_header_layout.addWidget(label, 1)
        clear_button = QPushButton('清空记录')
        clear_button.clicked.connect(self.clear_recent)
        recent_header_layout.addWidget(clear_button)
        welcome_layout.addWidget(recent_header)
        self.recent_list = QListWidget()
        self.recent_list.setMinimumHeight(120)
        self.recent_list.setMaximumHeight(156)
        self.recent_list.setStyleSheet('QListWidget::item { padding: 9px 12px; }')
        self.recent_list.itemActivated.connect(self._open_recent_item)
        self.recent_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self._recent_context_menu)
        welcome_layout.addWidget(self.recent_list)
        self.recent_hint = QLabel('暂无历史记录，打开 APK 后会自动保存。')
        self.recent_hint.setObjectName('muted')
        welcome_layout.addWidget(self.recent_hint)
        shortcuts = QLabel('双击符号 · 跳转定义     X · 交叉引用     Alt+← / → · 返回与前进')
        shortcuts.setObjectName('muted')
        shortcuts.setWordWrap(True)
        welcome_layout.addWidget(shortcuts)
        self.welcome_status = QLabel('ASC ENGINE  /  Qt 桌面工作区  /  v0.2')
        self.welcome_status.setWordWrap(True)
        self.welcome_status.setObjectName('muted')
        welcome_layout.addWidget(self.welcome_status)
        welcome_layout.addStretch(3)
        self.stack.addWidget(self.welcome)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBar().installEventFilter(self)
        self.stack.addWidget(self.tabs)
        self.find_bar, find_layout = layout_widget(False, 8)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText('在当前文件中查找…')
        self.find_input.returnPressed.connect(lambda: self.find_text(False))
        find_layout.addWidget(self.find_input, 1)
        self.find_status = QLabel('')
        find_layout.addWidget(self.find_status)
        for text, callback in [('上一个', lambda: self.find_text(True)), ('下一个', lambda: self.find_text(False)), ('关闭', self.hide_find)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            find_layout.addWidget(button)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.find_bar)
        self.find_bar.hide()
        self.setCentralWidget(central)

    def _dock(self, title, name, widget, area):
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.addDockWidget(area, dock)
        return dock

    def _build_project(self):
        widget, layout = layout_widget()
        wrapper, wrapper_layout = layout_widget(margins=10)
        self.class_filter = QLineEdit()
        self.class_filter.setPlaceholderText('筛选包 / 类名    Ctrl+N')
        self.class_filter.returnPressed.connect(self._open_first_class)
        wrapper_layout.addWidget(self.class_filter)
        layout.addWidget(wrapper)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setAnimated(False)
        self.tree_model = QStandardItemModel(self)
        self.tree.setModel(self.tree_model)
        self.tree.expanded.connect(self._expand)
        self.tree.doubleClicked.connect(self._tree_open)
        self.tree.activated.connect(self._tree_open)
        layout.addWidget(self.tree, 1)
        self.class_count = QLabel('  尚未加载项目')
        self.class_count.setObjectName('muted')
        self.class_count.setContentsMargins(0, 7, 0, 10)
        layout.addWidget(self.class_count)
        self.project_dock = self._dock('项目', 'project', widget, Qt.LeftDockWidgetArea)
        self.project_dock.setMinimumWidth(210)

    def _build_outline(self):
        widget, layout = layout_widget()
        hint = QLabel('  当前文件 · 方法导航')
        hint.setObjectName('muted')
        hint.setContentsMargins(0, 8, 0, 5)
        layout.addWidget(hint)
        self.outline = QListWidget()
        self.outline.itemActivated.connect(self._outline_jump)
        self.outline.itemClicked.connect(self._outline_jump)
        layout.addWidget(self.outline)
        note = QLabel('  点击方法跳转 · Alt+← 返回原位置')
        note.setObjectName('muted')
        note.setWordWrap(True)
        note.setContentsMargins(0, 8, 6, 8)
        layout.addWidget(note)
        self.outline_dock = self._dock('结构', 'outline', widget, Qt.RightDockWidgetArea)
        self.outline_dock.setMinimumWidth(180)

    def _build_results(self):
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setDocumentMode(True)
        self.bottom_tabs.tabBar().setDrawBase(False)
        search, layout = layout_widget()
        controls, row = layout_widget(False, 10)
        self.search_kind = QComboBox()
        for label, key in KINDS:
            self.search_kind.addItem(label, key)
        row.addWidget(self.search_kind)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('搜索字符串、类型、方法或字段…')
        self.search_input.returnPressed.connect(self.start_search)
        row.addWidget(self.search_input, 2)
        self.scope_input = QLineEdit()
        self.scope_input.setPlaceholderText('限定类（可选）')
        self.scope_input.setToolTip('仅用于方法 / 字段引用；支持完整类名，勾选后可模糊匹配')
        self.scope_input.returnPressed.connect(self.start_search)
        row.addWidget(self.scope_input, 1)
        self.fuzzy = QCheckBox('模糊类名')
        row.addWidget(self.fuzzy)
        self.search_button = QPushButton('搜索')
        self.search_button.setObjectName('primary')
        self.search_button.clicked.connect(self.start_search)
        row.addWidget(self.search_button)
        self.search_kind.currentIndexChanged.connect(self._search_kind_changed)
        layout.addWidget(controls)
        self.result_model = ResultModel()
        self.result_proxy = QSortFilterProxyModel(self)
        self.result_proxy.setSourceModel(self.result_model)
        self.result_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.result_proxy.setFilterKeyColumn(-1)
        self.results = QTableView()
        self.results.setModel(self.result_proxy)
        self.results.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results.setSelectionMode(QAbstractItemView.SingleSelection)
        self.results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results.setShowGrid(False)
        self.results.setAlternatingRowColors(True)
        self.results.setSortingEnabled(True)
        self.results.verticalHeader().hide()
        self.results.verticalHeader().setDefaultSectionSize(28)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setColumnWidth(0, 380)
        self.results.setColumnWidth(1, 200)
        self.results.setColumnWidth(2, 450)
        self.results.doubleClicked.connect(self.open_result)
        self.results.activated.connect(self.open_result)
        layout.addWidget(self.results, 1)
        foot, foot_layout = layout_widget(False, 8)
        self.result_status = QLabel('输入查询以开始 · 双击结果打开类')
        self.result_status.setObjectName('muted')
        foot_layout.addWidget(self.result_status, 1)
        self.result_filter = QLineEdit()
        self.result_filter.setMaximumWidth(270)
        self.result_filter.setPlaceholderText('过滤当前结果…')
        self.result_filter.textChanged.connect(self.result_proxy.setFilterFixedString)
        foot_layout.addWidget(self.result_filter)
        layout.addWidget(foot)
        self.bottom_tabs.addTab(search, '搜索结果')
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1500)
        self.bottom_tabs.addTab(self.logs, '运行日志')
        self.results_dock = self._dock('检索与输出', 'results', self.bottom_tabs, Qt.BottomDockWidgetArea)
        self.results_dock.setMinimumHeight(210)
        self._search_kind_changed()

    def _build_status(self):
        self.status_label = QLabel('就绪 · 打开 APK 开始分析')
        self.statusBar().addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.position_label = QLabel('UTF-8')
        self.statusBar().addPermanentWidget(self.position_label)
        if not self.settings.value('state'):
            QTimer.singleShot(0, lambda: self.resizeDocks([self.project_dock, self.outline_dock], [260, 210], Qt.Horizontal))
            QTimer.singleShot(0, lambda: self.resizeDocks([self.results_dock], [260], Qt.Vertical))

    def reset_layout(self):
        for dock, area in [(self.project_dock, Qt.LeftDockWidgetArea), (self.outline_dock, Qt.RightDockWidgetArea), (self.results_dock, Qt.BottomDockWidgetArea)]:
            dock.setFloating(False)
            self.addDockWidget(area, dock)
            dock.show()
        self.resizeDocks([self.project_dock, self.outline_dock], [260, 210], Qt.Horizontal)
        self.resizeDocks([self.results_dock], [260], Qt.Vertical)

    def choose_apk(self):
        if self.busy:
            self.status_label.setText('请等待当前任务完成后打开其他 APK')
            return
        path, _ = QFileDialog.getOpenFileName(self, '打开 Android APK', str(Path.home()), 'Android APK (*.apk);;所有文件 (*)')
        if path:
            self.open_apk(path)

    def open_apk(self, path):
        if self.busy:
            return
        path = str(Path(path).expanduser().resolve())
        if not Path(path).is_file():
            self._error(f'文件不存在：{path}')
            return
        self.stop_engine()
        self.path = path
        if self.definition_dialog:
            self.definition_dialog.close()
            self.definition_dialog = None
        self.active_page = None
        self.pending_origin = None
        self.pending_definition = None
        self.definition_origin = None
        self.loaded = False
        self.classes = []
        self.class_lookup = ClassLookup([])
        self.packages = {}
        for page in self.documents.values():
            page.deleteLater()
        self.documents.clear()
        self.tabs.clear()
        self.tree_model.clear()
        self.class_filter.clear()
        self.result_model.replace([])
        self.result_status.setText('输入查询以开始 · 双击结果打开类')
        self.result_filter.clear()
        self.history.clear()
        self.history_index = -1
        self.pending_line = None
        self.pending_source_range = None
        self.stack.setCurrentWidget(self.welcome)
        self.apk_label.setText(Path(path).name)
        self.apk_label.setToolTip(path)
        self.setWindowTitle(f'{Path(path).name} — ASC Workbench')
        self.welcome_status.setText('正在加载 APK 类列表…')
        self.class_count.setText('  正在读取 DEX…')
        self.busy = True
        self.progress.setRange(0, 0)
        self.progress.show()
        self._update_enabled()
        self.status_label.setText('正在启动分析引擎…')
        self.buffer = b''
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(ROOT))
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(['-u', '-m', 'asc_gui.worker'])
        self.proc.readyReadStandardOutput.connect(self._read_engine)
        self.proc.readyReadStandardError.connect(self._read_stderr)
        self.proc.errorOccurred.connect(self._process_error)
        self.proc.finished.connect(self._process_finished)
        self.pending_request = {'op': 'load', 'path': path}
        self.proc.start()

    def _send(self, request):
        if self.proc is None or self.busy:
            return False
        self.busy = True
        self.pending_request = request
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status_label.setText({'definitions': '正在查找方法定义…', 'source': '正在反编译类…', 'manifest': '正在解析 Manifest…', 'search': '正在搜索…'}.get(request['op'], '正在处理…'))
        self._update_enabled()
        self.proc.write((json.dumps(request) + '\n').encode())
        return True

    def _read_engine(self):
        self.buffer += bytes(self.proc.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                self.logs.appendPlainText(line.decode(errors='replace'))
                continue
            self._handle_message(message)

    def _read_stderr(self):
        data = bytes(self.proc.readAllStandardError()).decode(errors='replace').strip()
        if data:
            self.logs.appendPlainText(data[-16000:])

    def _handle_message(self, message):
        event = message['event']
        if event == 'ready':
            self.proc.write((json.dumps(self.pending_request) + '\n').encode())
        elif event == 'progress':
            self.progress.setRange(0, max(1, message['total']))
            self.progress.setValue(message['done'])
            self.status_label.setText(message['text'])
        elif event == 'rows':
            self.result_model.append(message['rows'])
            self.result_status.setText(f'搜索中 · 已显示 {len(self.result_model.rows):,} 条')
        elif event in ('result', 'error'):
            self.busy = False
            self.progress.hide()
            self.pending_request = None
            if event == 'error':
                if message['op'] == 'load':
                    self.class_count.setText('  加载失败')
                    self.welcome_status.setText('加载失败，可重新选择 APK。详细信息见运行日志。')
                elif message['op'] == 'search':
                    self.result_status.setText('搜索失败 · 详细信息见运行日志')
                self._error(message['message'], message.get('detail', ''))
            else:
                result = message['result']
                elapsed = message['elapsed']
                op = message['op']
                self.logs.appendPlainText(f'{op} 完成 · {elapsed:.3f} s')
                if op == 'load':
                    self.loaded = True
                    self.classes = result['classes']
                    self.class_lookup = ClassLookup(self.classes)
                    self._index_packages()
                    self._filter_tree()
                    self.welcome_status.setText(f'已加载 {Path(self.path).name}\n{len(self.classes):,} 个类 · {result["dex_count"]} 个 DEX\n从左侧选择类，或按 Ctrl+N 搜索。')
                    self._remember_recent(self.path)
                    self.status_label.setText(f'加载完成 · {len(self.classes):,} 个类 · {result["dex_count"]} DEX · {elapsed:.2f} s')
                elif op in ('source', 'manifest'):
                    key = result['class_name'] if op == 'source' else '@manifest'
                    self.status_label.setText(f'就绪 · {elapsed:.2f} s')
                    self.add_document(key, result['source'], result.get('dex', 'XML'))
                elif op == 'definitions':
                    self._definition_results(result)
                elif op == 'search':
                    self.result_model.replace(result['results'])
                    self.result_status.setText(f'{result["total_hits"]:,} 条命中 · 显示 {len(result["results"]):,} 条（上限 5,000） · {elapsed:.2f} s')
                    self.status_label.setText('搜索完成 · 双击结果打开类')
                    if result.get('exact'):
                        if result.get('reason'):
                            self.result_status.setText(result['reason'])
                            self.status_label.setText(result['reason'])
                        else:
                            scope = result['scope_text']
                            self.result_status.setText(self.result_status.text()+' · 精确引用：'+scope)
                            self.logs.appendPlainText('引用所属类：'+scope+'；结果包含其他类中的调用/访问位置')
                        owners = result.get('owners', [])
                        self.scope_input.setText(owners[0] if len(owners) == 1 and self.search_kind.currentData() != 'type refs' else '')
            self._update_enabled()

    def _error(self, message, detail=''):
        self.status_label.setText(f'操作失败：{message[:180]}')
        self.logs.appendPlainText(message + ('\n' + detail if detail else ''))
        self.results_dock.show()
        self.bottom_tabs.setCurrentIndex(1)
        self.pending_line = None
        self.pending_source_range = None
        self.pending_definition = None
        self.pending_origin = None

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self.busy = False
            self.loaded = False
            self.progress.hide()
            self._error('无法启动分析引擎：' + self.proc.errorString())
            self._update_enabled()

    def _process_finished(self, code, status):
        self.busy = False
        self.loaded = False
        self.progress.hide()
        self._error(f'分析引擎已退出（{code}），请重新打开 APK')
        self._update_enabled()

    def stop_engine(self):
        if self.proc:
            self.proc.blockSignals(True)
            if self.proc.state() != QProcess.NotRunning:
                pid = self.proc.processId()
                if os.name == 'posix':
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        self.proc.kill()
                else:
                    self.proc.kill()
                self.proc.waitForFinished(3000)
            self.proc.deleteLater()
            self.proc = None

    def _index_packages(self):
        self.packages = {'': {'packages': set(), 'classes': []}}
        for name in self.classes:
            parts = (name[1:-1] if name.startswith('L') and name.endswith(';') else name).split('/')
            parent = ''
            for segment in parts[:-1]:
                child = parent + '/' + segment if parent else segment
                self.packages.setdefault(parent, {'packages': set(), 'classes': []})['packages'].add(child)
                self.packages.setdefault(child, {'packages': set(), 'classes': []})
                parent = child
            self.packages[parent]['classes'].append(name)

    def _item(self, text, kind, value):
        item = QStandardItem(text)
        item.setEditable(False)
        item.setData((kind, value), ROLE)
        item.setIcon(badge({'class': 'C', 'package': 'P', 'manifest': 'X', 'root': 'A'}.get(kind, '·'),
                           {'class': '#8ac6a0', 'package': '#cfb67e', 'manifest': '#b399de'}.get(kind, '#78a6ff')))
        item.setToolTip(value)
        return item

    def _populate(self, parent, package):
        data = self.packages.get(package, {'packages': [], 'classes': []})
        for path in sorted(data['packages']):
            node = self._item(path.rsplit('/', 1)[-1], 'package', path)
            node.appendRow(QStandardItem('…'))
            parent.appendRow(node)
        for name in data['classes']:
            parent.appendRow(self._item(name.rsplit('/', 1)[-1].rstrip(';'), 'class', name))

    def _filter_tree(self):
        self.tree_model.clear()
        if not self.loaded:
            return
        query = self.class_filter.text().strip().lower().replace('.', '/')
        if query:
            matches = [name for name in self.classes if query in name.lower()]
            matches.sort(key=lambda name: (name[1:-1].lower() != query, name))
            for name in matches[:2000]:
                self.tree_model.appendRow(self._item(name[1:-1].replace('/', '.'), 'class', name))
            self.class_count.setText(f'  匹配 {len(matches):,} 个类 · 显示前 {min(2000, len(matches)):,} 个')
        else:
            root = self._item(Path(self.path).name, 'root', self.path)
            self.tree_model.appendRow(root)
            root.appendRow(self._item('AndroidManifest.xml', 'manifest', '@manifest'))
            source = self._item('源代码', 'package', '')
            root.appendRow(source)
            self._populate(source, '')
            self.tree.expand(root.index())
            self.tree.expand(source.index())
            self.class_count.setText(f'  {len(self.classes):,} 个类 · 按包展开')

    def _expand(self, index):
        item = self.tree_model.itemFromIndex(index)
        data = item.data(ROLE)
        if data and data[0] == 'package' and item.rowCount() == 1 and item.child(0).data(ROLE) is None:
            item.removeRows(0, 1)
            self._populate(item, data[1])

    def _tree_open(self, index):
        data = index.data(ROLE)
        if data and data[0] in ('class', 'manifest'):
            self.open_document(data[1])

    def _open_first_class(self):
        self.tree_timer.stop()
        self._filter_tree()
        if self.class_filter.text().strip() and self.tree_model.rowCount():
            self._tree_open(self.tree_model.index(0, 0))

    def capture_location(self, page=None):
        page = page or self.active_page
        if page is None:
            return None
        cursor = page.editor.textCursor()
        return {'key': page.key, 'position': cursor.position(), 'anchor': cursor.anchor(),
                'vertical': page.editor.verticalScrollBar().value(),
                'horizontal': page.editor.horizontalScrollBar().value()}

    def remember_location(self):
        location = self.capture_location()
        if location and 0 <= self.history_index < len(self.history) and self.history[self.history_index]['key'] == location['key']:
            self.history[self.history_index] = location

    def push_location(self, location):
        if location is None or location['key'] not in self.documents:
            return
        if self.history_index >= 0 and self.history[self.history_index]['key'] == location['key'] and self.history[self.history_index]['position'] == location['position']:
            self.history[self.history_index] = location
            return
        self.history = self.history[:self.history_index+1] + [location]
        self.history = self.history[-200:]
        self.history_index = len(self.history)-1

    def open_document(self, key, line=None, definition=None, origin=None, source_range=None):
        if self.busy and key in self.documents:
            self.tabs.setCurrentWidget(self.documents[key])
            self.stack.setCurrentWidget(self.tabs)
            return
        if key not in self.documents and (not self.loaded or self.busy):
            self.status_label.setText('请等待当前任务完成，再打开新的类')
            return
        self.remember_location()
        self.pending_origin = origin or self.capture_location()
        self.pending_line = line
        self.pending_source_range = source_range
        self.pending_definition = definition
        if key in self.documents:
            self.navigating = True
            self.tabs.setCurrentWidget(self.documents[key])
            self.navigating = False
            self.stack.setCurrentWidget(self.tabs)
            self._finish_open(self.documents[key])
            return
        request = {'op': 'manifest'} if key == '@manifest' else {'op': 'source', 'class_name': key}
        self._send(request)

    def _finish_open(self, page):
        origin = self.pending_origin
        target = self.pending_definition
        line = self.pending_line
        source_range = self.pending_source_range
        self.pending_origin = None
        self.pending_line = None
        self.pending_source_range = None
        self.pending_definition = None
        self.active_page = page
        if target:
            locations = matching_declarations(page.editor.toPlainText(), target)
            if len(locations) == 1:
                line = locations[0]['line']
            elif locations:
                self._choose_definitions([{**target, 'line': row['line'], 'signature': row['signature']+' · 行 '+str(row['line'])} for row in locations], origin)
            else:
                self.status_label.setText('已打开目标类；未能在反编译源码中精确定位该方法')
        if line:
            page.editor.goto_line(line)
        if source_range is not None:
            source = page.editor.toPlainText()
            start, end = source_range
            cursor = page.editor.textCursor()
            cursor.setPosition(len(source[:start].encode('utf-16-le')) // 2)
            cursor.setPosition(len(source[:end].encode('utf-16-le')) // 2, QTextCursor.KeepAnchor)
            page.editor.setTextCursor(cursor)
            page.editor.centerCursor()
            page.editor.setFocus()
        self.push_location(origin)
        self.push_location(self.capture_location(page))
        self._update_enabled()

    def add_document(self, key, source, dex):
        page, layout = layout_widget()
        page.key = key
        title = 'AndroidManifest.xml' if key == '@manifest' else key.split('/')[-1].rstrip(';') + '.java'
        breadcrumb = QLabel(('AndroidManifest.xml' if key == '@manifest' else key[1:-1].replace('/', '  ›  ')) + '    ·    ' + dex)
        breadcrumb.setObjectName('breadcrumb')
        breadcrumb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        breadcrumb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        breadcrumb.setToolTip(breadcrumb.text())
        layout.addWidget(breadcrumb)
        page.editor = CodeEditor(self.theme, self.font_size)
        page.editor.setPlainText(source)
        page.editor.highlighter.type_names = {alias for alias in self.class_lookup.aliases if '.' not in alias}
        page.editor.highlighter.rehighlight()
        page.editor.definitionRequested.connect(lambda position, e=page.editor: self.jump_definition(e, position))
        page.editor.definition_action = QAction('跳转到定义', page.editor)
        page.editor.definition_action.setShortcut(QKeySequence('F3'))
        page.editor.definition_action.setShortcutContext(Qt.WidgetShortcut)
        page.editor.definition_action.triggered.connect(lambda checked=False, e=page.editor: self.jump_definition(e, e.textCursor().selectionStart()))
        page.editor.addAction(page.editor.definition_action)
        page.editor.xref_action = QAction('查找交叉引用', page.editor)
        page.editor.xref_action.setShortcut(QKeySequence('X'))
        page.editor.xref_action.setShortcutContext(Qt.WidgetShortcut)
        page.editor.xref_action.setAutoRepeat(False)
        page.editor.xref_action.triggered.connect(lambda checked=False, e=page.editor: self.find_xrefs(e))
        page.editor.addAction(page.editor.xref_action)
        page.editor.cursorPositionChanged.connect(self._position_changed)
        page.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        page.editor.customContextMenuRequested.connect(lambda pos, e=page.editor: self.editor_menu(e, pos))
        layout.addWidget(page.editor, 1)
        self.documents[key] = page
        self.navigating = True
        index = self.tabs.addTab(page, badge('X' if key == '@manifest' else 'C', '#8ac6a0'), title)
        self.tabs.setTabToolTip(index, key)
        self.tabs.setCurrentIndex(index)
        self.stack.setCurrentWidget(self.tabs)
        self.navigating = False
        self._finish_open(page)

    def current_editor(self):
        page = self.tabs.currentWidget()
        return page.editor if page else None

    def _tab_changed(self, index):
        if not self.navigating:
            self.remember_location()
        self.outline.clear()
        page = self.tabs.widget(index) if index >= 0 else None
        self.active_page = page
        if page:
            if not self.navigating:
                self.push_location(self.capture_location(page))
            for row in declarations(page.editor.toPlainText(), page.key):
                item = QListWidgetItem(badge('m', '#b399de'), row['signature'])
                item.setData(ROLE, row['line'])
                item.setToolTip(row['signature']+' · 行 '+str(row['line']))
                self.outline.addItem(item)
            self._position_changed()
        if not self.tabs.count():
            self.stack.setCurrentWidget(self.welcome)
        self._update_enabled()

    def eventFilter(self, watched, event):
        if hasattr(self, 'tabs') and watched is self.tabs.tabBar() and event.type() == QEvent.MouseButtonRelease and event.button() == Qt.MiddleButton:
            self.close_tab(self.tabs.tabBar().tabAt(event.position().toPoint()))
            return True
        return super().eventFilter(watched, event)

    def editor_menu(self, editor, pos):
        if not editor.textCursor().hasSelection():
            editor.setTextCursor(editor.cursorForPosition(pos))
        menu = editor.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction(editor.definition_action)
        global_definition = menu.addAction('全局查找同名方法定义…')
        global_definition.triggered.connect(lambda: self.jump_definition(editor, editor.textCursor().selectionStart(), allow_global=True))
        menu.addAction(editor.xref_action)
        global_refs = menu.addAction('全局按名称查找引用…')
        global_refs.triggered.connect(lambda: self.find_xrefs(editor, global_search=True))
        search = menu.addAction('使用选中文本搜索引用')
        search.setEnabled(editor.textCursor().hasSelection())
        search.triggered.connect(self.focus_search)
        menu.addAction(self.find_action)
        menu.addAction(self.export_action)
        menu.exec(editor.mapToGlobal(pos))
        menu.deleteLater()

    def jump_definition(self, editor, position, allow_global=False):
        page = self.tabs.currentWidget()
        if page is None or page.editor is not editor or page.key == '@manifest':
            return
        if not self.loaded or self.busy:
            self.status_label.setText('请等待当前任务完成后跳转')
            return
        source = editor.toPlainText()
        offset = python_offset(source, position)
        variable = find_variable_definition(source, offset, page.key)
        if variable is not None and not allow_global:
            origin = self.capture_location(page)
            self.open_document(page.key, source_range=(variable.start, variable.end), origin=origin)
            label = '函数参数声明' if variable.kind == 'parameter' else '局部变量声明'
            self.status_label.setText(f'已跳转到{label}：{variable.name}')
            return
        query = navigation_query(source, offset, page.key, self.class_lookup)
        if query is None:
            self.status_label.setText('没有可跳转的 APK 内定义；可选中文本后按 X 查找引用')
            return
        if allow_global and query.kind != 'method':
            self.status_label.setText('请在方法调用名称上使用全局查找')
            return
        self.definition_origin = self.capture_location(page)
        if query.kind == 'class':
            candidates = [{'class_name': key, 'signature': key[1:-1].replace('/', '.')} for key in query.owners]
            self._definition_results({'candidates': candidates, 'total': len(candidates), 'uncertain': query.uncertain})
        else:
            self._send({'op': 'definitions', **asdict(query), 'allow_global': allow_global})

    def _definition_results(self, result):
        candidates = result['candidates']
        origin = self.definition_origin
        self.definition_origin = None
        if not candidates:
            self.status_label.setText(result.get('reason') or 'APK 中没有可用的方法定义（可能是系统库或外部依赖）')
            return
        if len(candidates) == 1 and not result.get('uncertain'):
            self._jump_candidate(candidates[0], origin)
        else:
            self._choose_definitions(candidates, origin, result.get('total', len(candidates)), result.get('global_search', False))

    def _jump_candidate(self, candidate, origin):
        self.open_document(candidate['class_name'], line=candidate.get('line', 1 if 'name' not in candidate else None),
                           definition=candidate if 'name' in candidate and 'line' not in candidate else None,
                           origin=origin)

    def _choose_definitions(self, candidates, origin, total=None, global_search=False):
        if self.definition_dialog:
            self.definition_dialog.close()
            self.definition_dialog.deleteLater()
        dialog = QDialog(self)
        self.definition_dialog = dialog
        dialog.setWindowTitle('全局同名方法：选择定义' if global_search else '选择要跳转的定义')
        dialog.resize(820, 360)
        layout = QVBoxLayout(dialog)
        hint = QLabel(f'找到 {total or len(candidates)} 个候选（显示 {len(candidates)} 个），请选择目标类或方法签名。')
        if global_search:
            hint.setText('以下为全局同名候选，尚未确认调用所属类。\n'+hint.text())
        hint.setWordWrap(True)
        layout.addWidget(hint)
        entries = QListWidget()
        dialog.entries = entries
        for candidate in candidates:
            item = QListWidgetItem(candidate['signature'])
            item.setData(ROLE, candidate)
            item.setToolTip(candidate['signature'])
            entries.addItem(item)
        layout.addWidget(entries, 1)
        button = QPushButton('跳转到所选定义')
        button.setObjectName('primary')
        layout.addWidget(button)
        def choose():
            item = entries.currentItem()
            if item:
                candidate = item.data(ROLE)
                dialog.accept()
                self._jump_candidate(candidate, origin)
        entries.itemActivated.connect(lambda item: choose())
        button.clicked.connect(choose)
        entries.setCurrentRow(0)
        # Open rather than exec: definition selection does not block the event loop.
        dialog.open()

    def find_xrefs(self, editor, global_search=False):
        if not self.loaded or self.busy:
            self.status_label.setText('请先打开 APK，或等待当前分析任务完成')
            return
        page = self.tabs.currentWidget()
        if page is None or page.editor is not editor:
            return
        if page.key == '@manifest':
            self.status_label.setText('X 交叉引用适用于 Java 代码；Manifest 可选中文本后手动搜索')
            return
        source = editor.toPlainText()
        cursor = editor.textCursor()
        start = python_offset(source, cursor.selectionStart())
        end = python_offset(source, cursor.selectionEnd()) if cursor.hasSelection() else None
        target = infer_reference(source, start, end, self.classes)
        if target is None:
            self.status_label.setText('请将光标放在方法、字段、类型或字符串上，再按 X')
            return
        self.results_dock.show()
        self.results_dock.raise_()
        self.bottom_tabs.setCurrentIndex(0)
        self.search_input.setText(target.query)
        self.scope_input.clear()
        self.fuzzy.setChecked(False)
        if not global_search and target.kind != 'string refs':
            if target.kind == 'type refs':
                name = target.query
                owners = [name] if name.startswith('L') and name.endswith(';') else self.class_lookup.resolve_scoped(name, source, page.key)
                request = {'kind': 'type', 'name': name, 'owners': owners}
            else:
                request = scoped_reference(source, start, page.key, self.class_lookup, target)
                if request is None:
                    self.result_model.replace([])
                    self.result_status.setText('局部变量没有 DEX 成员引用；可双击跳转声明')
                    self.status_label.setText(self.result_status.text())
                    return
            kind = request['kind']+' refs'
            self.search_kind.setCurrentIndex(self.search_kind.findData(kind))
            self.result_model.replace([])
            self.result_filter.clear()
            self.result_status.setText('正在解析所属类并查找精确引用…')
            self.logs.appendPlainText('X 精确引用 · '+target.query)
            self._send({'op': 'search', 'kind': kind, 'query': target.query, 'reference_request': request})
            return
        if target.kind is None:
            self.search_kind.setCurrentIndex(self.search_kind.findData('field refs'))
            self.result_model.replace([])
            self.result_status.setText('无法确定符号类型，请选择引用类型后点击搜索')
            self.status_label.setText('待确认引用类型：' + target.query)
            self.search_kind.setFocus()
            return
        self.search_kind.setCurrentIndex(self.search_kind.findData(target.kind))
        self.logs.appendPlainText('X 交叉引用 · ' + self.search_kind.currentText() + ' · ' + target.query + '（按名称检索）')
        self.start_search()

    def _outline_jump(self, item):
        page = self.tabs.currentWidget()
        if page:
            self.open_document(page.key, line=item.data(ROLE))

    def _position_changed(self):
        if editor := self.current_editor():
            cursor = editor.textCursor()
            self.position_label.setText(f'行 {cursor.blockNumber() + 1} : 列 {cursor.positionInBlock() + 1}    UTF-8')

    def close_tab(self, index):
        page = self.tabs.widget(index) if index >= 0 else None
        if page:
            self.documents.pop(page.key, None)
            self.history = [loc for loc in self.history if loc['key'] != page.key]
            self.active_page = None
            self.history_index = len(self.history) - 1
            self.tabs.removeTab(index)
            page.deleteLater()
            self._update_enabled()

    def cycle_tab(self, direction):
        if self.tabs.count():
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + direction) % self.tabs.count())

    def escape_back(self):
        if self.find_bar.isVisible():
            self.hide_find()
            if editor := self.current_editor():
                editor.setFocus()
        elif self.stack.currentWidget() is self.welcome and self.tabs.count():
            self.navigate(0)
        else:
            self.navigate(-1)

    def navigate(self, direction):
        index = self.history_index + direction
        if 0 <= index < len(self.history):
            self.remember_location()
            location = self.history[index]
            page = self.documents.get(location['key'])
            if page is None:
                return
            self.history_index = index
            self.navigating = True
            self.tabs.setCurrentWidget(page)
            self.stack.setCurrentWidget(self.tabs)
            self.active_page = page
            cursor = page.editor.textCursor()
            limit = page.editor.document().characterCount()-1
            cursor.setPosition(min(location['anchor'], limit))
            cursor.setPosition(min(location['position'], limit), QTextCursor.KeepAnchor)
            page.editor.setTextCursor(cursor)
            page.editor.verticalScrollBar().setValue(location['vertical'])
            page.editor.horizontalScrollBar().setValue(location['horizontal'])
            page.editor.setFocus()
            self.navigating = False
            self._update_enabled()

    def _search_kind_changed(self):
        enable = self.search_kind.currentData() in ('method refs', 'field refs')
        self.scope_input.setEnabled(enable)
        self.fuzzy.setEnabled(enable)

    def start_search(self):
        if not self.loaded or self.busy:
            return
        query = self.search_input.text().strip()
        scope = self.scope_input.text().strip() if self.scope_input.isEnabled() else ''
        if not query and not scope:
            self.result_status.setText('请输入搜索内容')
            self.search_input.setFocus()
            return
        self.result_model.replace([])
        self.result_filter.clear()
        self.result_status.setText('搜索中…')
        self.bottom_tabs.setCurrentIndex(0)
        self._send({'op': 'search', 'kind': self.search_kind.currentData(), 'query': query,
                    'class_filter': scope, 'fuzzy': self.fuzzy.isChecked()})

    def open_result(self, index):
        source_index = self.result_proxy.mapToSource(index)
        if source_index.isValid():
            row = self.result_model.rows[source_index.row()]
            self.open_document(row['class_name'])

    def focus_search(self):
        self.results_dock.show()
        self.results_dock.raise_()
        self.bottom_tabs.setCurrentIndex(0)
        editor = self.current_editor()
        if editor and editor.textCursor().hasSelection():
            selected = editor.textCursor().selectedText()
            if len(selected) < 200:
                self.search_input.setText(selected)
        self.search_input.setFocus()
        self.search_input.selectAll()

    def focus_classes(self):
        self.project_dock.show()
        self.project_dock.raise_()
        self.class_filter.setFocus()
        self.class_filter.selectAll()

    def hide_find(self):
        self.find_bar.hide()
        for page in self.documents.values():
            page.editor.highlight_search('')

    def show_find(self):
        if editor := self.current_editor():
            self.find_bar.show()
            if editor.textCursor().hasSelection():
                self.find_input.setText(editor.textCursor().selectedText())
            self.find_input.setFocus()
            self.find_input.selectAll()

    def find_text(self, backwards=False):
        editor = self.current_editor()
        query = self.find_input.text()
        if not editor or not query:
            return
        editor.highlight_search(query)
        flags = QTextDocument.FindBackward if backwards else QTextDocument.FindFlags()
        previous = editor.textCursor()
        found = editor.find(query, flags)
        if not found:
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.End if backwards else QTextCursor.Start)
            editor.setTextCursor(cursor)
            found = editor.find(query, flags)
        if not found:
            editor.setTextCursor(previous)
        self.find_status.setText('已定位' if found else '无匹配')

    def goto_line(self):
        if editor := self.current_editor():
            line, ok = QInputDialog.getInt(self, '跳转到行', '行号', editor.textCursor().blockNumber() + 1, 1, editor.blockCount())
            if ok:
                self.open_document(self.tabs.currentWidget().key, line=line)

    def zoom(self, delta):
        self.font_size = max(8, min(28, self.font_size + delta))
        for page in self.documents.values():
            page.editor.set_font_size(self.font_size)

    def export_source(self):
        if editor := self.current_editor():
            name = self.tabs.tabText(self.tabs.currentIndex())
            path, _ = QFileDialog.getSaveFileName(self, '导出源码', name, '文本文件 (*.java *.xml *.txt);;所有文件 (*)')
            if path:
                try:
                    Path(path).write_text(editor.toPlainText(), encoding='utf-8')
                    self.status_label.setText('已导出：' + path)
                except OSError as exc:
                    self._error(str(exc))

    def toggle_theme(self):
        self.dark = not self.dark
        self.theme = apply_theme(QApplication.instance(), self.dark)
        for page in self.documents.values():
            page.editor.set_theme(self.theme)

    def _update_enabled(self):
        if not hasattr(self, 'search_button'):
            return
        self.open_action.setEnabled(not self.busy)
        self.search_button.setEnabled(self.loaded and not self.busy)
        self.resume_button.setVisible(self.tabs.count() > 0)
        has_editor = self.current_editor() is not None
        for action in (self.find_action, self.goto_action, self.export_action, self.close_tab_action):
            action.setEnabled(has_editor)
        self.back_action.setEnabled(self.history_index > 0)
        self.forward_action.setEnabled(self.history_index + 1 < len(self.history))

    def resume_workspace(self):
        if self.tabs.count():
            self.stack.setCurrentWidget(self.tabs)
            self.current_editor().setFocus()

    def show_home(self):
        self._refresh_recent()
        self.stack.setCurrentWidget(self.welcome)

    def _recent_paths(self):
        return list(dict.fromkeys(self.settings.value('recent', [], type=list)))[:12]

    def _refresh_recent(self):
        self.recent_menu.clear()
        self.recent_list.clear()
        recent = self._recent_paths()
        for path in recent:
            available = Path(path).is_file()
            label = Path(path).name + ('（文件已移动或删除）' if not available else '')
            action = self.recent_menu.addAction(label+' — '+str(Path(path).parent))
            action.setToolTip(path)
            action.setEnabled(available)
            action.triggered.connect(lambda checked=False, p=path: self.open_apk(p))
            item = QListWidgetItem(badge('A', '#78a6ff'), label+'\n'+path)
            item.setData(ROLE, path)
            item.setToolTip(path)
            if not available:
                item.setForeground(QColor(self.theme['muted']))
            self.recent_list.addItem(item)
        self.recent_hint.setText('双击打开 · 右键可从记录中移除' if recent else '暂无历史记录，打开 APK 后会自动保存。')
        if recent:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction('清空记录', self.clear_recent)
        self.recent_menu.setEnabled(bool(recent))

    def _remember_recent(self, path):
        path = str(Path(path).resolve())
        self.settings.setValue('recent', [path]+[p for p in self._recent_paths() if p != path][:11])
        self.settings.sync()
        self._refresh_recent()

    def clear_recent(self):
        self.settings.setValue('recent', [])
        self.settings.sync()
        self._refresh_recent()

    def _open_recent_item(self, item):
        path = item.data(ROLE)
        if not Path(path).is_file():
            self.status_label.setText('文件已移动或删除，可右键从最近打开中移除：'+path)
        else:
            self.open_apk(path)

    def _recent_context_menu(self, pos):
        item = self.recent_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        remove = menu.addAction('从最近打开中移除')
        if menu.exec(self.recent_list.mapToGlobal(pos)) is remove:
            self.settings.setValue('recent', [p for p in self._recent_paths() if p != item.data(ROLE)])
            self.settings.sync()
            self._refresh_recent()
        menu.deleteLater()

    def show_help(self):
        self.logs.appendPlainText('ASC Workbench v0.2\nCtrl+O 打开 APK；Ctrl+N 筛选类；Ctrl+Shift+F 搜索引用；Ctrl+F 文件内查找；Ctrl+G 跳行；代码区按 X 查找光标处或选中符号的交叉引用。\n双击代码符号或按 F3 跳转到类/方法定义，Alt+←/→ 恢复光标与滚动位置。起始页显示最近打开。Ctrl+滚轮缩放代码。\n可拖动或关闭侧栏，通过「视图」恢复。源码只读，当前版本不修改 APK。\n搜索结果上限 5,000 条；类过滤最多展示 2,000 条，请缩小查询范围。\n方法结构为文本导航；引用结果目前定位到类。当前后端会加载所有 DEX。')
        self.results_dock.show()
        self.bottom_tabs.setCurrentIndex(1)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and urls and urls[0].isLocalFile() and urls[0].toLocalFile().lower().endswith('.apk'):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.open_apk(urls[0].toLocalFile())
            event.acceptProposedAction()

    def closeEvent(self, event):
        if self.definition_dialog:
            self.definition_dialog.close()
        self.settings.setValue('geometry', self.saveGeometry())
        self.settings.setValue('state', self.saveState())
        self.settings.setValue('dark', self.dark)
        self.settings.setValue('font_size', self.font_size)
        self.settings.sync()
        self.stop_engine()
        event.accept()
