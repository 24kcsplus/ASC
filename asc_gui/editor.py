import re
from array import array
from PySide6.QtCore import Qt, QRect, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit
from .navigation import mask_literals, identifier_at
from .xref import IDENT, python_offset


class Highlighter(QSyntaxHighlighter):
    def __init__(self, document, theme):
        super().__init__(document)
        self.theme = theme
        self.rules = [
            (re.compile(r'\b(?:abstract|boolean|break|byte|case|catch|char|class|const|continue|default|do|double|else|enum|extends|final|finally|float|for|if|implements|import|instanceof|int|interface|long|native|new|package|private|protected|public|return|short|static|super|switch|synchronized|this|throw|throws|try|void|volatile|while|true|false|null)\b'), 'keyword'),
            (re.compile(r'\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)[lLfFdD]?\b'), 'number'),
            (re.compile(r'@[\w.]+|</?[\w:.-]+'), 'keyword'),
        ]
        self.type_names = set()
        self.lex = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*|<!--')

    def paint_format(self, start, length, key):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.theme[key]))
        self.setFormat(start, length, fmt)

    def highlightBlock(self, text):
        # Qt text offsets are UTF-16, while Python regex offsets are code points.
        def mark(start, end, key):
            qstart = len(text[:start].encode('utf-16-le')) // 2
            qend = len(text[:end].encode('utf-16-le')) // 2
            self.paint_format(qstart, qend - qstart, key)
        for match in re.finditer(IDENT, text):
            name = match.group()
            if name in self.type_names or (name[0].isupper() and not name.isupper()):
                mark(match.start(), match.end(), 'type')
            if re.match(r'\s*\(', text[match.end():]):
                mark(match.start(), match.end(), 'method')
        for regex, key in self.rules:
            for m in regex.finditer(text):
                mark(m.start(), m.end(), key)
        self.setCurrentBlockState(0)
        offset = 0
        state = self.previousBlockState()
        if state in (1, 2):
            closing = '*/' if state == 1 else '-->'
            end = text.find(closing)
            if end == -1:
                mark(0, len(text), 'comment')
                self.setCurrentBlockState(state)
                return
            offset = end + len(closing)
            mark(0, offset, 'comment')
        while match := self.lex.search(text, offset):
            token = match.group()
            if token in ('/*', '<!--'):
                closing = '*/' if token == '/*' else '-->'
                end = text.find(closing, match.end())
                if end == -1:
                    mark(match.start(), len(text), 'comment')
                    self.setCurrentBlockState(1 if token == '/*' else 2)
                    break
                offset = end + len(closing)
                mark(match.start(), offset, 'comment')
            else:
                mark(match.start(), match.end(), 'comment' if token.startswith('//') else 'string')
                offset = match.end()


class Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    definitionRequested = Signal(int)

    def __init__(self, theme, font_size=12):
        super().__init__()
        self.theme = theme
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont('JetBrains Mono', font_size)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        self.font_size = font_size
        self.setStyleSheet(f'QPlainTextEdit {{ font-family: "DejaVu Sans Mono", "Noto Sans Mono", monospace; font-size: {font_size}pt; }}')
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 4)
        self.gutter = Gutter(self)
        self.highlighter = Highlighter(self.document(), theme)
        self.blockCountChanged.connect(self.update_gutter)
        self.updateRequest.connect(self.scroll_gutter)
        self.cursorPositionChanged.connect(self.highlight_line)
        self.symbol_timer = QTimer(self)
        self.symbol_timer.setSingleShot(True)
        self.symbol_timer.setInterval(100)
        self.symbol_timer.timeout.connect(self.highlight_symbols)
        self.cursorPositionChanged.connect(lambda: self.symbol_timer.start())
        self.selectionChanged.connect(lambda: self.symbol_timer.start())
        self.symbol_selections = []
        self.search_selections = []
        self._search_query = ''
        self._source_cache = None
        self.textChanged.connect(self._reset_source_cache)
        self.update_gutter()
        self.highlight_line()

    def gutter_width(self):
        return 22 + self.fontMetrics().horizontalAdvance('9') * len(str(max(1, self.blockCount())))

    def update_gutter(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        rect = self.contentsRect()
        self.gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))
        self.gutter.update()

    def scroll_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_gutter()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_gutter()

    def paint_gutter(self, event):
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), QColor(self.theme['editor']))
        p.setFont(self.font())
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                p.setPen(QColor(self.theme['accent'] if block.blockNumber() == self.textCursor().blockNumber() else self.theme['muted']))
                p.drawText(0, top, self.gutter.width() - 12, self.fontMetrics().height(), Qt.AlignRight, str(block.blockNumber() + 1))
            top += height
            block = block.next()

    def highlight_line(self):
        s = QTextEdit.ExtraSelection()
        s.format.setBackground(QColor(self.theme['line']))
        s.format.setProperty(QTextFormat.FullWidthSelection, True)
        s.cursor = self.textCursor()
        s.cursor.clearSelection()
        self.setExtraSelections([s] + self.search_selections + self.symbol_selections)
        self.gutter.update()

    def set_theme(self, theme):
        self.theme = theme
        self.highlighter.theme = theme
        self.highlighter.rehighlight()
        self.highlight_search(self._search_query)
        self.highlight_symbols()

    def goto_line(self, line):
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            self.setTextCursor(QTextCursor(block))
            self.centerCursor()
            self.setFocus()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.set_font_size(self.font_size + (1 if event.angleDelta().y() > 0 else -1))
            event.accept()
        else:
            super().wheelEvent(event)

    def set_font_size(self, size):
        self.font_size = max(8, min(28, size))
        self.setStyleSheet(f'QPlainTextEdit {{ font-family: "DejaVu Sans Mono", "Noto Sans Mono", monospace; font-size: {self.font_size}pt; }}')
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 4)
        self.update_gutter()

    def _reset_source_cache(self):
        # Qt can emit textChanged for syntax-format changes without a text edit.
        if self._source_cache is not None and self._source_cache[0] == self.toPlainText():
            return
        self._source_cache = None
        self.symbol_selections = []
        self.search_selections = []
        self._search_query = ''
        self.symbol_timer.start()

    def source_context(self):
        if self._source_cache is None:
            source = self.toPlainText()
            self._source_cache = (source, mask_literals(source))
            self._qt_offsets = None
            if any(ord(char) > 0xffff for char in source):
                positions = array('I', [0])
                for char in source:
                    positions.append(positions[-1] + (2 if ord(char) > 0xffff else 1))
                self._qt_offsets = positions
        return self._source_cache

    def _selection(self, source, start, end, color):
        selection = QTextEdit.ExtraSelection()
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._qt_offsets[start] if self._qt_offsets is not None else start)
        cursor.setPosition(self._qt_offsets[end] if self._qt_offsets is not None else end, QTextCursor.KeepAnchor)
        selection.cursor = cursor
        selection.format.setBackground(QColor(color))
        return selection

    def highlight_symbols(self):
        self.symbol_selections = []
        source, clean = self.source_context()
        cursor = self.textCursor()
        offset = python_offset(source, cursor.selectionStart())
        token = identifier_at(source, offset)
        if token:
            start, end, name = token
            if not cursor.hasSelection() or cursor.selectedText() == name:
                pattern = re.compile(r'(?<![\w$])'+re.escape(name)+r'(?![\w$])')
                for match in pattern.finditer(clean):
                    self.symbol_selections.append(self._selection(source, match.start(), match.end(), self.theme['occurrence']))
                    if len(self.symbol_selections) >= 1000:
                        break
        # Match braces around the caret while ignoring comments and literals.
        position = python_offset(source, cursor.position())
        pairs = {'(': ')', '[': ']', '{': '}'}
        for pos in (position, position-1):
            if not 0 <= pos < len(clean):
                continue
            char = clean[pos]
            if char not in '()[]{}':
                continue
            forward = char in pairs
            other = pairs[char] if forward else {v:k for k,v in pairs.items()}[char]
            depth = 0
            positions = range(pos, len(clean)) if forward else range(pos, -1, -1)
            for match_pos in positions:
                if clean[match_pos] == char:
                    depth += 1
                elif clean[match_pos] == other:
                    depth -= 1
                if depth == 0:
                    for at in (pos, match_pos):
                        self.symbol_selections.append(self._selection(source, at, at+1, self.theme['bracket']))
                    break
            break
        self.highlight_line()

    def highlight_search(self, query):
        self.source_context()
        self._search_query = query
        self.search_selections = []
        if query:
            cursor = QTextCursor(self.document())
            for _ in range(1000):
                cursor = self.document().find(query, cursor)
                if cursor.isNull():
                    break
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format.setBackground(QColor(self.theme['occurrence']))
                self.search_selections.append(selection)
        self.highlight_line()

    def mouseDoubleClickEvent(self, event):
        position = self.cursorForPosition(event.position().toPoint()).position()
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.LeftButton and not event.modifiers():
            self.definitionRequested.emit(position)
