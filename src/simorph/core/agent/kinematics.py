"""Кинематическое дерево агента (T5-01, FR-AGT-02, FR-AGT-05).

Дерево строится из звеньев и суставов и никогда не бросает исключений:
сломанная иерархия — это список ошибок, который валидатор превращает
в замечания. Так же устроена сцена мира: сбой одного объекта не отменяет
остальные.

Поза звена складывается из смещений вдоль пути от корня: в схеме поза
звена задана относительно родителя, а не в мировых координатах. Поэтому
``world_pose`` — это сумма ``Link.pose`` по цепочке суставов.

Масса и инерция считаются по геометрии звеньев. Инерция каждого звена
приводится к центру масс агента теоремой Гюйгенса — Штейнера: без этого
шага нельзя проверить «положительную инерцию» осмысленно.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from simorph.core.models.agent import AgentSection, Joint, Link

__all__ = ["KinematicTree", "Vec3", "build_tree", "link_inertia", "link_volume"]

Vec3 = tuple[float, float, float]

#: Ускорение свободного падения, м/с².
GRAVITY: float = 9.80665


def _add(left: Vec3, right: Vec3) -> Vec3:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _length(value: Vec3) -> float:
    return math.sqrt(value[0] ** 2 + value[1] ** 2 + value[2] ** 2)


def link_volume(link: Link) -> float:
    """Объём звена, м³: нужен для оценки плотности и инерции."""
    x, y, z = (max(0.0, value) for value in link.size_m)
    if link.type == "sphere":
        radius = x / 2.0
        return 4.0 / 3.0 * math.pi * radius**3
    if link.type == "cylinder":
        radius = x / 2.0
        return math.pi * radius**2 * z
    return x * y * z


def link_inertia(link: Link) -> Vec3:
    """Главные моменты инерции звена относительно его центра, кг·м²."""
    x, y, z = (max(0.0, value) for value in link.size_m)
    mass = float(link.mass_kg)
    if link.type == "sphere":
        radius = x / 2.0
        value = 0.4 * mass * radius**2
        return (value, value, value)
    if link.type == "cylinder":
        radius = x / 2.0
        axial = 0.5 * mass * radius**2
        radial = mass / 12.0 * (3.0 * radius**2 + z**2)
        return (radial, radial, axial)
    return (
        mass / 12.0 * (y**2 + z**2),
        mass / 12.0 * (x**2 + z**2),
        mass / 12.0 * (x**2 + y**2),
    )


@dataclass(slots=True)
class KinematicTree:
    """Результат сборки дерева: порядок обхода, позы, масса и инерция."""

    links: dict[str, Link] = field(default_factory=dict)
    joints: list[Joint] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    root: str | None = None
    children: dict[str, list[str]] = field(default_factory=dict)
    parents: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """Дерево связно и имеет ровно один корень."""
        return not self.errors and self.root is not None

    def world_pose(self, link_id: str) -> Vec3:
        """Мировая поза звена: сумма смещений от корня."""
        chain = self.path(link_id)
        pose: Vec3 = (0.0, 0.0, 0.0)
        for name in chain:
            pose = _add(pose, self.links[name].pose)
        return pose

    def path(self, link_id: str) -> list[str]:
        """Путь от корня до звена включительно."""
        chain = [link_id]
        seen = {link_id}
        current = link_id
        while current in self.parents:
            current = self.parents[current]
            if current in seen:  # защита от цикла: дальше идти нельзя
                break
            seen.add(current)
            chain.append(current)
        chain.reverse()
        return chain

    def depth(self, link_id: str) -> int:
        return max(0, len(self.path(link_id)) - 1)

    @property
    def total_mass_kg(self) -> float:
        return sum(float(link.mass_kg) for link in self.links.values())

    @property
    def weight_n(self) -> float:
        return self.total_mass_kg * GRAVITY

    @property
    def center_of_mass(self) -> Vec3:
        """Центр масс в мировых координатах."""
        mass = self.total_mass_kg
        if mass <= 0.0:
            return (0.0, 0.0, 0.0)
        total = [0.0, 0.0, 0.0]
        for link_id, link in self.links.items():
            pose = self.world_pose(link_id)
            for index in range(3):
                total[index] += pose[index] * float(link.mass_kg)
        return (total[0] / mass, total[1] / mass, total[2] / mass)

    @property
    def inertia_kg_m2(self) -> Vec3:
        """Моменты инерции агента относительно центра масс."""
        mass = self.total_mass_kg
        if mass <= 0.0:
            return (0.0, 0.0, 0.0)
        center = self.center_of_mass
        total = [0.0, 0.0, 0.0]
        for link_id, link in self.links.items():
            own = link_inertia(link)
            pose = self.world_pose(link_id)
            offset = (pose[0] - center[0], pose[1] - center[1], pose[2] - center[2])
            distance = _length(offset)
            for index in range(3):
                total[index] += own[index] + float(link.mass_kg) * distance**2
        return (total[0], total[1], total[2])

    @property
    def extent_m(self) -> Vec3:
        """Габариты агента по всем звеньям в мировых координатах."""
        if not self.links:
            return (0.0, 0.0, 0.0)
        low = [math.inf, math.inf, math.inf]
        high = [-math.inf, -math.inf, -math.inf]
        for link_id, link in self.links.items():
            pose = self.world_pose(link_id)
            for index in range(3):
                half = max(0.0, link.size_m[index]) / 2.0
                low[index] = min(low[index], pose[index] - half)
                high[index] = max(high[index], pose[index] + half)
        return (high[0] - low[0], high[1] - low[1], high[2] - low[2])

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "links": len(self.links),
            "joints": len(self.joints),
            "mass_kg": round(self.total_mass_kg, 4),
            "weight_n": round(self.weight_n, 3),
            "center_of_mass": [round(value, 4) for value in self.center_of_mass],
            "inertia_kg_m2": [round(value, 6) for value in self.inertia_kg_m2],
            "extent_m": [round(value, 4) for value in self.extent_m],
            "valid": self.valid,
            "errors": list(self.errors),
        }


def build_tree(agent: AgentSection) -> KinematicTree:
    """Собирает дерево звеньев и суставов, накапливая ошибки иерархии."""
    tree = KinematicTree(links={link.id: link for link in agent.links}, joints=list(agent.joints))
    for joint in agent.joints:
        if joint.parent == joint.child:
            tree.errors.append(f"сустав {joint.id!r} связывает звено {joint.parent!r} само с собой")
            continue
        if joint.parent not in tree.links:
            tree.errors.append(f"сустав {joint.id!r}: родитель {joint.parent!r} не найден")
            continue
        if joint.child not in tree.links:
            tree.errors.append(f"сустав {joint.id!r}: потомок {joint.child!r} не найден")
            continue
        if joint.child in tree.parents:
            tree.errors.append(
                f"у звена {joint.child!r} два родителя:"
                f" {tree.parents[joint.child]!r} и {joint.parent!r}"
            )
            continue
        tree.parents[joint.child] = joint.parent
        tree.children.setdefault(joint.parent, []).append(joint.child)

    roots = [link_id for link_id in tree.links if link_id not in tree.parents]
    if not tree.links:
        tree.errors.append("у агента нет звеньев")
    elif len(roots) > 1:
        tree.errors.append(
            "у агента несколько корней: " + ", ".join(sorted(roots)) + " (ожидался один)"
        )
    elif not roots:
        tree.errors.append("в иерархии звеньев есть цикл: корень не найден")
    else:
        tree.root = roots[0]

    tree.order = _traverse(tree, roots[0] if len(roots) == 1 else None)
    if tree.root is not None and len(tree.order) != len(tree.links):
        missing = sorted(set(tree.links) - set(tree.order))
        tree.errors.append("звенья недостижимы от корня: " + ", ".join(missing))
    return tree


def _traverse(tree: KinematicTree, root: str | None) -> list[str]:
    """Обход в глубину с защитой от цикла: порядок детерминирован."""
    if root is None:
        return []
    order: list[str] = []
    seen: set[str] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        order.append(current)
        for child in reversed(tree.children.get(current, [])):
            if child not in seen:
                stack.append(child)
    return order
