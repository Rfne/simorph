"""Локальный сервер дашборда: только ``127.0.0.1`` и без сети (T11-08, FR-REP-15).

Panel поднимает Bokeh-сервер на выбранном порту. Адрес всегда локальный:
дашборд — часть настольного приложения, а не сетевой сервис. Ресурсы
встраиваются в страницу, поэтому браузер не обращается к CDN.
"""

from __future__ import annotations

from typing import Any

from simorph.core.dashboard.config import DashboardConfig

__all__ = [
    "LOCAL_ADDRESS",
    "dashboard_url",
    "serve_dashboard",
]

#: Единственный допустимый адрес сервера.
LOCAL_ADDRESS: str = "127.0.0.1"


def dashboard_url(config: DashboardConfig, *, port: int | None = None) -> str:
    return f"http://{LOCAL_ADDRESS}:{int(port or config.port)}/"


def serve_dashboard(
    app: Any,
    config: DashboardConfig,
    *,
    port: int | None = None,
    show: bool | None = None,
    threaded: bool = False,
) -> Any:
    """Поднимает сервер Panel и возвращает его ручку.

    ``threaded=True`` возвращает поток вместо блокировки: так дашборд можно
    запустить из теста или из приложения, не занимая вызывающий поток.
    """
    import panel as pn
    from panel.io.resources import set_resource_mode

    set_resource_mode("inline")
    pn.config.inline = True
    return pn.serve(
        app,
        address=LOCAL_ADDRESS,
        port=int(port or config.port),
        show=config.open_browser if show is None else bool(show),
        title=config.title,
        threaded=threaded,
        verbose=False,
    )
