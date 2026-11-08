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
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI

from app.auth_deps import get_current_user


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
class Module:
    id: str
    title: str
    routes: tuple[Route, ...] = ()
    jobs: tuple[Job, ...] = ()
    startup: tuple[Callable[[], Awaitable[Any]], ...] = field(default_factory=tuple)


def mount(app: FastAPI, modules: list[Module]) -> None:
    """Every app's routers, in manifest order."""
    signed_in = [Depends(get_current_user)]
    for module in modules:
        for route in module.routes:
            app.include_router(route.router, prefix=route.prefix, tags=[route.tag],
                               dependencies=signed_in if route.signed_in else None)


async def start(modules: list[Module]) -> None:
    for module in modules:
        for hook in module.startup:
            await hook()


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
