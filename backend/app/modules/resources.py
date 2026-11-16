"""Club Resources: past projects, slide templates, interview and survey guides and analytics
resources, searchable and access-controlled (docs/product/RESOURCES-PLAN.md)."""
from app.migrations import Migration
from app.modules import Module, Route, Setting
from app.routers import resources
from app.services import resources as resource_service

MODULE = Module(
    id="resources",
    title="Resources",
    routes=(Route(resources.router, "/api/resources", "resources", signed_in=True),),
    migrations=(
        Migration(1, "Resources, their files, team and search index", resource_service.create_tables),
        Migration(2, "A SharePoint or Google Drive link per resource", resource_service.add_link_column),
    ),
    settings=(
        Setting("SHAREPOINT_BASE_URL", "Open in SharePoint on Resources: the club's SharePoint address (https://<tenant>.sharepoint.com); without it imported SharePoint links stay hidden"),
    ),
)
