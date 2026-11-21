"""Создание нового проекта (T1-07, G1-01).

Новый проект сразу валиден: все семь разделов на месте, ссылки между ними
согласованы, поэтому ``project validate`` на свежем файле даёт ноль ошибок.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from simorph.core.models.agent import Actuator, AgentSection, Joint, Link, Sensor
from simorph.core.models.exercise import ExerciseSection, Mission, MissionElement
from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.models.profile import ProfileParameter, ProfileSection
from simorph.core.models.project import ProjectDocument
from simorph.core.models.report import ReportSection
from simorph.core.models.world import Terrain, WorldSection, Zone
from simorph.core.schema_version import CURRENT_VERSION

#: Идентификаторы объектов демо-проекта.
DEMO_WORLD_ID = "W-01"
DEMO_AGENT_ID = "NRTC-01"
DEMO_EXERCISE_ID = "EX-01"
DEMO_MISSION_ID = "M-01"
DEMO_PIPELINE_ID = "PIPE-CMP-01"
DEMO_REPORT_ID = "REP-01"
DEMO_ZONE_ID = "Z-01"


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def build_profile() -> ProfileSection:
    """Минимальный профиль: один параметр класса ``N``.

    Класс ``N`` выбран намеренно: он не проецируется, поэтому свежий проект
    валиден без мира и агента, а полноценный профиль импортируется на этапе 3.
    """
    return ProfileSection(
        schema_version=CURRENT_VERSION,
        customer=None,
        parameters=[
            ProfileParameter.model_validate(
                {
                    "id": "П-01",
                    "block": "A",
                    "value": "demo",
                    "class": "N",
                    "source": "project new",
                }
            )
        ],
    )


def build_world(seed: int) -> WorldSection:
    """Мир из одной технологической зоны."""
    return WorldSection(
        schema_version=CURRENT_VERSION,
        world_id=DEMO_WORLD_ID,
        seed=seed,
        terrain=Terrain(size_m=(2000.0, 2000.0), elevation_m=(0.0, 12.0)),
        zones=[
            Zone(
                id=DEMO_ZONE_ID,
                polygon=[(0.0, 0.0), (500.0, 0.0), (500.0, 400.0), (0.0, 400.0)],
                kind="tech",
            )
        ],
    )


def build_agent() -> AgentSection:
    """Агент-ровер: корпус, ведущее колесо и стереокамера."""
    return AgentSection(
        schema_version=CURRENT_VERSION,
        agent_id=DEMO_AGENT_ID,
        role="rover",
        links=[
            Link(id="base", type="box", size_m=(1.2, 0.8, 0.4), mass_kg=45.0, pose=(0.0, 0.0, 0.2)),
            Link(
                id="wheel_fl",
                type="cylinder",
                size_m=(0.2, 0.2, 0.1),
                mass_kg=2.0,
                pose=(0.4, 0.3, 0.1),
            ),
        ],
        joints=[
            Joint(
                id="wheel_fl_joint",
                parent="base",
                child="wheel_fl",
                type="continuous",
                axis=(0.0, 1.0, 0.0),
            )
        ],
        sensors=[
            Sensor(
                id="cam_stereo",
                type="stereo_camera",
                link="base",
                rate_hz=120.0,
                resolution=(1280, 800),
                bit_depth=8,
                mode="mono",
                baseline_m=0.12,
                fov_deg=90.0,
            )
        ],
        actuators=[
            Actuator(
                id="drive",
                type="drive_axis",
                joint="wheel_fl_joint",
                max_speed_rad_s=20.0,
                max_torque_nm=8.0,
            )
        ],
    )


def build_exercise(seed: int) -> ExerciseSection:
    """Упражнение с одной миссией: старт → возврат на базу."""
    return ExerciseSection(
        schema_version=CURRENT_VERSION,
        exercise_id=DEMO_EXERCISE_ID,
        seed=seed,
        world_id=DEMO_WORLD_ID,
        agents=[DEMO_AGENT_ID],
        missions=[
            Mission(
                mission_id=DEMO_MISSION_ID,
                agent_id=DEMO_AGENT_ID,
                elements=[
                    MissionElement(id="start", type="start", next=["ret-1"]),
                    MissionElement(id="ret-1", type="return_to_base", next=[]),
                ],
            )
        ],
    )


def build_pipeline() -> PipelineSection:
    """Минимальный вычислительный конвейер: источник → приёмник."""
    return PipelineSection(
        schema_version=CURRENT_VERSION,
        pipeline_id=DEMO_PIPELINE_ID,
        kind="computational",
        nodes=[
            PipelineNode(
                id="src", type="source.frame", params={"camera": "cam_stereo", "fps": 120}
            ),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        edges=[PipelineEdge.model_validate({"from": "src.frame", "to": "sink.events"})],
    )


def build_report() -> ReportSection:
    """Отчёт по умолчанию: markdown из шаблона Jinja2."""
    return ReportSection(
        schema_version=CURRENT_VERSION,
        report_id=DEMO_REPORT_ID,
        template="templates/run_report.j2",
        pipeline_ids=[DEMO_PIPELINE_ID],
    )


def new_project(
    name: str,
    *,
    seed: int = 42,
    project_id: UUID | None = None,
    moment: datetime | None = None,
) -> ProjectDocument:
    """Собирает новый проект со всеми семью разделами."""
    created = moment or _now()
    return ProjectDocument(
        schema_version=CURRENT_VERSION,
        project_id=project_id or uuid4(),
        name=name,
        created_at=created,
        updated_at=created,
        profile=build_profile(),
        world=build_world(seed),
        agents=[build_agent()],
        exercises=[build_exercise(seed)],
        pipelines=[build_pipeline()],
        reports=[build_report()],
    )
