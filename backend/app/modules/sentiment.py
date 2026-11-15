"""Market sentiment: sentiment from interview transcripts, Reddit and Google reviews"""
from app.migrations import Migration
from app.modules import Job, Module, Route, Setting
from app.routers import sentiment
from app.services import sentiment_runs

MODULE = Module(
    id="sentiment",
    title="Market sentiment",
    routes=(Route(sentiment.router, "/api/sentiment", "sentiment", signed_in=True),),
    jobs=(
        Job("sentiment_stale_runs", sentiment_runs.fail_stale_runs, "interval", {"minutes": 1}),
    ),
    migrations=(
        Migration(1, "Market sentiment runs", sentiment_runs.create_tables),
    ),
    settings=(
        Setting("REDDIT_CLIENT_ID", "Reddit tab: app id of a Reddit script/web app (app-only access)"),
        Setting("REDDIT_CLIENT_SECRET", "Reddit tab: that app's secret"),
        Setting("SERPAPI_KEY", "Google reviews tab: SerpAPI key (paid per search; the tab is off without it)"),
    ),
)
