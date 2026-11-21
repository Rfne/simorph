"""Главное окно конфигуратора (T2-01, T2-06).

Окно собирает рабочее место: дерево проекта слева, инспектор справа,
панель валидации и поиск внизу, вкладки редакторов в центре. Все панели
подписаны на один ``DocumentStore``, поэтому правка в инспекторе сразу
видна в дереве, а ошибка валидатора — в бейдже узла.

Жизненный цикл проекта — открытие, сохранение, недавние, автосохранение
и восстановление после падения — вынесен в ``ProjectSession``; окно только
спрашивает пользователя и переключает состояние.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QTreeView,
    QWidget,
)
from shiboken6 import isValid

from simorph.core.errors import ProjectError, StaleLockError
from simorph.core.factory import new_project
from simorph.core.issues import Issue
from simorph.core.models.project import ProjectDocument
from simorph.core.validation import validate_document
from simorph.settings import THEMES, ApplicationSettings
from simorph.ui import theme
from simorph.ui.commands import DocumentStore
from simorph.ui.inspector import InspectorPanel
from simorph.ui.profile_editor import ProfileEditor
from simorph.ui.search_panel import SearchPanel
from simorph.ui.section_editor import SectionEditor
from simorph.ui.session import ProjectSession
from simorph.ui.tabs import EditorTabs
from simorph.ui.tree_model import PATH_ROLE, ProjectTreeModel
from simorph.ui.validation_panel import ValidationPanel

__all__ = ["SECTION_TITLES", "MainWindow"]

#: Заголовки вкладок разделов.
SECTION_TITLES: dict[str, str] = {
    "profile": "Профиль",
    "world": "Мир",
    "agents": "Агенты",
    "exercises": "Упражнения",
    "pipelines": "Конвейеры",
    "reports": "Отчёты",
}

#: Интервал автосохранения, мс.
AUTOSAVE_INTERVAL_MS = 30_000

#: Задержка пересчёта валидации после правки, мс.
VALIDATION_DELAY_MS = 250


class MainWindow(QMainWindow):
    """Главное окно конфигуратора."""

    def __init__(
        self,
        settings: ApplicationSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else ApplicationSettings()
        self._session = ProjectSession(self._settings)
        self._store = DocumentStore(new_project("без имени"))
        self._issues: list[Issue] = []
        self._section_editors: dict[str, QWidget] = {}
        self._current_theme = theme.apply_theme(_app(), self._settings.theme)
        self._confirmations_enabled = True

        self.setWindowTitle("simorph")
        self.resize(1280, 800)

        self._model = ProjectTreeModel(self)
        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSortingEnabled(False)
        self._tree.header().setStretchLastSection(True)

        self._inspector = InspectorPanel(self._store)
        self._validation = ValidationPanel()
        self._search = SearchPanel(self._settings)
        self._tabs = EditorTabs(self._settings)
        self._status_label = QLabel("готово")

        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self.statusBar().addWidget(self._status_label, 1)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start()

        self._validation_timer = QTimer(self)
        self._validation_timer.setSingleShot(True)
        self._validation_timer.setInterval(VALIDATION_DELAY_MS)
        self._validation_timer.timeout.connect(self.validate_project)

        self._connect_signals()
        self._tabs.set_dirty_checker(lambda _key: self._store.is_modified)
        self._bind_document(self._store.document)
        self._restore_layout()
        self._update_title()

    # --- построение интерфейса ------------------------------------------

    def _build_docks(self) -> None:
        self._tree_dock = QDockWidget("Проект", self)
        self._tree_dock.setObjectName("dock_tree")
        self._tree_dock.setWidget(self._tree)
        self._tree_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._tree_dock)

        self._inspector_dock = QDockWidget("Свойства", self)
        self._inspector_dock.setObjectName("dock_inspector")
        self._inspector_dock.setWidget(self._inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._inspector_dock)

        self._validation_dock = QDockWidget("Валидация", self)
        self._validation_dock.setObjectName("dock_validation")
        self._validation_dock.setWidget(self._validation)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._validation_dock)

        self._search_dock = QDockWidget("Поиск", self)
        self._search_dock.setObjectName("dock_search")
        self._search_dock.setWidget(self._search)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._search_dock)
        self._search_dock.hide()

        self.setCentralWidget(self._tabs)
        self.resizeDocks(
            [self._tree_dock, self._inspector_dock],
            [320, 380],
            Qt.Orientation.Horizontal,
        )

    def _build_actions(self) -> None:
        self.action_new = QAction("Новый проект", self)
        self.action_new.setShortcut(QKeySequence.StandardKey.New)
        self.action_new.triggered.connect(self.new_project)

        self.action_open = QAction("Открыть…", self)
        self.action_open.setShortcut(QKeySequence.StandardKey.Open)
        self.action_open.triggered.connect(lambda: self.open_project())

        self.action_save = QAction("Сохранить", self)
        self.action_save.setShortcut(QKeySequence.StandardKey.Save)
        self.action_save.triggered.connect(self.save_project)

        self.action_save_as = QAction("Сохранить как…", self)
        self.action_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.action_save_as.triggered.connect(self.save_project_as)

        self.action_close = QAction("Закрыть проект", self)
        self.action_close.triggered.connect(self.close_project)

        self.action_quit = QAction("Выход", self)
        self.action_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.action_quit.triggered.connect(self.close)

        self.action_undo = QAction("Отменить", self)
        self.action_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.action_undo.triggered.connect(self._store.undo)
        self.action_redo = QAction("Повторить", self)
        self.action_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.action_redo.triggered.connect(self._store.redo)

        self.action_validate = QAction("Проверить проект", self)
        self.action_validate.setShortcut(QKeySequence("F5"))
        self.action_validate.triggered.connect(self.validate_project)

        self.action_search = QAction("Поиск", self)
        self.action_search.setShortcut(QKeySequence.StandardKey.Find)
        self.action_search.triggered.connect(self.toggle_search)

        self._theme_group = QActionGroup(self)
        self._theme_actions: dict[str, QAction] = {}
        for name in THEMES:
            action = QAction(name, self, checkable=True)
            action.setChecked(name == self._settings.theme)
            action.triggered.connect(lambda _checked=False, value=name: self.switch_theme(value))
            self._theme_group.addAction(action)
            self._theme_actions[name] = action

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("Файл")
        file_menu.addAction(self.action_new)
        file_menu.addAction(self.action_open)
        self._recent_menu = file_menu.addMenu("Недавние")
        file_menu.addSeparator()
        file_menu.addAction(self.action_save)
        file_menu.addAction(self.action_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self.action_close)
        file_menu.addAction(self.action_quit)

        edit_menu = menu_bar.addMenu("Правка")
        edit_menu.addAction(self.action_undo)
        edit_menu.addAction(self.action_redo)

        view_menu = menu_bar.addMenu("Вид")
        theme_menu = view_menu.addMenu("Тема")
        for action in self._theme_actions.values():
            theme_menu.addAction(action)
        view_menu.addSeparator()
        view_menu.addAction(self._tree_dock.toggleViewAction())
        view_menu.addAction(self._inspector_dock.toggleViewAction())
        view_menu.addAction(self._validation_dock.toggleViewAction())
        view_menu.addAction(self._search_dock.toggleViewAction())

        project_menu = menu_bar.addMenu("Проект")
        project_menu.addAction(self.action_validate)
        project_menu.addAction(self.action_search)

        self._rebuild_recent_menu()

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Основное")
        toolbar.setObjectName("toolbar_main")
        toolbar.setMovable(False)
        toolbar.addAction(self.action_new)
        toolbar.addAction(self.action_open)
        toolbar.addAction(self.action_save)
        toolbar.addSeparator()
        toolbar.addAction(self.action_undo)
        toolbar.addAction(self.action_redo)
        toolbar.addSeparator()
        toolbar.addAction(self.action_validate)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("фильтр дерева")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setMaximumWidth(240)
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_edit)

    def _connect_signals(self) -> None:
        self._tree.selectionModel().currentChanged.connect(self._on_tree_current_changed)
        self._tree.doubleClicked.connect(self._on_tree_activated)
        self._inspector.navigate_requested.connect(self.navigate_to)
        self._validation.navigate_requested.connect(self.navigate_to)
        self._search.navigate_requested.connect(self.navigate_to)
        self._tabs.editor_closed.connect(self._on_editor_closed)
        self._store.add_listener(self._on_document_changed)
        self._store.stack.canUndoChanged.connect(self.action_undo.setEnabled)
        self._store.stack.canRedoChanged.connect(self.action_redo.setEnabled)
        self._store.stack.undoTextChanged.connect(self._on_history_changed)
        self._store.stack.redoTextChanged.connect(self._on_history_changed)
        self._store.stack.cleanChanged.connect(self._on_clean_changed)
        self.action_undo.setEnabled(False)
        self.action_redo.setEnabled(False)

    def _on_history_changed(self, _text: str = "") -> None:
        """Обновляет заголовок окна при смене истории правок."""
        self._update_title()

    def _on_clean_changed(self, _clean: bool = True) -> None:
        """Обновляет отметку несохранённых изменений."""
        self._update_title()

    # --- документ --------------------------------------------------------

    def _bind_document(self, document: ProjectDocument) -> None:
        self._model.set_document(document)
        self._search.set_document(document)
        self._refresh_editors()
        self._tree.expandToDepth(0)
        self._refresh_issues()
        self._apply_access_mode()
        self._update_title()
        self._update_status()

    def _apply_access_mode(self) -> None:
        """Включает или выключает правку в зависимости от занятости проекта."""
        read_only = self._session.read_only
        self._inspector.set_read_only(read_only)
        self.action_save.setEnabled(not read_only)
        self.action_save_as.setEnabled(not read_only)

    def _on_document_changed(self, document: ProjectDocument) -> None:
        self._model.set_document(document)
        self._search.set_document(document)
        self._refresh_editors()
        self._inspector.refresh()
        self._validation_timer.start()
        self._update_title()
        self._update_status()

    def _refresh_editors(self) -> None:
        """Переставляет корни вкладок после перестройки модели."""
        for index in range(self._tabs.count()):
            widget = self._tabs.widget(index)
            if isinstance(widget, SectionEditor):
                widget.refresh()

    def _refresh_issues(self) -> None:
        self._issues = validate_document(self._store.document)
        self._model.set_issues(self._issues)
        self._validation.set_issues(self._issues)
        self._update_status()

    def validate_project(self) -> list[Issue]:
        """Пересчитывает замечания и раскладывает их по дереву."""
        self._refresh_issues()
        errors = sum(1 for item in self._issues if item.severity == "error")
        self._status_label.setText(
            f"{self._read_only_prefix()}проверка: замечаний {len(self._issues)}, ошибок {errors}"
        )
        return self._issues

    def _read_only_prefix(self) -> str:
        """Пометка статус-бара, если проект занят другим экземпляром."""
        if not self._session.read_only:
            return ""
        holder = self._session.lock_holder
        detail = f" ({holder.description})" if holder is not None else ""
        return f"только чтение{detail} · "

    @property
    def issues(self) -> list[Issue]:
        return list(self._issues)

    @property
    def store(self) -> DocumentStore:
        return self._store

    @property
    def session(self) -> ProjectSession:
        return self._session

    @property
    def confirmations_enabled(self) -> bool:
        """Спрашивать ли подтверждение при потере несохранённых правок."""
        return self._confirmations_enabled

    @confirmations_enabled.setter
    def confirmations_enabled(self, value: bool) -> None:
        """Выключает модальные вопросы: нужно headless-прогонам и тестам."""
        self._confirmations_enabled = bool(value)

    @property
    def tree_model(self) -> ProjectTreeModel:
        return self._model

    @property
    def tree_view(self) -> QTreeView:
        return self._tree

    @property
    def inspector(self) -> InspectorPanel:
        return self._inspector

    @property
    def validation_panel(self) -> ValidationPanel:
        return self._validation

    @property
    def search_panel(self) -> SearchPanel:
        return self._search

    @property
    def tabs(self) -> EditorTabs:
        return self._tabs

    # --- навигация -------------------------------------------------------

    def navigate_to(self, path: str) -> None:
        """Раскрывает путь в дереве и показывает свойства объекта."""
        index = self._model.index_for_path(path)
        if index.isValid():
            parent = index.parent()
            while parent.isValid():
                self._tree.expand(parent)
                parent = parent.parent()
            self._tree.setCurrentIndex(index)
            self._tree.scrollTo(index)
        self._inspector.show_path(path)

    def _on_tree_current_changed(self, current: Any, _previous: Any = None) -> None:
        if current is None or not current.isValid():
            return
        path = current.data(PATH_ROLE)
        if isinstance(path, str):
            self._inspector.show_path(path)

    def _on_tree_activated(self, index: Any) -> None:
        """Двойной клик по группе открывает раздел во вкладке."""
        if index is None or not index.isValid():
            return
        path = index.data(PATH_ROLE)
        if isinstance(path, str) and path in SECTION_TITLES:
            self.open_section(path)

    # --- вкладки редакторов ----------------------------------------------

    def open_section(self, section: str) -> int:
        """Открывает раздел во вкладке редактора."""
        existing = self._tabs.index_of(section)
        if existing >= 0:
            self._tabs.setCurrentIndex(existing)
            return existing
        editor = self._make_section(section)
        return self._tabs.open_editor(section, SECTION_TITLES[section], editor)

    def _section_factories(self) -> dict[str, Any]:
        """Фабрики вкладок для восстановления раскладки."""
        return {
            section: (lambda value=section: (SECTION_TITLES[value], self._make_section(value)))
            for section in SECTION_TITLES
        }

    def _make_section(self, section: str) -> QWidget:
        """Создаёт редактор раздела: профиль — свой, остальные — общий."""
        editor: QWidget
        if section == "profile":
            editor = ProfileEditor(self._store, self._settings)
        else:
            tree_editor = SectionEditor(self._model, section)
            tree_editor.navigate_requested.connect(self.navigate_to)
            editor = tree_editor
        self._section_editors[section] = editor
        return editor

    def _on_editor_closed(self, key: str) -> None:
        """Закрытая вкладка отписывается от документа."""
        editor = self._section_editors.pop(key, None)
        if isinstance(editor, ProfileEditor):
            editor.dispose()

    def restore_layout(self) -> int:
        """Восстанавливает открытые вкладки прошлого сеанса."""
        return self._tabs.restore_layout(self._section_factories())

    def _restore_layout(self) -> None:
        """Восстанавливает раскладку, если в ней есть знакомые разделы."""
        factories = self._section_factories()
        for section in self._settings.layout:
            factory = factories.get(section)
            if factory is None:
                continue
            title, widget = factory()
            self._tabs.open_editor(section, title, widget)

    def _on_filter_changed(self, text: str) -> None:
        self._model.set_filter(text)
        if text.strip():
            self._tree.expandAll()
        else:
            self._tree.expandToDepth(0)

    def toggle_search(self) -> None:
        """Показывает или скрывает панель поиска."""
        visible = not self._search_dock.isVisible()
        self._search_dock.setVisible(visible)
        if visible:
            self._search_dock.raise_()

    # --- жизненный цикл --------------------------------------------------

    def new_project(self, *, confirm: bool = True) -> bool:
        """Создаёт новый проект в памяти."""
        if confirm and not self._confirm_discard():
            return False
        document = new_project("без имени")
        self._session.detach()
        self._store.reset(document)
        self._bind_document(document)
        self._update_title()
        return True

    def open_project(self, path: Path | None = None, *, confirm: bool = True) -> bool:
        """Открывает проект с диска, предлагая восстановление."""
        if confirm and not self._confirm_discard():
            return False
        target = path
        if target is None:
            chosen = QFileDialog.getOpenFileName(
                self,
                "Открыть проект",
                str(self._settings.last_dir or Path.cwd()),
                "Проект simorph (project.json);;JSON (*.json)",
            )
            if not chosen or not chosen[0]:
                return False
            target = Path(chosen[0])

        try:
            opened = self._session.open(Path(target))
        except StaleLockError as exc:
            answer = QMessageBox.question(
                self,
                "Устаревшая блокировка",
                f"{exc}\n\nСнять блокировку и открыть проект для правки?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            try:
                opened = self._session.open(
                    Path(target),
                    takeover_stale=answer == QMessageBox.StandardButton.Yes,
                    read_only=answer != QMessageBox.StandardButton.Yes,
                )
            except ProjectError as retry_error:
                QMessageBox.critical(self, "Не удалось открыть проект", str(retry_error))
                self._status_label.setText(str(retry_error))
                return False
        except ProjectError as exc:
            QMessageBox.critical(self, "Не удалось открыть проект", str(exc))
            self._status_label.setText(str(exc))
            return False

        document = opened.document
        if opened.recovery_available and not opened.read_only:
            answer = QMessageBox.question(
                self,
                "Восстановление",
                "Найдено автосохранение этого проекта. Восстановить несохранённые изменения?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                recovered = self._session.recover()
                if recovered is not None:
                    document = recovered
            self._session.discard_recovery()

        self._store.reset(document)
        self._bind_document(document)
        self._rebuild_recent_menu()
        if opened.read_only:
            holder = opened.lock_holder
            detail = holder.description if holder is not None else "другой экземпляр"
            self._status_label.setText(f"только чтение: проект занят ({detail})")
        elif opened.migrations:
            self._status_label.setText(f"применены миграции: {', '.join(opened.migrations)}")
        return True

    def save_project(self) -> bool:
        """Сохраняет проект; при отсутствии пути спрашивает его."""
        if self._session.read_only:
            self._status_label.setText("проект открыт только для чтения")
            return False
        if self._session.path is None:
            return self.save_project_as()
        return self._save_to(self._session.path)

    def save_project_as(self) -> bool:
        """Сохраняет проект под новым путём."""
        if self._session.read_only:
            self._status_label.setText("проект открыт только для чтения")
            return False
        suggested = self._session.path or (self._settings.last_dir or Path.cwd()) / "project.json"
        chosen = QFileDialog.getSaveFileName(
            self,
            "Сохранить проект",
            str(suggested),
            "Проект simorph (project.json);;JSON (*.json)",
        )
        if not chosen or not chosen[0]:
            return False
        return self._save_to(Path(chosen[0]))

    def _save_to(self, path: Path) -> bool:
        try:
            self._session.save(self._store.document, path=path)
        except ProjectError as exc:
            QMessageBox.critical(self, "Не удалось сохранить проект", str(exc))
            self._status_label.setText(str(exc))
            return False
        self._store.mark_saved()
        self._update_title()
        self._rebuild_recent_menu()
        self._status_label.setText(f"сохранено: {path}")
        return True

    def close_project(self) -> bool:
        """Закрывает проект, освобождая состояние окна и блокировку."""
        if not self._confirm_discard():
            return False
        document = new_project("без имени")
        self._session.detach()
        self._store.reset(document)
        self._bind_document(document)
        self._update_title()
        return True

    def autosave(self) -> None:
        """Пишет автосохранение, если есть несохранённые правки."""
        if self._session.read_only or not self._store.is_modified:
            return
        try:
            self._session.autosave(self._store.document)
        except OSError as exc:  # pragma: no cover - редкий сбой диска
            self._status_label.setText(f"автосохранение не удалось: {exc}")

    def _confirm_discard(self) -> bool:
        """Спрашивает подтверждение, если есть несохранённые правки."""
        if not self._confirmations_enabled:
            return True
        if not self._store.is_modified:
            return True
        answer = QMessageBox.question(
            self,
            "Несохранённые изменения",
            "В проекте есть несохранённые изменения. Сохранить перед продолжением?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return True

    # --- оформление ------------------------------------------------------

    def switch_theme(self, name: str) -> str:
        """Переключает тему без перезапуска приложения."""
        self._current_theme = theme.apply_theme(_app(), name)
        self._settings.theme = name
        action = self._theme_actions.get(name)
        if action is not None:
            action.setChecked(True)
        return self._current_theme

    @property
    def current_theme(self) -> str:
        return self._current_theme

    def _update_title(self) -> None:
        # При разрушении окна стек правок может умереть раньше сигнала.
        if not isValid(self._store.stack):
            return
        mark = " *" if self._store.is_modified else ""
        name = self._session.path.name if self._session.path is not None else "без имени"
        access = " [только чтение]" if self._session.read_only else ""
        self.setWindowTitle(f"simorph — {name}{mark}{access}")
        self.setWindowModified(self._store.is_modified)

    def _update_status(self) -> None:
        errors = sum(1 for item in self._issues if item.severity == "error")
        warnings = len(self._issues) - errors
        self._status_label.setText(
            f"{self._read_only_prefix()}объектов: {len(self._model.walk())}"
            f" · ошибок: {errors} · предупреждений: {warnings}"
        )

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        recent = self._settings.recent_projects
        if not recent:
            placeholder = self._recent_menu.addAction("пусто")
            placeholder.setEnabled(False)
            return
        for path in recent:
            action = self._recent_menu.addAction(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self.open_project(target))
        self._recent_menu.addSeparator()
        clear = self._recent_menu.addAction("Очистить список")
        clear.triggered.connect(self._clear_recent)

    def _clear_recent(self) -> None:
        self._settings.clear_recent()
        self._rebuild_recent_menu()

    # --- закрытие --------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self._session.discard_recovery()
        self._session.close()
        self._tabs.save_layout()
        self._settings.sync()
        super().closeEvent(event)


def _app() -> QApplication:
    """Текущее приложение; создаётся, если окно строят вне ``main``."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])
