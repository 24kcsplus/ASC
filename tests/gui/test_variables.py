"""Scope-sensitive variable navigation and real Qt double-click regression tests."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from asc_gui.variables import find_variable_definition
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from asc_gui.app import MainWindow


class VariableScopes(unittest.TestCase):
    def resolve(self, source, word, occurrence=-1):
        offset=source.rindex(word) if occurrence==-1 else source.index(word)
        return find_variable_definition(source,offset,'Lp/A;')

    def test_parameter_signature_with_annotations_and_multiline(self):
        source='''class A {
 public void run(
   final int p0,
   @Nullable java.lang.String[] p1) {
   use(p0, p1);
 }
}'''
        for word in ('p0','p1'):
            target=self.resolve(source,word)
            self.assertEqual(target.kind,'parameter')
            self.assertEqual(target.start,source.index(word))
            self.assertEqual(source[target.start:target.end],word)

    def test_local_arrays_generics_and_multiple_declarations(self):
        source='''class A {
 void run() {
   int first = call(1,2), second = 0;
   java.util.List<String> names = make();
   int numbers[] = null;
   use(second, names, numbers);
 }
}'''
        for word in ('second','names','numbers'):
            target=self.resolve(source,word)
            self.assertIsNotNone(target,word)
            self.assertEqual(target.kind,'local')
            self.assertEqual(target.start,source.index(word))

    def test_sibling_blocks_do_not_leak(self):
        source='''class A {
 void run() {
   { int x = 1; use(x); }
   { int x = 2; use(x); }
   use(x);
 }
}'''
        self.assertIsNone(self.resolve(source,'x'))
        second_use=source.index('use(x)',source.index('int x = 2'))+4
        target=find_variable_definition(source,second_use,'Lp/A;')
        self.assertEqual(target.start,source.index('x = 2'))

    def test_other_methods_fields_and_assignment_are_not_declarations(self):
        source='''class A {
 int p0;
 void first(int p0) { use(p0); }
 void second() {
   p0 = 2;
   use(p0);
 }
}'''
        self.assertIsNone(self.resolve(source,'p0'))
        source='''class A {
 void run(int p0) {
   this.p0 = 2;
 }
}'''
        self.assertIsNone(self.resolve(source,'p0'))
        source='class A {\n void run() { use(v0); int v0=2; }\n}'
        self.assertIsNone(find_variable_definition(source,source.index('use(v0)')+4,'Lp/A;'))

    def test_loop_and_catch_scopes(self):
        source='''class A {
 void run() {
   for (int i=0; i<2; i++) { use(i); }
   use(i);
   try { call(); } catch (Exception error) { use(error); }
   use(error);
 }
}'''
        self.assertIsNone(self.resolve(source,'i'))
        self.assertIsNone(self.resolve(source,'error'))
        for word in ('i','error'):
            pos=source.index('use('+word+')')+4
            target=find_variable_definition(source,pos,'Lp/A;')
            self.assertEqual(source[target.start:target.end],word)

    def test_comments_strings_and_lambdas_are_not_misresolved(self):
        source='''class A {
 void run(int value) {
  String text = "😀 value";
  // value
  action = value -> use(value);
 }
}'''
        self.assertIsNone(find_variable_definition(source,source.index('😀 value')+2,'Lp/A;'))
        self.assertIsNone(find_variable_definition(source,source.index('// value')+3,'Lp/A;'))
        self.assertIsNone(self.resolve(source,'value'))


@unittest.skipUnless(os.environ.get('ASC_VARIABLE_APK_DIR'),'设置 ASC_VARIABLE_APK_DIR 以运行真实 APK 测试')
class VariableClicks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def test_real_parameter_local_and_back_history(self):
        root=Path(os.environ['ASC_VARIABLE_APK_DIR'])
        with tempfile.TemporaryDirectory() as tmp:
            w=MainWindow(QSettings(str(Path(tmp)/'settings.ini'),QSettings.IniFormat))
            w.show();w.activateWindow()
            def wait():
                deadline=time.monotonic()+45
                while w.busy and time.monotonic()<deadline:QTest.qWait(10)
                self.assertFalse(w.busy,w.logs.toPlainText())
                QTest.qWait(80)
            try:
                for apk,key,expression,word in [
                    ('check_env.apk','Lcom/yuuki/check_env/MainActivity;','p2.is_env_abnormal','p2'),
                    ('funnyV3.apk','Lcom/yuuki/ukiinjector/MainActivity;','v0_1.getPaddingLeft','v0_1'),
                ]:
                    w.open_apk(str(root/apk));wait();w.open_document(key);wait()
                    editor=w.current_editor();source=editor.toPlainText()
                    use=source.index(expression)
                    expected=find_variable_definition(source,use,key)
                    self.assertIsNotNone(expected)
                    cursor=editor.textCursor();cursor.setPosition(use+1)
                    editor.setTextCursor(cursor);editor.centerCursor();editor.setFocus();QTest.qWait(60)
                    QTest.mouseDClick(editor.viewport(),Qt.LeftButton,pos=editor.cursorRect(cursor).center());wait()
                    self.assertEqual(editor.textCursor().selectionStart(),len(source[:expected.start].encode('utf-16-le'))//2)
                    self.assertEqual(editor.textCursor().selectedText(),word)
                    self.assertEqual(w.tabs.count(),1)
                    self.assertIn('声明',w.status_label.text())
                    self.assertFalse(w.busy)
                    origin=w.history[w.history_index-1].copy()
                    QTest.keyClick(editor,Qt.Key_Left,Qt.AltModifier)
                    self.assertEqual(editor.textCursor().position(),origin['position'])
                    self.assertEqual(editor.verticalScrollBar().value(),origin['vertical'])
                    self.assertEqual(editor.horizontalScrollBar().value(),origin['horizontal'])
                    QTest.keyClick(editor,Qt.Key_Right,Qt.AltModifier)
                    self.assertEqual(editor.textCursor().selectedText(),word)
                    self.assertEqual(editor.toPlainText(),source)
            finally:w.close()


if __name__=='__main__':unittest.main(verbosity=2)
