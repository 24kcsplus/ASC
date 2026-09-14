"""X shortcut parsing, focus isolation and real-APK reference integration tests."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from asc_gui.app import MainWindow
from asc_gui.xref import infer_reference, python_offset


class ReferenceTests(unittest.TestCase):
    def target(self, text, word, classes=()):
        return infer_reference(text, text.index(word) + 1, classes=classes)

    def test_method_field_and_type(self):
        self.assertEqual(self.target('obj.onCreate(null);', 'onCreate').kind, 'method refs')
        self.assertEqual(self.target('this.Companion;', 'Companion').kind, 'field refs')
        self.assertEqual(self.target('private int count;', 'count').kind, 'field refs')
        target = self.target('class Foo extends android.app.Activity {}', 'Activity')
        self.assertEqual((target.query, target.kind), ('Landroid/app/Activity;', 'type refs'))
        self.assertEqual(self.target('class Foo {}', 'Foo').kind, 'type refs')

    def test_string_and_comments(self):
        self.assertEqual(self.target('load("检查\\n环境");', '检查').query, '检查\n环境')
        self.assertIsNone(self.target('// obj.onCreate();', 'onCreate'))
        self.assertIsNone(self.target('/* obj.onCreate(); */', 'onCreate'))
        self.assertIsNone(self.target('public class Foo {}', 'public'))
        self.assertIsNone(infer_reference('  ', 1))
        self.assertIsNone(infer_reference('obj.foo();\nobj.bar();', 0, 21))

    def test_ambiguous_local_and_utf16(self):
        self.assertIsNone(self.target('return p0;', 'p0').kind)
        text = '"😀"; obj.onCreate();'
        index = text.index('onCreate')
        qt_index = len(text[:index].encode('utf-16-le')) // 2
        self.assertEqual(python_offset(text, qt_index), index)
        self.assertEqual(infer_reference(text, python_offset(text, qt_index)).query, 'onCreate')


@unittest.skipUnless(os.environ.get('ASC_XREF_TEST_APK'), '设置 ASC_XREF_TEST_APK 以验证真实 APK')
class ShortcutIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def wait(self):
        deadline = time.monotonic() + 45
        while self.window.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window.busy)
        QTest.qWait(30)

    def test_key_and_real_queries(self):
        with tempfile.TemporaryDirectory() as temp:
            self.window = w = MainWindow(QSettings(str(Path(temp) / 'settings.ini'), QSettings.IniFormat))
            try:
                w.show(); w.activateWindow()
                w.open_apk(os.environ['ASC_XREF_TEST_APK']); self.wait()
                self.assertTrue(w.loaded)
                key = 'Lcom/yuuki/check_env/MainActivity;'
                w.open_document(key); self.wait()
                editor = w.current_editor()
                original = editor.toPlainText()

                def press_x(word, selected=False):
                    index = original.index(word)
                    cursor = editor.textCursor()
                    cursor.setPosition(len(original[:index].encode('utf-16-le')) // 2)
                    if selected:
                        cursor.setPosition(cursor.position() + len(word), QTextCursor.KeepAnchor)
                    editor.setTextCursor(cursor)
                    editor.setFocus(); QTest.qWait(30)
                    QTest.keyClick(editor, Qt.Key_X)
                    self.wait()

                # A method call name in the real source (not the onCreate$lambda declaration).
                press_x('is_env_abnormal', selected=True)
                self.assertEqual(w.search_kind.currentData(), 'method refs')
                self.assertEqual(w.search_input.text(), 'is_env_abnormal')
                self.assertIn('搜索完成', w.status_label.text())
                self.assertGreater(w.result_model.rowCount(), 0)
                press_x('Companion;')
                self.assertEqual(w.search_kind.currentData(), 'field refs')
                self.assertGreater(w.result_model.rowCount(), 0)
                press_x('"check_env"')
                self.assertEqual(w.search_kind.currentData(), 'string refs')
                self.assertEqual(w.search_input.text(), 'check_env')
                self.assertGreater(w.result_model.rowCount(), 0)
                press_x('MainActivity')
                self.assertEqual(w.search_kind.currentData(), 'type refs')
                self.assertGreater(w.result_model.rowCount(), 0)
                # X must remain ordinary text inside search fields.
                before_rows = list(w.result_model.rows)
                w.search_input.clear(); w.search_input.setFocus()
                QTest.keyClicks(w.search_input, 'x'); QTest.qWait(100)
                self.assertEqual(w.search_input.text(), 'x')
                self.assertFalse(w.busy)
                self.assertEqual(w.result_model.rows, before_rows)
                editor.setFocus()
                QTest.keyClick(editor, Qt.Key_X, Qt.ControlModifier)
                QTest.qWait(60)
                self.assertFalse(w.busy)
                self.assertEqual(editor.toPlainText(), original)
            finally:
                w.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
