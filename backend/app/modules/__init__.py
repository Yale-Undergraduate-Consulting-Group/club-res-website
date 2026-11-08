"""Apps on the platform, and the one place that wires them into the server.

An app is a manifest (``Module``): the routers it serves, the jobs it runs,
and what it does at startup. ``main.py`` mounts whatever ``MODULES`` lists, so
adding an app is a new manifest module plus one line in ``MODULES``; nothing
else in ``main.py`` changes. See docs/PLATFORM.md.

Shared code an app may use - the platform core - stays where it is:
``app.database`` (connections, schema), ``app.auth_deps`` (sign-in, roles),
``app.services.scheduler_lease`` (one job runner across a blue/green
overlap), ``app.services.settings_service``, ``app.services.llm``,
``app.services.gmail_api`` and ``app.services.usage_service``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI

from app.auth_deps import get_current_user

if TYPE_CHECKING:
    from app.migrations import Migration


@dataclass(frozen=True)
class Route:
    """One router mounted at ``prefix``. ``signed_in`` adds the session check
    to every route in it; routers that check access per route (or authenticate
    themselves, like Slack's signed requests) leave it off."""
    router: APIRouter
    prefix: str
    tag: str
    signed_in: bool = False


@dataclass(frozen=True)
class Job:
    """A scheduled job. It runs only in the process holding the scheduler
    lease (blue/green overlap). ``sends_mail`` jobs send or read members'
    mail and are not scheduled in the beta environment."""
    id: str
    run: Callable[[], Awaitable[Any]]
    trigger: str
    schedule: dict[str, Any]
    sends_mail: bool = False
    first_run_after: timedelta | None = None


@dataclass(frozen=True)
class Setting:
    """An environment variable an app reads. ``required`` ones must be set on
    a deployed server (``APP_ENV`` is set by ops/restart-yucg.sh): without them
    it refuses to start, which fails the deploy's health check and keeps the
    previous release serving - in dev before prod. Optional ones each turn
    one feature on; ``purpose`` says which. Admins see which are set, by name
    only (Admin → Apps); values never leave the server."""
    name: str
    purpose: str
    required: bool = False


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    routes: tuple[Route, ...] = ()
    jobs: tuple[Job, ...] = ()
    startup: tuple[Callable[[], Awaitable[Any]], ...] = field(default_factory=tuple)
    #: Schema changes after the frozen baseline (app.database.init_db), in
    #: version order from 1. The rules are in app/migrations.py.
    migrations: tuple[Migration, ...] = ()
    #: The environment variables the app reads that matter to operators.
    settings: tuple[Setting, ...] = ()


def mount(app: FastAPI, modules: list[Module]) -> None:
    """Every app's routers, in manifest order. Every route of an app other
    than core also checks that the signed-in member may use that app
    (app.app_access); the platform's own routes are open to every member."""
    from app.app_access import CORE, require_app

    for module in modules:
        access = [] if module.id == CORE else [Depends(require_app(module.id, module.title))]
        for route in module.routes:
            session = [Depends(get_current_user)] if route.signed_in else []
            app.include_router(route.router, prefix=route.prefix, tags=[route.tag],
                               dependencies=(session + access) or None)


async def start(modules: list[Module]) -> None:
    for module in modules:
        for hook in module.startup:
            await hook()


def missing_settings(modules: list[Module]) -> list[str]:
    """Required settings that are not set (empty counts as not set)."""
    import os

    return [s.name for m in modules for s in m.settings if s.required and not (os.getenv(s.name) or "").strip()]


def check_settings(modules: list[Module]) -> None:
    """On a deployed server (APP_ENV set), refuse to start without a required
    setting. Local development sets no APP_ENV and needs no cloud settings."""
    import os

    if not (os.getenv("APP_ENV") or "").strip():
        return
    missing = missing_settings(modules)
    if missing:
        raise RuntimeError("Required settings are not set: " + ", ".join(missing)
                           + ". Add them to the environment's app secret (docs/PLATFORM.md).")


def schedule(scheduler: Any, modules: list[Module], *, beta: bool) -> None:
    """Every app's jobs, run only by the lease holder; mail jobs skipped in beta."""
    from app.services.scheduler_lease import leader_only

    for module in modules:
        for job in module.jobs:
            if beta and job.sends_mail:
                continue
            extra = ({"next_run_time": datetime.now(timezone.utc) + job.first_run_after}
                     if job.first_run_after else {})
            scheduler.add_job(leader_only(job.run), job.trigger, id=job.id,
                              max_instances=1, coalesce=True, **job.schedule, **extra)


def all_modules() -> list[Module]:
    """The platform's apps, core first. Imported lazily so a manifest's
    routers and services load only when the server is built."""
    from app.modules import core, outreach, projects

    return [core.MODULE, outreach.MODULE, projects.MODULE]
