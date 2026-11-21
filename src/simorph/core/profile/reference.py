"""Эталонные профили заказчиков (T3-08).

Три демонстрационных профиля — «АгроФорс», «Татнефть» и «Волга-Кама» —
собраны из одной спецификации (``spec.py``): различаются только значения
параметров. Поэтому изменение маппинга в спеке сразу видно в golden-тестах
всех трёх профилей, а не только одного.

Профиль «Татнефть» помечен как NDA: его объекты попадают под режим
изолированного контура, поэтому экспорт такого профиля разрешён только
в NDA-каталог (T3-06).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from simorph.core.models.profile import ProfileParameter, ProfileSection
from simorph.core.profile.spec import CUSTOMER_CODES, PROFILE_SPEC
from simorph.core.schema_version import CURRENT_VERSION

__all__ = ["CUSTOMERS", "CustomerProfile", "build_reference_profile", "customer_profile"]


@dataclass(frozen=True, slots=True)
class CustomerProfile:
    """Эталонный заказчик: заголовок, признак NDA и значения параметров."""

    code: str
    title: str
    nda: bool
    values: dict[str, Any] = field(default_factory=dict)


_AGROFORCE: Final = CustomerProfile(
    code="agroforce",
    title="АО «АгроФорс»",
    nda=False,
    values={
        "П-01": "АО «АгроФорс», ИНН 2310123456",
        "П-02": "01.11",
        "П-03": "agro",
        "П-04": "seasonal",
        "П-05": "ERP, ТОиР",
        "П-06": 240.0,
        "П-07": "elongated",
        "П-08": (0.0, 35.0),
        "П-09": 3.0,
        "П-10": "поля, элеватор, АБК",
        "П-11": "грунт, щебень",
        "П-12": 6.0,
        "П-13": 140.0,
        "П-14": 12.0,
        "П-15": 60.0,
        "П-16": (1.0, 18.0),
        "П-17": 1.0,
        "П-18": "коррозия, течь",
        "П-19": "as-built",
        "П-20": "силос, элеватор, опора ЛЭП",
        "П-21": "пыль",
        "П-22": "slow",
        "П-23": "коррозия, перегрев",
        "П-26": 120.0,
        "П-27": "плановые",
        "П-28": 25.0,
        "П-29": 3.0,
        "П-32": "влажность, температура",
        "П-37": "ООПТ, ИВП",
        "П-39": "пожар",
        "П-40": "store-and-forward",
        "П-41": "observation",
        "П-43": "1С:ТОиР",
        "П-44": "Modbus",
        "П-46": False,
        "П-49": "3 НРТК + 2 БВС",
        "П-50": "wheel6",
        "П-51": "RGB-камера, IMU, GPS",
        "П-52": 420.0,
        "П-53": "lte",
        "П-54": 2.0,
        "П-55": "за элеватором",
        "П-56": 1.0,
        "П-57": "temperate",
        "П-58": 6.5,
        "П-59": 53.0,
        "П-60": 55.0,
        "П-61": "dust",
        "П-62": "snow",
        "П-63": "low",
        "П-66": "T_react 180 с",
        "П-69": 20.0,
        "П-72": "будни, 7:00–19:00",
        "П-75": 9.0,
    },
)

_TATNEFT: Final = CustomerProfile(
    code="tatneft",
    title="ПАО «Татнефть»",
    nda=True,
    values={
        "П-01": "ПАО «Татнефть», ИНН 1644003838",
        "П-02": "06.10",
        "П-03": "continuous",
        "П-04": "continuous",
        "П-05": "BIM, ERP, ТОиР, СКУД",
        "П-06": 640.0,
        "П-07": "multi",
        "П-08": (0.0, 80.0),
        "П-09": 6.0,
        "П-10": "техзона, резервуарный парк, факел, АБК",
        "П-11": "асфальт, щебень, лестницы",
        "П-12": 48.0,
        "П-13": 25.0,
        "П-14": 60.0,
        "П-15": 420.0,
        "П-16": (2.0, 24.0),
        "П-17": 3.0,
        "П-18": "коррозия, разгерметизация, перегрев",
        "П-19": "BIM as-built",
        "П-20": "факел, градирня, резервуар, колонна",
        "П-21": "газ, взрывоопасная",
        "П-22": "slow",
        "П-23": "утечка, перегрев",
        "П-26": 30.0,
        "П-27": "плановые, реактивные",
        "П-28": 10.0,
        "П-29": 1.5,
        "П-32": "ПДК, температура, радиация",
        "П-37": "ИВП, запретные зоны",
        "П-39": "пожар, утечка",
        "П-40": "store-and-forward",
        "П-41": "corporate",
        "П-43": "SAP PM",
        "П-44": "OPC-UA",
        "П-46": True,
        "П-49": "6 НРТК + 2 БВС",
        "П-50": "tracked",
        "П-51": "стереопара, IMU, GPS, тепловизор",
        "П-52": 640.0,
        "П-53": "lte",
        "П-54": 4.0,
        "П-55": "за резервуарами",
        "П-56": 2.0,
        "П-57": "continental",
        "П-58": 8.0,
        "П-59": 54.0,
        "П-60": 70.0,
        "П-61": "steam",
        "П-62": "snow",
        "П-63": "high",
        "П-66": "T_react 60 с",
        "П-69": 12.0,
        "П-72": "круглосуточно",
        "П-75": 18.0,
    },
)

_VOLGA_KAMA: Final = CustomerProfile(
    code="volga-kama",
    title="АО «Волга-Кама»",
    nda=False,
    values={
        "П-01": "АО «Волга-Кама», ИНН 1650123456",
        "П-02": "30.11",
        "П-03": "discrete",
        "П-04": "shift",
        "П-05": "BIM, ERP",
        "П-06": 320.0,
        "П-07": "compact",
        "П-08": (0.0, 25.0),
        "П-09": 1.0,
        "П-10": "цеха, склад, АБК",
        "П-11": "асфальт, бетон",
        "П-12": 12.0,
        "П-13": 30.0,
        "П-14": 45.0,
        "П-15": 260.0,
        "П-16": (1.0, 16.0),
        "П-17": 2.0,
        "П-18": "трещина, коррозия",
        "П-19": "BIM as-built",
        "П-20": "кран, конвейер, опора ЛЭП",
        "П-21": "нет",
        "П-22": "moving",
        "П-23": "трещина",
        "П-26": 45.0,
        "П-27": "плановые, ручные",
        "П-28": 12.0,
        "П-29": 2.0,
        "П-32": "температура",
        "П-37": "ИВП",
        "П-39": "пожар",
        "П-40": "manual",
        "П-41": "corporate",
        "П-43": "1С:ТОиР",
        "П-44": "MQTT",
        "П-46": False,
        "П-49": "4 НРТК + 3 БВС",
        "П-50": "hybrid",
        "П-51": "стереопара, IMU, GPS, дальномер",
        "П-52": 280.0,
        "П-53": "wifi6",
        "П-54": 3.0,
        "П-55": "между цехами",
        "П-56": 2.0,
        "П-57": "continental",
        "П-58": 4.5,
        "П-59": 56.0,
        "П-60": 45.0,
        "П-61": "steam",
        "П-62": "snow",
        "П-63": "medium",
        "П-66": "T_react 90 с",
        "П-69": 15.0,
        "П-72": "смены, 6:00–22:00",
        "П-75": 15.0,
    },
)

#: Эталонные заказчики по коду.
CUSTOMERS: Final[dict[str, CustomerProfile]] = {
    profile.code: profile for profile in (_AGROFORCE, _TATNEFT, _VOLGA_KAMA)
}


def customer_profile(code: str) -> CustomerProfile:
    """Эталонный заказчик по коду.

    Raises:
        KeyError: код не входит в эталонный набор.
    """
    if code not in CUSTOMERS:
        raise KeyError(f"неизвестный эталонный профиль: {code!r}; доступны: {CUSTOMER_CODES}")
    return CUSTOMERS[code]


def build_reference_profile(code: str) -> ProfileSection:
    """Собирает профиль на 75 параметров из спецификации и значений заказчика."""
    customer = customer_profile(code)
    parameters = [
        ProfileParameter.model_validate(
            {
                "id": spec.id,
                "block": spec.block,
                "value": None
                if spec.kind == "range"
                else customer.values.get(spec.id, spec.default),
                "range": customer.values.get(spec.id, spec.default)
                if spec.kind == "range"
                else None,
                "class": spec.klass,
                "target": spec.target,
                "projection": list(spec.projection),
                "unit": spec.unit,
                "source": spec.source,
            }
        )
        for spec in PROFILE_SPEC
    ]
    return ProfileSection(
        schema_version=CURRENT_VERSION,
        customer=customer.title,
        nda=customer.nda,
        parameters=parameters,
    )
