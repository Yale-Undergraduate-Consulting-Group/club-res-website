"""Club Resources: past projects, slide templates, interview and survey guides and analytics
resources, searchable and access-controlled (docs/product/RESOURCES-PLAN.md)."""
from app.migrations import Migration
from app.modules import Module, Route
from app.routers import resources
from app.services import resources as resource_service

MODULE = Module(
    id="resources",
    title="Resources",
    routes=(Route(resources.router, "/api/resources", "resources", signed_in=True),),
    migrations=(
        Migration(1, "Resources, their files, team and search index", resource_service.create_tables),
    ),
)
