"""Receiver ownership, no implicit global scan, and actual Qt/DEX navigation."""
from dataclasses import asdict
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from asc_gui.navigation import ClassLookup, navigation_query
from asc_gui.definitions import find_definitions
from asc_gui.variables import find_variable_definition


class Managers(dict):
    def values(self):
        if not getattr(self, 'allow_scan', False):
            raise AssertionError('Unexpected global DEX scan')
        return super().values()


def fixture():
    classes = {}
    types = []
    def clazz(key, methods=(), fields=(), parents=()):
        buf = bytearray(16+4+2*max(0,len(parents)-1))
        super_idx = len(types) if parents else 0xffffffff
        types.extend(parents)
        struct.pack_into('<II', buf, 8, super_idx, 16 if len(parents)>1 else 0)
        if len(parents)>1:
            struct.pack_into('<I', buf, 16, len(parents)-1)
            for index in range(len(parents)-1):
                struct.pack_into('<H', buf, 20+index*2, super_idx+index+1)
        item = NS(fullname=key, _class_def_off=0,
            dex=NS(buf=buf, get_type=lambda index:NS(descriptor=types[index])),
            fields=[NS(name=name,type=NS(descriptor=typ)) for name,typ in fields],
            methods=[NS(name=name,prototype=NS(parameters_type=[NS(descriptor=p) for p in params]),access_flags=0) for name,params in methods])
        classes[key]=item
    clazz('Lp/A;', [('a',[])], [('b','Lp/B;'),('external','Lsdk/External;')], ['Lp/Base;'])
    clazz('Lp/Base;', [('base',[])], [('inherited','Lp/B;')], ['Lp/I;'])
    clazz('Lp/I;', [('iface',[])])
    clazz('Lp/B;', [('a',[]),('a',['I'])], [('child','Lp/C;')])
    clazz('Lp/C;', [('a',[])])
    clazz('Lp/b;', [('a',[])])  # Obfuscated class colliding with a field/local.
    clazz('Lother/B;', [('a',[])])
    clazz('Lp/external;', [('a',[])])
    managers=Managers(main=NS(dex=NS(get_class=classes.get, classes=list(classes.values()))))
    return NS(class_to_dex={key:'main' for key in classes},findref_managers=managers), ClassLookup(classes)


class ReceiverScopeTests(unittest.TestCase):
    def setUp(self):
        self.store,self.lookup=fixture()

    def query(self, expression, prefix=''):
        source='class A {\n void run('+prefix+') {\n '+expression+'\n }\n}'
        return navigation_query(source,source.rindex('.a(')+1 if '.a(' in source else source.index(expression), 'Lp/A;',self.lookup)

    def result(self, expression, prefix=''):
        return find_definitions(self.store, asdict(self.query(expression,prefix)))

    def test_field_types_chains_and_inheritance(self):
        for expression,owner in [('this.b.a();','Lp/B;'),('b.a();','Lp/B;'),
                                 ('this.b.child.a();','Lp/C;'),('inherited.a();','Lp/B;'),
                                 ('super.inherited.a();','Lp/B;')]:
            with self.subTest(expression=expression):
                result=self.result(expression)
                self.assertEqual([c['class_name'] for c in result['candidates']],[owner])
                self.assertFalse(result['uncertain'])
        for name in ['base','iface']:
            result=find_definitions(self.store,{'name':name,'owners':['Lp/A;']})
            self.assertEqual(len(result['candidates']),1)

    def test_visible_variables_win_over_fields_and_obfuscated_classes(self):
        self.assertEqual(self.result('b.a();','p.C b')['owners'],['Lp/C;'])
        self.assertEqual(self.result('p.C b = null; b.a();')['owners'],['Lp/C;'])
        self.assertEqual(self.result('p.C first = null, b = null; b.a();')['owners'],['Lp/C;'])
        self.assertEqual(self.result('{ p.C b = null; b.a(); }\n b.a();')['owners'],['Lp/B;'])
        self.assertEqual(self.result('b.a();','sdk.External b')['candidates'],[])
        self.assertEqual(self.result('external.a();')['owners'],['Lsdk/External;'])
        self.assertEqual(self.result('external.child.a();')['candidates'],[])

    def test_import_package_static_and_no_global_class_aliases(self):
        self.assertEqual(self.result('B.a();')['owners'],['Lp/B;'])
        self.assertEqual(self.result('other.B.a();')['owners'],['Lother/B;'])
        source='import other.B;\nclass A {\n void run(B b) { b.a(); }\n}'
        query=navigation_query(source,source.index('.a(')+1,'Lp/A;',self.lookup)
        self.assertEqual(query.owners,['Lother/B;'])
        lookup=ClassLookup(['Lone/a;','Ltwo/a;'])
        query=navigation_query('a.a();',2,'Lp/A;',lookup)
        self.assertEqual(query.owners,[])

    def test_complex_and_unknown_receivers_never_become_this_or_global(self):
        for expression in ['mystery.a();','factory().a();','factory().child.a();',
                           'items[0].a();','((p.B) value).a();','this.missing.a();']:
            with self.subTest(expression=expression):
                result=self.result(expression)
                self.assertEqual(result['candidates'],[])
                self.assertTrue(result['reason'])
        self.assertEqual(find_definitions(self.store,{'name':'a'})['candidates'],[])
        self.assertEqual(find_definitions(self.store,{'name':'a','owners':['Lsdk/External;']})['candidates'],[])

    def test_explicit_global_search_keeps_chooser_and_scope_overloads(self):
        self.store.findref_managers.allow_scan=True
        result=find_definitions(self.store,{'name':'a','allow_global':True,'owners':['Lp/A;']})
        self.assertGreater(len({c['class_name'] for c in result['candidates']}),3)
        self.assertTrue(result['uncertain'])
        self.assertTrue(result['global_search'])
        result=find_definitions(self.store,{'name':'a','owners':['Lp/B;']})
        self.assertEqual(len(result['candidates']),2)
        self.assertEqual({c['class_name'] for c in result['candidates']},{'Lp/B;'})

    def test_variable_type_annotations_arrays_and_generics(self):
        for declaration,expected in [('final p.B b','p.B'),('@Nullable p.B b','p.B'),
                                     ('p.B b[]','p.B[]'),('p.B<String> b','p.B<String>')]:
            source='class A {\n void run('+declaration+') { b.a(); }\n}'
            variable=find_variable_definition(source,source.index('b.a()'),'Lp/A;')
            self.assertEqual(variable.type_name,expected)
        self.assertEqual(self.result('b.a();','p.B[] b')['candidates'],[])


@unittest.skipUnless(os.environ.get('ASC_RECEIVER_APK_DIR'),'设置 ASC_RECEIVER_APK_DIR 运行真实 APK 测试')
class ActualReceiverNavigation(unittest.TestCase):
    def test_real_field_jump_and_manual_global(self):
        from PySide6.QtCore import QSettings, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from asc_gui.app import MainWindow
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            window=MainWindow(QSettings(str(Path(tmp)/'settings.ini'),QSettings.IniFormat))
            def wait():
                deadline=time.monotonic()+60
                while window.busy and time.monotonic()<deadline: QTest.qWait(10)
                self.assertFalse(window.busy,window.logs.toPlainText())
                QTest.qWait(50)
            try:
                window.show()
                window.open_apk(str(Path(os.environ['ASC_RECEIVER_APK_DIR'])/'funnyV3.apk'));wait()
                key='Lcom/yuuki/ukiinjector/MainActivity;'
                window.open_document(key);wait()
                editor=window.current_editor();source=editor.toPlainText()
                offset=source.index('this.navigator.a(')+len('this.navigator.')
                cursor=editor.textCursor();cursor.setPosition(offset);editor.setTextCursor(cursor)
                editor.centerCursor();editor.setFocus();QTest.qWait(100)
                QTest.keyClick(editor,Qt.Key_F3);wait()
                self.assertEqual(window.tabs.currentWidget().key,'LG;')
                self.assertIn('a(int ',window.current_editor().textCursor().block().text())
                self.assertFalse(window.definition_dialog and window.definition_dialog.isVisible())
                window.navigate(-1)
                self.assertEqual(window.current_editor(),editor)
                window.jump_definition(editor,offset,allow_global=True);wait()
                self.assertTrue(window.definition_dialog.isVisible())
                self.assertIn('全局同名',window.definition_dialog.windowTitle())
                self.assertGreater(window.definition_dialog.entries.count(),1)
                window.definition_dialog.reject()
                # Known Android framework variable must not open unrelated APK methods.
                external=source.index('v0_1.getPaddingLeft(')+len('v0_1.')
                window.jump_definition(editor,external);wait()
                self.assertEqual(window.current_editor(),editor)
                self.assertIn('android.view.View',window.status_label.text())
                self.assertFalse(window.definition_dialog.isVisible())
            finally:
                window.close()


if __name__=='__main__': unittest.main(verbosity=2)
