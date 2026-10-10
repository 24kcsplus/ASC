from pathlib import Path
from PySide6.QtGui import QColor, QPalette

DARK = dict(bg='#191c23', panel='#20242d', editor='#171a21', raised='#292e39',
            border='#323845', fg='#dce2ec', muted='#8c98ac', accent='#78a6ff',
            selection='#2e466b', keyword='#c49cf4', string='#9dc896', comment='#748398',
            number='#dfb27d', line='#202733', type='#7ecbd1', method='#e2c385', occurrence='#304459', bracket='#496247')
LIGHT = dict(bg='#edf0f5', panel='#f7f8fb', editor='#ffffff', raised='#e5eaf2',
             border='#d6dce7', fg='#283449', muted='#65758d', accent='#285bc1',
             selection='#dbe7ff', keyword='#8551b0', string='#38734d', comment='#7a8596',
             number='#a36329', line='#f0f4fc', type='#147d85', method='#875914', occurrence='#dce9f8', bracket='#cfebcd')


def apply_theme(app, dark=True):
    t = dict(DARK if dark else LIGHT)
    t["close_icon"] = (Path(__file__).parent / "assets" / "close.svg").as_posix()
    p = QPalette()
    for role, key in [(QPalette.Window, 'bg'), (QPalette.WindowText, 'fg'),
                      (QPalette.Base, 'editor'), (QPalette.AlternateBase, 'panel'),
                      (QPalette.Text, 'fg'), (QPalette.Button, 'raised'),
                      (QPalette.ButtonText, 'fg'), (QPalette.Highlight, 'selection'),
                      (QPalette.HighlightedText, 'fg'), (QPalette.ToolTipBase, 'raised'),
                      (QPalette.ToolTipText, 'fg')]:
        p.setColor(role, QColor(t[key]))
    app.setPalette(p)
    app.setStyleSheet('''
    QWidget { color: %(fg)s; font-family: "Noto Sans CJK SC", "Microsoft YaHei", "DejaVu Sans"; font-size: 12px; }
    QMainWindow, QDialog { background: %(bg)s; }
    QMenuBar, QToolBar, QStatusBar { background: %(panel)s; }
    QMenuBar { padding: 4px 10px; border-bottom: 1px solid %(border)s; }
    QMenuBar::item { padding: 5px 10px; }
    QMenuBar::item:selected, QMenu::item:selected { background: %(selection)s; }
    QMenu { background: %(panel)s; border: 1px solid %(border)s; padding: 5px; }
    QMenu::item { padding: 7px 24px; }
    QToolBar { border: 0; border-bottom: 1px solid %(border)s; padding: 7px; spacing: 7px; }
    QToolButton { border: 1px solid transparent; padding: 6px 9px; border-radius: 4px; }
    QToolButton:hover, QPushButton:hover { background: %(selection)s; }
    QToolButton:disabled, QPushButton:disabled { color: %(muted)s; }
    QToolBar::separator { background: %(border)s; width: 1px; margin: 5px; }
    QDockWidget { titlebar-close-icon: url("%(close_icon)s"); }
    QDockWidget::title { background: %(panel)s; padding: 9px 10px; color: %(muted)s; border-bottom: 1px solid %(border)s; }
    QTreeView, QTableView, QListWidget { background: %(panel)s; border: 0; outline: 0; }
    QTreeView::item, QListWidget::item { min-height: 27px; padding: 0 5px; }
    QTreeView::item:selected, QTableView::item:selected, QListWidget::item:selected { background: %(selection)s; }
    QTreeView::item:hover, QListWidget::item:hover { background: %(raised)s; }
    QHeaderView::section { background: %(raised)s; border: 0; border-right: 1px solid %(border)s; padding: 7px; color: %(muted)s; }
    QTabWidget::pane { border: 0; }
    QTabBar { background: %(panel)s; }
    QTabBar::tab { background: %(panel)s; padding: 10px 15px; border-right: 1px solid %(border)s; border-bottom: 2px solid transparent; }
    QTabBar::tab:selected { background: %(editor)s; color: %(accent)s; border-bottom: 2px solid %(accent)s; }
    QTabBar::close-button { image: url("%(close_icon)s"); width: 14px; height: 14px; padding: 2px; border-radius: 3px; }
    QTabBar::close-button:hover { background: %(selection)s; }
    QTabBar::tab:hover { background: %(raised)s; }
    QPlainTextEdit { background: %(editor)s; border: 0; selection-background-color: %(selection)s; }
    QLineEdit, QComboBox, QSpinBox { background: %(editor)s; border: 1px solid %(border)s; border-radius: 4px; padding: 7px; selection-background-color: %(selection)s; }
    QLineEdit:focus, QComboBox:focus { border: 1px solid %(accent)s; }
    QComboBox QAbstractItemView { background: %(panel)s; selection-background-color: %(selection)s; }
    QPushButton { background: %(raised)s; border: 1px solid %(border)s; border-radius: 4px; padding: 8px 15px; }
    QPushButton#primary { background: %(accent)s; color: %(editor)s; font-weight: bold; border: 0; }
    QLabel#muted { color: %(muted)s; }
    QLabel#brand { color: %(accent)s; font-size: 12px; font-weight: bold; letter-spacing: 2px; }
    QLabel#hero { font-size: 32px; font-weight: bold; }
    QLabel#breadcrumb { background: %(editor)s; color: %(muted)s; padding: 9px 14px; border-bottom: 1px solid %(border)s; }
    QFrame#card { background: %(panel)s; border: 1px solid %(border)s; border-radius: 8px; }
    QStatusBar { border-top: 1px solid %(border)s; padding: 3px; color: %(muted)s; }
    QStatusBar::item { border: 0; }
    QProgressBar { border: 0; background: %(raised)s; max-height: 4px; min-height: 4px; }
    QProgressBar::chunk { background: %(accent)s; }
    QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
    QScrollBar::handle:vertical { background: %(border)s; min-height: 30px; border-radius: 4px; }
    QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
    QScrollBar::handle:horizontal { background: %(border)s; min-width: 30px; border-radius: 4px; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QSplitter::handle { background: %(border)s; }
    QToolTip { background: %(raised)s; color: %(fg)s; border: 1px solid %(border)s; padding: 6px; }
    ''' % t)
    return t
