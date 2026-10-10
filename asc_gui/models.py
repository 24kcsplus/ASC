from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex


class ResultModel(QAbstractTableModel):
    headers = ('类', '方法 / 字段', '匹配内容', 'DEX')

    def __init__(self):
        super().__init__()
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        row = self.rows[index.row()]
        return (row['class_display'], row['method_text'].split('->', 1)[-1], row['matched_text'], row['dex_name'])[index.column()]

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def replace(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def append(self, rows):
        if rows:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(rows) - 1)
            self.rows.extend(rows)
            self.endInsertRows()
