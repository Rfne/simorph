"""JSON-редактор профиля: подсветка, сворачивание блоков, переход к ошибке (T3-02).

Редактор — обычный ``QPlainTextEdit`` с тремя добавками:

- подсветка синтаксиса JSON через ``QSyntaxHighlighter``;
- номера строк и маркеры сворачивания в поле слева: блоком считается
  объект или массив, начатый на строке; клик по маркеру скрывает тело;
- переход к позиции ошибки: строка подсвечивается, курсор ставится
  в указанную колонку.

Сворачивание работает по видимости блоков документа, поэтому номера строк
остаются «файловыми»: скрытая строка не исчезает из нумерации.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

__all__ = ["JsonEditor", "JsonHighlighter"]

#: Цвета подсветки по тёмной теме; светлая читается за счёт насыщенности.
_KEY_COLOR: Final = "#7aa2f7"
_STRING_COLOR: Final = "#9ece6a"
_NUMBER_COLOR: Final = "#e0af68"
_LITERAL_COLOR: Final = "#bb9af7"
_ERROR_BACKGROUND: Final = QColor("#e5534b")
_ERROR_BACKGROUND.setAlpha(60)
_MARKER_COLOR: Final = QColor("#8f949c")


class JsonHighlighter(QSyntaxHighlighter):
    """Подсветка ключей, строк, чисел и литералов JSON."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._key = _format(_KEY_COLOR, bold=True)
        self._string = _format(_STRING_COLOR)
        self._number = _format(_NUMBER_COLOR)
        self._literal = _format(_LITERAL_COLOR)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - переопределение Qt
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if char == '"':
                end = _string_end(text, index)
                fmt = self._key if _is_key(text, end) else self._string
                self.setFormat(index, end - index, fmt)
                index = end
                continue
            if char.isdigit() or (char == "-" and index + 1 < length and text[index + 1].isdigit()):
                end = index + 1
                while end < length and (text[end].isdigit() or text[end] in ".eE+-"):
                    end += 1
                self.setFormat(index, end - index, self._number)
                index = end
                continue
            for word, fmt in (
                ("true", self._literal),
                ("false", self._literal),
                ("null", self._literal),
            ):
                if text.startswith(word, index):
                    self.setFormat(index, len(word), fmt)
                    index += len(word)
                    break
            else:
                index += 1


def _format(color: str, *, bold: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    return fmt


def _string_end(text: str, start: int) -> int:
    """Индекс сразу за закрывающей кавычкой."""
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    return len(text)


def _is_key(text: str, after: int) -> bool:
    """Строка является ключом, если дальше по строке идёт двоеточие."""
    rest = text[after:].lstrip()
    return rest.startswith(":")


class _Gutter(QWidget):
    """Поле слева: номера строк и маркеры сворачивания."""

    def __init__(self, editor: JsonEditor) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802 - переопределение Qt
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - переопределение Qt
        self._editor.paint_gutter(event)


class JsonEditor(QPlainTextEdit):
    """Текстовый редактор JSON с подсветкой и сворачиванием блоков."""

    #: Ширина маркера сворачивания, px.
    MARKER_WIDTH: Final = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(28.0)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.setFont(font)

        self._gutter = _Gutter(self)
        self._highlighter = JsonHighlighter(self.document())
        self._folds: dict[int, int] = {}
        self._collapsed: set[int] = set()
        self._error_line = -1
        self._error_column = 0

        self.blockCountChanged.connect(lambda _count: self._update_gutter_width())
        self.updateRequest.connect(self._on_update_request)
        self.textChanged.connect(self._recompute_folds)
        self._update_gutter_width()

    # --- поле слева -----------------------------------------------------

    def gutter_width(self) -> int:
        """Ширина поля: номера строк плюс маркеры."""
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 12 + digits * 9 + self.MARKER_WIDTH

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _line_height(self) -> int:
        return int(self.fontMetrics().height())

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - переопределение Qt
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))

    def paint_gutter(self, event: QPaintEvent) -> None:
        """Рисует номера строк и маркеры сворачивания."""
        painter = QPainter(self._gutter)
        try:
            painter.fillRect(event.rect(), self.palette().window())
            painter.setPen(self.palette().text().color())
            block = self.firstVisibleBlock()
            top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            bottom = top + self.blockBoundingRect(block).height()
            width = self._gutter.width()
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible():
                    number = block.blockNumber()
                    painter.drawText(
                        QRect(0, int(top), width - self.MARKER_WIDTH - 4, self._line_height()),
                        Qt.AlignmentFlag.AlignRight,
                        str(number + 1),
                    )
                    if number in self._folds:
                        marker = "+" if number in self._collapsed else "−"
                        painter.setPen(_MARKER_COLOR)
                        painter.drawText(
                            QRect(
                                width - self.MARKER_WIDTH,
                                int(top),
                                self.MARKER_WIDTH,
                                self._line_height(),
                            ),
                            Qt.AlignmentFlag.AlignCenter,
                            marker,
                        )
                        painter.setPen(self.palette().text().color())
                top = bottom
                block = block.next()
                bottom = top + self.blockBoundingRect(block).height()
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - переопределение Qt
        """Клик по маркеру в поле слева сворачивает или разворачивает блок."""
        point = event.position().toPoint()
        if point.x() < self.gutter_width():
            block = self.firstVisibleBlock()
            top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            while block.isValid():
                height = self.blockBoundingRect(block).height()
                if block.isVisible() and top <= point.y() <= top + height:
                    if point.x() >= self.gutter_width() - self.MARKER_WIDTH:
                        self.toggle_fold(block.blockNumber())
                        return
                    break
                top += height
                block = block.next()
        super().mousePressEvent(event)

    # --- сворачивание ---------------------------------------------------

    def fold_ranges(self) -> dict[int, int]:
        """Начальная строка блока → последняя строка блока."""
        return dict(self._folds)

    def _recompute_folds(self) -> None:
        self._folds = _scan_folds(self.toPlainText())
        stale = {start for start in self._collapsed if start not in self._folds}
        self._collapsed -= stale
        self._apply_visibility()
        self._gutter.update()

    def toggle_fold(self, start_line: int) -> None:
        """Сворачивает или разворачивает блок, начатый на строке."""
        if start_line not in self._folds:
            return
        if start_line in self._collapsed:
            self._collapsed.discard(start_line)
        else:
            self._collapsed.add(start_line)
        self._apply_visibility()

    def fold_all(self) -> None:
        """Сворачивает все блоки верхнего уровня."""
        collapsed: set[int] = set()
        for start in sorted(self._folds):
            if any(other < start <= self._folds[other] for other in collapsed):
                continue
            collapsed.add(start)
        self._collapsed = collapsed
        self._apply_visibility()

    def unfold_all(self) -> None:
        """Разворачивает все блоки."""
        self._collapsed.clear()
        self._apply_visibility()

    def collapsed_blocks(self) -> set[int]:
        """Строки свёрнутых блоков (для тестов)."""
        return set(self._collapsed)

    def _apply_visibility(self) -> None:
        document = self.document()
        for number in range(document.blockCount()):
            block = document.findBlockByNumber(number)
            if block.isValid():
                block.setVisible(True)
        for start in sorted(self._collapsed):
            end = self._folds.get(start)
            if end is None:
                continue
            for number in range(start + 1, end + 1):
                block = document.findBlockByNumber(number)
                if block.isValid():
                    block.setVisible(False)
        self._gutter.update()

    # --- ошибки ---------------------------------------------------------

    def goto_line(self, line: int, column: int = 1) -> None:
        """Ставит курсор в строку и колонку (нумерация с единицы)."""
        block = self.document().findBlockByNumber(max(0, line - 1))
        if not block.isValid():
            return
        cursor = self.textCursor()
        cursor.setPosition(block.position() + max(0, column - 1))
        self.setTextCursor(cursor)
        self.centerCursor()

    def highlight_error(self, line: int, column: int = 1) -> None:
        """Подсвечивает строку с ошибкой и ставит туда курсор."""
        self._error_line = line
        self._error_column = column
        selection = QTextEdit.ExtraSelection()
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            cursor = self.textCursor()
            cursor.setPosition(block.position())
            cursor.movePosition(cursor.MoveOperation.EndOfBlock, cursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selection.format.setBackground(_ERROR_BACKGROUND)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            self.setExtraSelections([selection])
        self.goto_line(line, column)

    def clear_error(self) -> None:
        """Снимает подсветку ошибки."""
        if self._error_line < 0:
            return
        self._error_line = -1
        self.setExtraSelections([])


def _scan_folds(text: str) -> dict[int, int]:
    """Строки, где открывается блок, и строки, где он закрывается.

    Сканер учитывает строки и экранирование: скобки внутри строковых
    значений не считаются границами блоков.
    """
    folds: dict[int, int] = {}
    stack: list[int] = []
    line = 0
    in_string = False
    escape = False
    for char in text:
        if char == "\n":
            line += 1
            continue
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(line)
        elif char in "}]" and stack:
            start = stack.pop()
            if line > start:
                folds[start] = line
    return folds
