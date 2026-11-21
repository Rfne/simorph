"""Модель дерева проекта (T2-02).

Дерево строится из документа обобщённо: корень — проект, шесть групп —
разделы, дальше сущности и их поля. Путь узла совпадает с путём pydantic
(``agents.0.sensors.1.id``), поэтому замечания валидатора ложатся на узлы
без пересчёта, а переход по ошибке — это поиск узла по пути.

Бейдж — счётчик ошибок в поддереве: ошибка на поле поднимается до группы,
поэтому свёрнутая ветка всё равно показывает проблему. Фильтр оставляет
узел, если совпал он сам или кто-то из потомков.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtGui import QColor, QFont

from simorph.core.issues import Issue, Severity
from simorph.core.models.project import ProjectDocument
from simorph.ui.icons import GROUP_COLORS, letter_icon

__all__ = [
    "FIELD_TITLES",
    "SECTION_FIELDS",
    "NodeKind",
    "ProjectTreeModel",
    "TreeNode",
    "build_tree",
]

#: Роли данных.
PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 1
KIND_ROLE = PATH_ROLE + 1

#: Индекс, каким его может передать Qt.
IndexLike = QModelIndex | QPersistentModelIndex


class NodeKind(StrEnum):
    """Тип узла дерева."""

    ROOT = "root"
    GROUP = "group"
    ENTITY = "entity"
    FIELD = "field"


#: Шесть групп дерева: раздел документа и его заголовок.
SECTION_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("profile", "Профиль", "П"),
    ("world", "Мир", "М"),
    ("agents", "Агенты", "А"),
    ("exercises", "Упражнения", "У"),
    ("pipelines", "Конвейеры", "К"),
    ("reports", "Отчёты", "О"),
)

#: Русские подписи полей; неизвестное поле показывается своим именем.
FIELD_TITLES: dict[str, str] = {
    "schema_version": "версия схемы",
    "project_id": "идентификатор проекта",
    "name": "название",
    "created_at": "создан",
    "updated_at": "изменён",
    "transport": "транспорт",
    "settings": "настройки проекта",
    "mode": "режим",
    "domain_id": "домен",
    "theme": "тема",
    "language": "язык",
    "offline": "офлайн",
    "customer": "заказчик",
    "parameters": "параметры",
    "value": "значение",
    "source": "источник",
    "klass": "класс",
    "world_id": "мир",
    "seed": "seed",
    "terrain": "рельеф",
    "size_m": "размер, м",
    "elevation_m": "перепад высот, м",
    "mesh": "меш",
    "zones": "зоны",
    "polygon": "полигон",
    "kind": "вид",
    "buildings": "здания",
    "zone": "зона",
    "footprint": "пятно застройки",
    "floors": "этажей",
    "height_m": "высота, м",
    "equipment": "оборудование",
    "pose": "поза",
    "trees": "деревья",
    "water": "водоёмы",
    "lighting": "освещение",
    "sun_azimuth_deg": "азимут солнца, °",
    "sun_elevation_deg": "высота солнца, °",
    "cloudiness": "облачность",
    "wind": "ветер",
    "speed_mps": "скорость, м/с",
    "direction_deg": "направление, °",
    "gust_model": "модель порывов",
    "agent_id": "агент",
    "role": "роль",
    "links": "звенья",
    "joints": "суставы",
    "sensors": "сенсоры",
    "actuators": "актуаторы",
    "links_comm": "каналы связи",
    "type": "тип",
    "mass_kg": "масса, кг",
    "parent": "родитель",
    "child": "потомок",
    "axis": "ось",
    "link": "звено",
    "rate_hz": "частота, Гц",
    "resolution": "разрешение",
    "bit_depth": "глубина цвета, бит",
    "baseline_m": "база, м",
    "fov_deg": "угол обзора, °",
    "range_m": "дальность, м",
    "noise": "шум",
    "latency_ms": "задержка, мс",
    "joint": "сустав",
    "max_speed_rad_s": "макс. скорость, рад/с",
    "max_speed_mps": "макс. скорость, м/с",
    "max_torque_nm": "макс. момент, Н·м",
    "max_force_n": "макс. усилие, Н",
    "range_deg": "диапазон, °",
    "response_ms": "отклик, мс",
    "channels": "каналы",
    "bandwidth_mbps": "полоса, Мбит/с",
    "loss": "потери",
    "exercise_id": "упражнение",
    "missions": "миссии",
    "mission_id": "миссия",
    "elements": "элементы",
    "next": "далее",
    "sensor": "сенсор",
    "duration_s": "длительность, с",
    "ttl_s": "TTL, с",
    "priority": "приоритет",
    "iterations": "итераций",
    "condition": "условие",
    "pipeline_id": "конвейер",
    "nodes": "узлы",
    "edges": "связи",
    "params": "параметры",
    "from": "откуда",
    "to": "куда",
    "report_id": "отчёт",
    "template": "шаблон",
    "pipeline_ids": "конвейеры",
    "formats": "форматы",
}

#: Поля, значение которых — сущность или список сущностей.
ENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "profile",
        "world",
        "agents",
        "exercises",
        "pipelines",
        "reports",
        "parameters",
        "zones",
        "buildings",
        "equipment",
        "trees",
        "water",
        "links",
        "joints",
        "sensors",
        "actuators",
        "links_comm",
        "missions",
        "elements",
        "nodes",
        "edges",
        "terrain",
        "lighting",
        "wind",
        "transport",
        "settings",
        "customer",
    }
)

#: Поля-идентификаторы: по ним подписывается сущность в дереве.
_ID_FIELDS: tuple[str, ...] = (
    "id",
    "agent_id",
    "exercise_id",
    "pipeline_id",
    "report_id",
    "world_id",
    "mission_id",
    "project_id",
)


@dataclass
class TreeNode:
    """Узел дерева проекта."""

    label: str
    kind: NodeKind
    path: str
    value_text: str = ""
    section: str = ""
    icon_letter: str = ""
    errors: int = 0
    warnings: int = 0
    children: list[TreeNode] = field(default_factory=list)
    parent: TreeNode | None = None


def _field_key(name: str, model: type[BaseModel]) -> str:
    """Имя поля в JSON: алиас, если он задан (``class`` вместо ``klass``)."""
    info = model.model_fields.get(name)
    alias = getattr(info, "alias", None) if info is not None else None
    return str(alias or name)


def _ident(item: Any, index: int) -> str:
    """Подпись элемента списка: его id, иначе тип, иначе номер."""
    if isinstance(item, BaseModel):
        for key in _ID_FIELDS:
            value = getattr(item, key, None)
            if value:
                return str(value)
        for key in ("type", "kind", "klass", "name"):
            value = getattr(item, key, None)
            if value:
                return f"#{index} {value}"
    return f"#{index}"


def _describe(value: Any) -> str:
    """Короткое текстовое представление значения для второй колонки."""
    if isinstance(value, BaseModel):
        return value.__class__.__name__
    if isinstance(value, (list, tuple)):
        return f"{len(value)} эл."
    if isinstance(value, bool):
        return "да" if value else "нет"
    if value is None:
        return "—"
    return str(value)


def _build(value: Any, *, label: str, path: str, kind: NodeKind, section: str) -> TreeNode:
    """Строит поддерево для значения любого типа."""
    node = TreeNode(label=label, kind=kind, path=path, section=section)
    if isinstance(value, BaseModel):
        model = type(value)
        for name in model.model_fields:
            key = _field_key(name, model)
            child_path = f"{path}.{key}" if path else key
            child_kind = NodeKind.ENTITY if key in ENTITY_FIELDS else NodeKind.FIELD
            node.children.append(
                _build(
                    getattr(value, name),
                    label=FIELD_TITLES.get(key, key),
                    path=child_path,
                    kind=child_kind,
                    section=section,
                )
            )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            node.children.append(
                _build(
                    item,
                    label=_ident(item, index),
                    path=f"{path}.{index}",
                    kind=NodeKind.ENTITY,
                    section=section,
                )
            )
    else:
        node.value_text = _describe(value)
    return node


def _link(node: TreeNode) -> None:
    """Проставляет ссылки на родителей."""
    for child in node.children:
        child.parent = node
        _link(child)


def build_tree(document: ProjectDocument) -> TreeNode:
    """Строит корень дерева: проект и шесть групп разделов."""
    root = TreeNode(label=document.name, kind=NodeKind.ROOT, path="", section="project")
    for name, title, letter in SECTION_FIELDS:
        group = _build(
            getattr(document, name),
            label=title,
            path=name,
            kind=NodeKind.GROUP,
            section=name,
        )
        group.icon_letter = letter
        root.children.append(group)
    _link(root)
    return root


class ProjectTreeModel(QAbstractItemModel):
    """Дерево проекта для ``QTreeView``."""

    #: Заголовки колонок.
    HEADERS: tuple[str, str] = ("Объект", "Значение")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root = TreeNode(label="", kind=NodeKind.ROOT, path="")
        self._by_path: dict[str, TreeNode] = {}
        self._filter = ""
        self._visible: dict[int, list[TreeNode]] = {}
        self._matches_cache: dict[int, bool] = {}

    # --- наполнение -----------------------------------------------------

    def set_document(self, document: ProjectDocument) -> None:
        """Перестраивает дерево из документа."""
        self.beginResetModel()
        self._root = build_tree(document)
        self._by_path = {node.path: node for node in self.walk()}
        self._visible.clear()
        self._matches_cache.clear()
        self.endResetModel()

    def set_issues(self, issues: list[Issue]) -> None:
        """Раскладывает замечания по узлам и поднимает счётчики к корню."""
        self.beginResetModel()
        nodes = list(self.walk())
        for node in nodes:
            node.errors = 0
            node.warnings = 0
        for item in issues:
            target = self.node_for_path(item.path)
            if target is None:
                continue
            if item.severity is Severity.ERROR:
                target.errors += 1
            else:
                target.warnings += 1
        for node in reversed(nodes):
            if node.parent is not None:
                node.parent.errors += node.errors
                node.parent.warnings += node.warnings
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        """Показывает узлы, совпавшие с фильтром, и их предков."""
        self.beginResetModel()
        self._filter = text.strip().casefold()
        self._visible.clear()
        self._matches_cache.clear()
        self.endResetModel()

    @property
    def filter_text(self) -> str:
        return self._filter

    # --- поиск узлов ----------------------------------------------------

    def walk(self) -> list[TreeNode]:
        """Все узлы в порядке обхода в глубину."""
        result: list[TreeNode] = []
        stack = [self._root]
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(reversed(node.children))
        return result

    def node_for_path(self, path: str) -> TreeNode | None:
        """Узел по точному пути или ближайший существующий предок."""
        node = self._by_path.get(path)
        if node is not None:
            return node
        parts = path.split(".")
        while parts:
            parts.pop()
            candidate = self._by_path.get(".".join(parts))
            if candidate is not None:
                return candidate
        return None

    def index_for_path(self, path: str) -> QModelIndex:
        """Индекс узла по пути; невалидный индекс, если пути нет."""
        node = self._by_path.get(path)
        if node is None:
            return QModelIndex()
        return self._index_for_node(node)

    def _index_for_node(self, node: TreeNode) -> QModelIndex:
        chain: list[TreeNode] = []
        current: TreeNode | None = node
        while current is not None and current is not self._root:
            chain.append(current)
            current = current.parent
        index = QModelIndex()
        for item in reversed(chain):
            parent = item.parent
            if parent is None:
                return QModelIndex()
            siblings = self._children(parent)
            try:
                row = siblings.index(item)
            except ValueError:
                return QModelIndex()
            index = self.index(row, 0, index if index.isValid() else QModelIndex())
        return index

    # --- фильтрация -----------------------------------------------------

    def _matches(self, node: TreeNode) -> bool:
        if not self._filter:
            return True
        haystack = (node.label, node.path, node.value_text)
        return any(self._filter in part.casefold() for part in haystack)

    def _subtree_matches(self, node: TreeNode) -> bool:
        key = id(node)
        cached = self._matches_cache.get(key)
        if cached is not None:
            return cached
        result = self._matches(node) or any(self._subtree_matches(child) for child in node.children)
        self._matches_cache[key] = result
        return result

    def _children(self, node: TreeNode) -> list[TreeNode]:
        if not self._filter:
            return node.children
        key = id(node)
        cached = self._visible.get(key)
        if cached is None:
            cached = [child for child in node.children if self._subtree_matches(child)]
            self._visible[key] = cached
        return cached

    # --- QAbstractItemModel ---------------------------------------------

    def _node(self, index: IndexLike | None) -> TreeNode | None:
        """Узел из индекса Qt; ``None`` для невалидного индекса."""
        if index is None or not index.isValid():
            return None
        pointer = index.internalPointer()
        return pointer if isinstance(pointer, TreeNode) else None

    def rowCount(self, parent: IndexLike | None = None) -> int:
        if parent is None or not parent.isValid():
            return len(self._children(self._root))
        node = self._node(parent)
        return 0 if node is None else len(self._children(node))

    def columnCount(self, parent: IndexLike | None = None) -> int:
        return len(self.HEADERS)

    def index(
        self,
        row: int,
        column: int,
        parent: IndexLike | None = None,
    ) -> QModelIndex:
        parent_node = self._root
        if parent is not None and parent.isValid():
            candidate = self._node(parent)
            if candidate is None:
                return QModelIndex()
            parent_node = candidate
        children = self._children(parent_node)
        if not 0 <= row < len(children) or not 0 <= column < len(self.HEADERS):
            return QModelIndex()
        return self.createIndex(row, column, children[row])

    def parent(self, index: IndexLike | None = None) -> QModelIndex:  # type: ignore[override]
        node = self._node(index)
        if node is None or node.parent is None or node.parent is self._root:
            return QModelIndex()
        return self._index_for_node(node.parent)

    def data(
        self,
        index: IndexLike | None,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        node = self._node(index)
        if node is None or index is None:
            return None

        if role == int(Qt.ItemDataRole.DisplayRole):
            if index.column() == 1:
                return node.value_text
            return self._label_with_badge(node)
        if role == int(Qt.ItemDataRole.DecorationRole) and index.column() == 0:
            return self._icon_for(node)
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return self._tooltip(node)
        if role == int(Qt.ItemDataRole.ForegroundRole):
            if node.errors:
                return QColor("#e5534b")
            if node.warnings:
                return QColor("#c69026")
            return None
        if role == int(Qt.ItemDataRole.FontRole) and node.kind in (NodeKind.ROOT, NodeKind.GROUP):
            font = QFont()
            font.setBold(True)
            return font
        if role == PATH_ROLE:
            return node.path
        if role == KIND_ROLE:
            return str(node.kind)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if role != int(Qt.ItemDataRole.DisplayRole):
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def flags(self, index: IndexLike | None) -> Qt.ItemFlag:
        if index is None or not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # --- оформление -----------------------------------------------------

    def _label_with_badge(self, node: TreeNode) -> str:
        if node.errors and node.warnings:
            return f"{node.label}  ⚠{node.errors} !{node.warnings}"
        if node.errors:
            return f"{node.label}  ⚠{node.errors}"
        if node.warnings:
            return f"{node.label}  !{node.warnings}"
        return node.label

    def _tooltip(self, node: TreeNode) -> str:
        lines = [node.label, f"путь: {node.path or '<корень>'}"]
        if node.errors:
            lines.append(f"ошибок: {node.errors}")
        if node.warnings:
            lines.append(f"предупреждений: {node.warnings}")
        return "\n".join(lines)

    def _icon_for(self, node: TreeNode) -> Any:
        if node.kind is NodeKind.ROOT:
            return letter_icon("S", GROUP_COLORS["project"])
        if node.kind is NodeKind.GROUP:
            letter = getattr(node, "icon_letter", "•")
            return letter_icon(str(letter), GROUP_COLORS.get(node.section, GROUP_COLORS["entity"]))
        if node.kind is NodeKind.ENTITY:
            return letter_icon("•", GROUP_COLORS["entity"], 10)
        return None
