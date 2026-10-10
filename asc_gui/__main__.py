import argparse
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from .app import MainWindow


def main():
    parser = argparse.ArgumentParser(description='ASC Workbench — JADX 风格的 Qt 桌面 GUI')
    parser.add_argument('apk', nargs='?', help='可选 APK 路径')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('ASC Workbench')
    app.setStyle('Fusion')
    window = MainWindow()
    window.show()
    if args.apk:
        path = str(Path(args.apk).expanduser().resolve())
        QTimer.singleShot(0, lambda: window.open_apk(path))
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
