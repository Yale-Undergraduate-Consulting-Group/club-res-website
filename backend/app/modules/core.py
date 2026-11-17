"""The platform's own surface: sign-in, members, settings, admin and the
integrations every app shares. Paths here are called from outside the app
(Google's OAuth callback, Slack's signed events) and must never move."""
from app import app_access
from app.migrations import Migration
from app.modules import Job, Module, Route, Setting
from app.routers import activity, admin, alumni, alumni_portal, attachments, auth, invitations, operations, settings, slack, telemetry, workspace
from app.services import costs, slack_commands

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
        Route(app_access.router, "/api/admin/apps", "apps"),
        # Alumni: their own session (not a member's), checked inside the routers;
        # club admins reach the portal's board work through /api/admin/alumni.
        # Each portal router also carries the sign-in and access routes (routers/alumni.py).
        Route(alumni_portal.router, "/api/alumni", "alumni"),
        Route(alumni_portal.admin_router, "/api/admin/alumni", "alumni-admin"),
    ),
    jobs=(
        Job("cost_explorer_daily", costs.refresh_cost_explorer, "cron", {"hour": 8, "minute": 5}),
    ),
    migrations=(
        Migration(1, "app access", app_access.create_tables),
        Migration(2, "Slack feature requests", slack_commands.create_tables),
        Migration(3, "Join sessions (QR sign-up)", invitations.create_join_tables),
        Migration(4, "Alumni sign-in: domains, allow-list, accounts, sign-ins", alumni.create_tables),
        Migration(5, "Two-factor sign-in attempts (limit and single use)", auth.create_two_factor_attempts),
        Migration(6, "Daily Cost Explorer cache", costs.create_tables),
        Migration(7, "Alumni portal: board, news posts and covers, events, directory, board inbox", alumni_portal.create_tables),
    ),
    settings=(
        Setting("JWT_SECRET", "Signs members' sessions", required=True),
        Setting("GOOGLE_CLIENT_ID", "Google sign-in and Connect Gmail", required=True),
        Setting("GOOGLE_CLIENT_SECRET", "Google sign-in and Connect Gmail", required=True),
        Setting("BACKEND_URL", "The site's own address, for sign-in callbacks", required=True),
        Setting("FRONTEND_URL", "The site's own address, for links back to pages and the club logo in email sign-offs", required=True),
        Setting("SLACK_CLIENT_ID", "Connect Slack"),
        Setting("SLACK_CLIENT_SECRET", "Connect Slack"),
        Setting("SLACK_SIGNING_SECRET", "The Slack assistant and /yucg command"),
        Setting("SLACK_BOT_TOKEN", "Slack messages from the club bot"),
        Setting("DOCUMENTS_BUCKET", "Document uploads"),
    ),
)
