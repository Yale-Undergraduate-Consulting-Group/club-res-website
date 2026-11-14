"""Survey builder: draft a client survey from project background and hypotheses, then build it in Qualtrics."""
from app.migrations import Migration
from app.modules import Module, Route, Setting
from app.routers import surveys

MODULE = Module(
    id="surveys",
    title="Survey builder",
    routes=(Route(surveys.router, "/api/surveys", "surveys", signed_in=True),),
    migrations=(
        Migration(1, "Past-survey library", surveys.create_tables),
    ),
    settings=(
        Setting("QUALTRICS_CLIENT_ID", "The club's Qualtrics OAuth client (client credentials; scopes read/write/manage:surveys): past-survey library and Create in Qualtrics"),
        Setting("QUALTRICS_CLIENT_SECRET", "Secret of that Qualtrics client"),
        Setting("QUALTRICS_BASE_URL", "Qualtrics data centre; Yale's (yalesurvey.yul1.qualtrics.com) when unset"),
        Setting("SURVEY_LIBRARY_SKIP", "Comma-separated words or phrases; surveys whose names contain them are never copied into the library or shown to the model"),
    ),
)
