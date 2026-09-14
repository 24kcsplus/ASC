"""Navigation, recent-file persistence and highlighting regression tests."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QSettings, Qt, QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from asc_gui.app import MainWindow, ROLE
from asc_gui.navigation import ClassLookup, declarations, matching_declarations, navigation_query, mask_literals
from asc_gui.editor import CodeEditor
from asc_gui.theme import DARK, LIGHT


class NavigationUnitTests(unittest.TestCase):
    def test_source_declarations_not_calls_or_comments(self):
        source = '''class A {
    // public void fake() {}
    public void run(int a) {
        run(1);
        return fake();
    }
    public void run(String a) {}
    public native String nativeCall();
}'''
        rows = declarations(source, 'Lp/A;')
        self.assertEqual([r['signature'] for r in rows], ['run(int)', 'run(String)', 'nativeCall()'])
        target = {'class_name': 'Lp/A;', 'name':'run','params':['java.lang.String']}
        self.assertEqual(matching_declarations(source,target)[0]['line'],7)
        target['params']=['long']
        self.assertEqual(matching_declarations(source,target),[])

    def test_receivers_and_argument_count(self):
        lookup=ClassLookup(['Lp/A;', 'Lp/B;'])
        src='public void run(p.B b) { b.call("x,y", nested(1,2)); }'
        query=navigation_query(src,src.index('call'), 'Lp/A;',lookup)
        self.assertEqual(query.owners,['Lp/B;'])
        self.assertEqual(query.arity,2)
        src='this.run("x");'
        self.assertEqual(navigation_query(src,src.index('run'),'Lp/A;',lookup).arity,1)
        src='mystery.run();'
        self.assertTrue(navigation_query(src,src.index('run'),'Lp/A;',lookup).uncertain)
        self.assertEqual(navigation_query(src,src.index('run'),'Lp/A;',lookup).owners,[])
        src='super.run();'
        self.assertTrue(navigation_query(src,src.index('run'),'Lp/A;',lookup).super_only)

    def test_class_ambiguity_and_imports(self):
        lookup=ClassLookup(['La/Foo;','Lb/Foo;','Lp/Main$Inner;'])
        self.assertEqual(lookup.resolve('Foo'),['La/Foo;','Lb/Foo;'])
        self.assertEqual(lookup.resolve('Foo','import b.Foo;','Lp/Main;'),['Lb/Foo;'])
        self.assertEqual(lookup.resolve('p.Main.Inner'),['Lp/Main$Inner;'])
        self.assertIsNone(navigation_query('// Foo.call()',4,'Lp/Main;',lookup))
        self.assertIsNone(navigation_query('"Foo.call()"',2,'Lp/Main;',lookup))
        lookup=ClassLookup(['Lp/a;','Lp/Main;'])
        self.assertEqual(navigation_query('a();',0,'Lp/Main;',lookup).kind,'method')
        self.assertEqual(navigation_query('new a();',4,'Lp/Main;',lookup).kind,'class')
        src='public void run(p.Main a) { a.run(); }'
        query=navigation_query(src,src.rindex('run'),'Lp/Main;',lookup)
        self.assertEqual(query.owners,['Lp/Main;'])


class QtFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def test_highlighting_preserves_lexical_context_and_themes(self):
        editor=CodeEditor(DARK)
        editor.setPlainText('public class Sample {\n  String msg = "😀 Sample"; // Sample\n  Sample value;\n  void run() { run(); }\n  int n = 0xCAFE;\n}')
        editor.highlighter.rehighlight()
        editor.show();QTest.qWait(120)
        def color(word, occurrence=0):
            source=editor.toPlainText()
            offset=-1
            for _ in range(occurrence+1): offset=source.index(word,offset+1)
            qt_offset=len(source[:offset].encode('utf-16-le'))//2
            block=editor.document().findBlock(qt_offset)
            pos=qt_offset-block.position()
            for fmt in block.layout().formats():
                if fmt.start <= pos < fmt.start+fmt.length:
                    return fmt.format.foreground().color().name()
        self.assertEqual(color('public'),DARK['keyword'])
        self.assertEqual(color('Sample'),DARK['type'])
        self.assertEqual(color('run'),DARK['method'])
        self.assertEqual(color('0xCAFE'),DARK['number'])
        self.assertEqual(color('Sample',1),DARK['string'])
        self.assertEqual(color('Sample',2),DARK['comment'])
        cursor=editor.textCursor();cursor.setPosition(editor.toPlainText().index('Sample'))
        editor.setTextCursor(cursor);editor.highlight_symbols()
        # Only declaration and usage, not the string or the comment.
        self.assertEqual(len(editor.symbol_selections),2)
        self.assertTrue(all(s.cursor.selectedText()=='Sample' for s in editor.symbol_selections))
        editor.highlight_search('run')
        self.assertEqual(len(editor.search_selections),2)
        editor.set_theme(LIGHT)
        self.assertEqual(color('run'),LIGHT['method'])
        self.assertEqual(editor.search_selections[0].format.background().color().name(),LIGHT['occurrence'])
        editor.close()

    def test_recent_persistence_dedup_clear_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path=str(Path(tmp)/'settings.ini')
            first=Path(tmp)/'a'/'same.apk';first.parent.mkdir();first.write_bytes(b'a')
            second=Path(tmp)/'b'/'same.apk';second.parent.mkdir();second.write_bytes(b'b')
            window=MainWindow(QSettings(settings_path,QSettings.IniFormat))
            window._remember_recent(str(first));window._remember_recent(str(second));window._remember_recent(str(first))
            self.assertEqual(window._recent_paths(),[str(first),str(second)])
            self.assertIn(str(first),window.recent_list.item(0).text())
            window.close()
            window=MainWindow(QSettings(settings_path,QSettings.IniFormat))
            self.assertEqual(window.recent_list.count(),2)
            second.unlink();window._refresh_recent()
            self.assertIn('文件已移动或删除',window.recent_list.item(1).text())
            window._open_recent_item(window.recent_list.item(1))
            self.assertIn('文件已移动或删除',window.status_label.text())
            window.clear_recent()
            self.assertEqual(window.recent_list.count(),0)
            self.assertTrue(first.exists())
            window.close()


@unittest.skipUnless(os.environ.get('ASC_NAV_TEST_APK'),'设置 ASC_NAV_TEST_APK 运行真实 APK 测试')
class ActualNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def wait(self):
        deadline=time.monotonic()+60
        while self.window.busy and time.monotonic()<deadline:QTest.qWait(10)
        self.assertFalse(self.window.busy, self.window.logs.toPlainText())
        QTest.qWait(100)

    def test_double_click_class_and_method_with_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.window=w=MainWindow(QSettings(str(Path(tmp)/'settings.ini'),QSettings.IniFormat))
            try:
                w.show();w.activateWindow()
                w.open_apk(os.environ['ASC_NAV_TEST_APK']);self.wait()
                key='Lcom/yuuki/check_env/MainActivity;'
                w.open_document(key);self.wait()
                editor=w.current_editor()
                original=editor.toPlainText()
                def double_click(word):
                    offset=original.index(word)
                    cursor=editor.textCursor()
                    cursor.setPosition(len(original[:offset+2].encode('utf-16-le'))//2)
                    editor.setTextCursor(cursor);editor.centerCursor();editor.setFocus()
                    QTest.qWait(80)
                    point=editor.cursorRect(cursor).center()
                    self.assertTrue(editor.viewport().rect().contains(point))
                    QTest.mouseDClick(editor.viewport(),Qt.LeftButton,pos=point)
                    self.wait()
                double_click('is_env_abnormal')
                self.assertEqual(w.current_editor(),editor)
                self.assertEqual(editor.textCursor().blockNumber()+1,30)
                self.assertIn('native String is_env_abnormal',editor.textCursor().block().text())
                origin=w.history[w.history_index-1].copy()
                QTest.keyClick(editor,Qt.Key_Left,Qt.AltModifier)
                QTest.qWait(80)
                self.assertEqual(editor.textCursor().position(),origin['position'])
                self.assertEqual(editor.verticalScrollBar().value(),origin['vertical'])
                self.assertEqual(editor.horizontalScrollBar().value(),origin['horizontal'])
                QTest.keyClick(editor,Qt.Key_Right,Qt.AltModifier)
                self.assertEqual(editor.textCursor().blockNumber()+1,30)
                double_click('MainActivity$Companion')
                self.assertEqual(w.tabs.currentWidget().key,'Lcom/yuuki/check_env/MainActivity$Companion;')
                w.navigate(-1)
                self.assertEqual(w.current_editor(),editor)
                self.assertEqual(editor.toPlainText(),original)
                # Exact method names must not accidentally pick the $lambda methods.
                from asc_gui.definitions import find_definitions
                from src.asc_client.gui.runtime import GuiDexStore
                store=GuiDexStore(os.environ['ASC_NAV_TEST_APK']);store.load()
                result=find_definitions(store,{'name':'onCreate','owners':[key],'arity':1})
                self.assertEqual([c['name'] for c in result['candidates']],['onCreate'])
                # Unresolved owners get a chooser even if there is only one candidate.
                w.definition_origin=w.capture_location()
                w._definition_results({'candidates':result['candidates'],'total':1,'uncertain':True})
                self.assertTrue(w.definition_dialog.isVisible())
                w.definition_dialog.reject()
                # Actual JVM overloads remain separate choices.
                overload=find_definitions(store,{'name':'checkNotNull','owners':['Lkotlin/jvm/internal/Intrinsics;']})
                self.assertGreater(len(overload['candidates']),1)
                w.definition_origin=w.capture_location()
                w._definition_results(overload)
                self.assertTrue(w.definition_dialog.isVisible())
                picked=overload['candidates'][0]
                entries=w.definition_dialog.entries
                entries.setCurrentRow(0);entries.setFocus()
                QTest.keyClick(entries,Qt.Key_Return);self.wait()
                self.assertEqual(w.tabs.currentWidget().key,'Lkotlin/jvm/internal/Intrinsics;')
                target_rows=matching_declarations(w.current_editor().toPlainText(),picked)
                self.assertEqual(len(target_rows),1)
                self.assertEqual(w.current_editor().textCursor().blockNumber()+1,target_rows[0]['line'])
                # A latest-file entry should open the APK using the UI activation path.
                w.show_home();self.assertGreater(w.recent_list.count(),0)
                w.recent_list.setCurrentRow(0);w.recent_list.setFocus()
                QTest.keyClick(w.recent_list,Qt.Key_Return);self.wait()
                self.assertTrue(w.loaded)
                self.assertEqual(w.path,str(Path(os.environ['ASC_NAV_TEST_APK']).resolve()))
            finally:w.close()


if __name__=='__main__':unittest.main(verbosity=2)
