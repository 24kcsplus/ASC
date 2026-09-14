"""Exact owner/name reference regression and real APK coverage."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from asc_gui.references import exact_references
from asc_gui.navigation import ClassLookup
from asc_gui.xref import infer_reference, scoped_reference


class ScopedReferenceUnit(unittest.TestCase):
    def test_obfuscated_names_and_receiver_scopes(self):
        lookup=ClassLookup(['Lp/A;','Lp/B;','Lp/a;'])
        source='class A {\n void run(p.B a) { a.a(); }\n}'
        offset=source.index('.a()')+1
        target=infer_reference(source,offset,classes=lookup.classes)
        self.assertEqual(target.kind,'method refs')
        query=scoped_reference(source,offset,'Lp/A;',lookup,target)
        self.assertEqual(query['owners'],['Lp/B;'])
        target=infer_reference('private int a;',12,classes=lookup.classes)
        self.assertEqual(target.kind,'field refs')
        source='public final class A {}'
        target=infer_reference(source,source.index('A'),classes=lookup.classes)
        self.assertEqual(target.kind,'type refs')
        source='class A {\n void run(p.B a) { use(a); }\n}'
        offset=source.index('use(a)')+4
        target=infer_reference(source,offset,classes=lookup.classes)
        self.assertIsNone(scoped_reference(source,offset,'Lp/A;',lookup,target))

    def test_unknown_owner_never_scans_dex(self):
        class Forbidden(dict):
            def items(self):
                raise AssertionError('unresolved receiver triggered search')
        store=NS(class_to_dex={},findref_managers=Forbidden())
        for owners in [[],['Lp/A;','Lp/B;']]:
            result=exact_references(store,{'kind':'method','name':'a','owners':owners})
            self.assertEqual(result['results'],[])
            self.assertTrue(result['reason'])

    def test_fuzzy_locator_is_filtered_before_instruction_scan(self):
        def member(owner,name):
            return NS(cls=NS(fullname=owner),name=name,prototype=NS(parameters_type=[]),code_offset=1)
        methods=[member('Lp/A;','a'),member('Lp/A;','ab'),member('Lp/B;','a'),member('Lp/Caller;','run')]
        class Scanner:
            def scan(self,query,mark):
                assert query=={'method':{0}},query
                self.mark_idx=[0]
                query['method']=[3]
        scanner=Scanner()
        dex=NS(methods=methods,classes=[NS(methods=methods)])
        manager=NS(dex=dex,_get_method_locator=lambda _:NS(locate=lambda _:set(range(3))),_get_code_scanner=lambda:scanner)
        store=NS(class_to_dex={},findref_managers={'main':manager})
        result=exact_references(store,{'kind':'method','name':'a','owners':['Lp/A;']})
        self.assertEqual(result['total_hits'],1)
        self.assertEqual(result['results'][0]['matched_text'],'Lp/A;->a()')
        self.assertEqual(result['results'][0]['class_name'],'Lp/Caller;')


@unittest.skipUnless(os.environ.get('ASC_RECEIVER_APK_DIR'),'设置 ASC_RECEIVER_APK_DIR 运行真实 APK 测试')
class ActualScopedReferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.asc_client.gui.runtime import GuiDexStore
        cls.path=str(Path(os.environ['ASC_RECEIVER_APK_DIR'])/'funnyV3.apk')
        cls.store=GuiDexStore(cls.path);cls.store.load()

    def test_method_field_and_class_references(self):
        store=self.store; key='Lcom/yuuki/ukiinjector/MainActivity;'
        _,source=store.get_source(key)
        cases=[('this.navigator.a(',len('this.navigator.'),'LG;','method'),
               ('private G navigator',len('private G '),key,'field')]
        for fragment,delta,owner,kind in cases:
            offset=source.index(fragment)+delta
            target=infer_reference(source,offset,classes=store.class_names)
            query=scoped_reference(source,offset,key,ClassLookup(store.class_names),target)
            result=exact_references(store,query)
            self.assertGreater(result['total_hits'],0)
            self.assertEqual(query['kind'],kind)
            self.assertTrue(all(row['matched_owners']==[owner] for row in result['results']))
            if kind=='method':
                self.assertTrue(all(row['matched_text']=='LG;->a(int)' for row in result['results']))
                self.assertTrue(any(row['class_name']!=owner for row in result['results']))
        result=exact_references(store,{'name':'LG;','kind':'type','owners':['LG;']})
        self.assertTrue(all(row['matched_owners']==['LG;'] for row in result['results']))
        self.assertTrue(any('LG;->a(' in row['matched_text'] for row in result['results']))
        self.assertTrue(any(row['class_name']!= 'LG;' for row in result['results']))

    def test_x_key_scoped_results_and_manual_global(self):
        from PySide6.QtCore import QSettings,Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from asc_gui.app import MainWindow
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            w=MainWindow(QSettings(str(Path(tmp)/'settings.ini'),QSettings.IniFormat))
            def wait():
                deadline=time.monotonic()+60
                while w.busy and time.monotonic()<deadline:QTest.qWait(10)
                self.assertFalse(w.busy,w.logs.toPlainText())
                QTest.qWait(50)
            try:
                w.show();w.activateWindow();w.open_apk(self.path);wait()
                w.open_document('Lcom/yuuki/ukiinjector/MainActivity;');wait()
                editor=w.current_editor();source=editor.toPlainText()
                offset=source.index('this.navigator.a(')+len('this.navigator.')
                cursor=editor.textCursor();cursor.setPosition(offset);editor.setTextCursor(cursor);editor.setFocus()
                QTest.keyClick(editor,Qt.Key_X);wait()
                self.assertEqual(w.scope_input.text(),'LG;')
                self.assertEqual(w.result_model.rowCount(),2)
                self.assertTrue(all(row['matched_text']=='LG;->a(int)' for row in w.result_model.rows))
                w.find_xrefs(editor,global_search=True);wait()
                self.assertEqual(w.scope_input.text(),'')
                self.assertGreater(w.result_model.rowCount(),2)
            finally:w.close()


if __name__=='__main__':unittest.main(verbosity=2)
