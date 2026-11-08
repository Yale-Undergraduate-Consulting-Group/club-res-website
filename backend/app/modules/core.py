"""The platform's own surface: sign-in, members, settings, admin and the
integrations every app shares. Paths here are called from outside the app
(Google's OAuth callback, Slack's signed events) and must never move."""
from app.modules import Module, Route
from app.routers import activity, admin, attachments, auth, invitations, operations, settings, slack, telemetry, workspace

MODULE = Module(
    id="core",
    title="Platform",
    routes=(
        Route(activity.router, "/api/activity", "activity"),
        Route(workspace.router, "/api/workspace", "workspace"),
        Route(invitations.router, "/api/admin/invitations", "invitations"),
        Route(settings.router, "/api/settings", "settings"),
        Route(auth.router, "/api/auth", "auth"),
        # Slack signs its own requests and carries no club session, so this
        # door authenticates itself rather than using the session check.
        Route(slack.router, "/api/slack", "slack"),
        Route(admin.router, "/api/admin", "admin"),
        Route(attachments.router, "/api/attachments", "attachments"),
        Route(telemetry.router, "/api/telemetry", "telemetry"),
        Route(operations.router, "/api/admin/operations", "operations"),
    ),
)
