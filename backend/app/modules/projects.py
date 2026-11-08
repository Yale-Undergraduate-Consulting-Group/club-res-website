"""Projects: the club's engagements, and the citations drafts draw from them."""
from app.modules import Module, Route
from app.routers import projects

MODULE = Module(
    id="projects",
    title="Projects",
    routes=(Route(projects.router, "/api/projects", "projects"),),
)
