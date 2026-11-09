"""Outreach: finding companies and people, drafting, campaigns, sending and
tracking. /api/track is the open-tracking pixel already sitting in sent mail
and must never move."""
import os
from datetime import timedelta

from app.migrations import Migration
from app.modules import Job, Module, Route, Setting
from app.routers import analytics, campaigns, contacts, emails, outreach, releases, track, yucg_prospects, yucgoutreach
from app.routers.campaigns import drain_releasing_campaigns
from app.services.catalog_builder import builder_tick
from app.services.company_register import backfill_register_entities, drain_company_register
from app.services.follow_up_job import run_follow_up_sequences
from app.services.gmail_reply_sync import sync_replies_all_senders
from app.services.notification_digest_job import run_notification_digests
from app.services.roster_adjudicate import drain_roster_adjudication
from app.services.roster_email import drain_roster_emails, drain_roster_verification
from app.services.roster_watch import drain_roster_queue, enroll_prospect_companies
from app.services.yucgoutreach_discovery import drain_queued_yucgoutreach_runs, recover_interrupted_yucgoutreach_runs

MODULE = Module(
    id="outreach",
    title="Outreach",
    routes=(
        Route(contacts.router, "/api/contacts", "contacts", signed_in=True),
        Route(emails.router, "/api/emails", "emails", signed_in=True),
        Route(campaigns.router, "/api/campaigns", "campaigns", signed_in=True),
        Route(analytics.router, "/api/analytics", "analytics", signed_in=True),
        Route(outreach.router, "/api/outreach", "outreach", signed_in=True),
        Route(track.router, "/api/track", "track"),
        Route(yucgoutreach.router, "/api/yucgoutreach", "yucgoutreach"),
        Route(yucg_prospects.router, "/api/yucg", "yucg-coordinator"),
        Route(releases.router, "/api/yucg/releases", "releases", signed_in=True),
    ),
    startup=(recover_interrupted_yucgoutreach_runs, enroll_prospect_companies),
    migrations=(
        Migration(1, "campaign contact add and release times", campaigns.add_contact_times),
    ),
    settings=(
        Setting("MONID_API_KEY", "People search and page reading (TinyFish's free tier through Monid)"),
        Setting("FIRECRAWL_URL", "Fallback web search and page reading"),
        Setting("COMPANIES_HOUSE_API_KEY", "UK company officers"),
        Setting("SEC_CONTACT_EMAIL", "SEC filings (EDGAR refuses requests without a contact address)"),
        Setting("VERIFALIA_API_KEY", "Mailbox verification of guessed addresses"),
        Setting("TAVILY_API_KEY", "Optional extra web search"),
        Setting("CATALOG_BUCKET", "The prospect spreadsheet and catalog exports"),
    ),
    jobs=(
        Job("company_discovery_queue", drain_queued_yucgoutreach_runs, "interval", {"seconds": 10}),
        # Catalog builder: off until an admin turns it on. Each tick queues at
        # most one company, and only when no builder run is open and the club is
        # under the builder's share of search and AI budgets.
        Job("catalog_builder", builder_tick, "interval", {"seconds": 60}),
        # Bulk register ingest: one step per pass (ticker list, then a Form D
        # quarter, then sector backfill), so no pass is long or bursty at SEC.
        Job("company_register_ingest", drain_company_register, "interval",
            {"minutes": int(os.getenv("REGISTER_INGEST_MINUTES", "20") or 20)}),
        # Public name / entity key / hide reason for register rows that have none
        # yet (the first deploy of the columns, rows the previous release inserted
        # during a deploy overlap, a bumped DERIVATION_VERSION). It reads only the
        # derived_version index, so a tick with nothing to derive costs nothing;
        # the first tick runs shortly after start rather than a full interval later.
        Job("company_register_derive", backfill_register_entities, "interval", {"minutes": 5},
            first_run_after=timedelta(seconds=30)),
        Job("company_roster_watch", drain_roster_queue, "interval", {"seconds": 60}),
        Job("company_roster_emails", drain_roster_emails, "interval", {"seconds": 300}),
        Job("company_roster_adjudicate", drain_roster_adjudication, "interval", {"minutes": 20}),
        Job("company_roster_verification", drain_roster_verification, "cron", {"hour": 7, "minute": 30}),
        Job("follow_up_sequences", run_follow_up_sequences, "cron", {"hour": 8, "minute": 0}, sends_mail=True),
        Job("notification_digests", run_notification_digests, "cron", {"hour": 8, "minute": 5}, sends_mail=True),
        Job("gmail_reply_sync", sync_replies_all_senders, "cron", {"minute": "*/2"}, sends_mail=True),
        Job("campaign_drain", drain_releasing_campaigns, "cron", {"minute": "*/5"}, sends_mail=True),
    ),
)
