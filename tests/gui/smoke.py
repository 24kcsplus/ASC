"""Real-APK integration and Qt rendering checks (no mocked backend).

QT_QPA_PLATFORM=offscreen .venv/bin/python tests/gui/smoke.py APK --class-name 'L...;'
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from asc_gui.app import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('apk')
    parser.add_argument('--class-name', required=True)
    parser.add_argument('--output', default='/tmp/asc-gui-smoke')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle('Fusion')
    settings = QSettings(str(output / 'test-settings.ini'), QSettings.IniFormat)
    settings.clear()
    window = MainWindow(settings)
    window.show()
    report = []

    def check(condition, description):
        if not condition:
            raise AssertionError(description + '\n' + window.logs.toPlainText()[-6000:])
        report.append(description)
        print('PASS', description, flush=True)

    def wait():
        deadline = time.monotonic() + 90
        while window.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        check(not window.busy, '后台任务在时限内完成')
        QTest.qWait(50)

    try:
        QTest.qWait(100)
        window.grab().save(str(output / 'welcome.png'))
        window.open_apk(args.apk)
        wait()
        check(window.loaded and args.class_name in window.classes, f'真实 APK 加载：{len(window.classes):,} 个类')
        window.class_filter.setText(args.class_name[1:-1].replace('/', '.'))
        QTest.qWait(250)
        check(window.tree_model.rowCount() >= 1, '类名过滤返回目标类')
        window._open_first_class()
        wait()
        editor = window.current_editor()
        check(editor is not None and 'class ' in editor.toPlainText(), '目标类真实反编译并显示源码')
        check(window.outline.count() > 0, '方法结构导航可用')
        source = editor.toPlainText()
        window.show_find()
        window.find_input.setText('class')
        window.find_text()
        check(editor.textCursor().selectedText().lower() == 'class', '文件内查找定位文本')
        window.find_input.setText('__asc_missing_text_928__')
        window.find_text()
        check(window.find_status.text() == '无匹配', '无匹配查找有明确反馈')
        window.find_bar.hide()
        window.open_document('@manifest')
        wait()
        check(window.current_editor() is not editor and '<manifest' in window.current_editor().toPlainText(), '反编译之后仍可解析二进制 Manifest')
        window.navigate(-1)
        check(window.current_editor() is editor, '后退恢复原代码标签')
        window.navigate(1)
        check(window.tabs.currentWidget().key == '@manifest', '前进恢复 Manifest 标签')
        window.open_document(args.class_name)
        check(window.current_editor().toPlainText() == source, '标签切换保留源码')
        window.search_kind.setCurrentIndex(4)
        window.search_input.setText('onCreate')
        QTest.mouseClick(window.search_button, Qt.LeftButton)
        wait()
        check(len(window.result_model.rows) > 0, '真实方法检索返回结果')
        window.search_kind.setCurrentIndex(2)
        window.search_input.setText('onCreate')
        window.start_search()
        wait()
        check('搜索完成' in window.status_label.text() and len(window.result_model.rows) > 0, '反编译与 Manifest 后仍可搜索方法引用')
        check(all('class_name' in row for row in window.result_model.rows), '引用结果包含可导航类标识')
        window.result_filter.setText('__no_match__')
        check(window.result_proxy.rowCount() == 0, '结果过滤生效')
        window.result_filter.clear()
        window.class_filter.clear()
        QTest.qWait(250)
        root = window.tree_model.item(0)
        source_node = root.child(1)
        node = source_node
        for segment in args.class_name[1:-1].split('/')[:-1]:
            candidates = [node.child(i) for i in range(node.rowCount())]
            node = next((item for item in candidates if item.text() == segment), node)
            window.tree.expand(node.index())
        editor.goto_line(1)
        window.bottom_tabs.setCurrentIndex(0)
        QTest.qWait(100)
        window.grab().save(str(output / 'workbench-dark.png'))
        window.toggle_theme()
        QTest.qWait(100)
        window.grab().save(str(output / 'workbench-light.png'))
        check(editor.toPlainText() == source, '切换主题保留文档内容')
        window.resize(1100, 720)
        QTest.qWait(100)
        window.grab().save(str(output / 'workbench-compact.png'))
        window.toggle_theme()
        # Explicit backend error followed by a successful request verifies recovery.
        window.open_document('Lasc/nonexistent/DefinitelyMissing;')
        wait()
        check('操作失败' in window.status_label.text() and window.loaded, '后端操作失败不会破坏已加载会话')
        window.open_document('@manifest')
        check(window.tabs.currentWidget().key == '@manifest', '失败后已有标签仍然可用')
        # Reload and invalid input must clear obsolete project state.
        invalid = output / 'invalid.apk'
        invalid.write_bytes(b'not an apk')
        window.open_apk(str(invalid))
        wait()
        check(not window.loaded and not window.classes and window.tabs.count() == 0, '无效 APK 清除旧会话并显示错误')
        window.open_apk(args.apk)
        wait()
        check(window.loaded and args.class_name in window.classes, '加载失败后可重新打开有效 APK')
        pid = window.proc.processId()
        window.close()
        app.processEvents()
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        check(not alive, '关闭窗口后分析进程已回收')
        (output / 'test-report.json').write_text(json.dumps({'apk': Path(args.apk).name, 'class': args.class_name, 'passed': report}, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        window.close()


if __name__ == '__main__':
    main()
