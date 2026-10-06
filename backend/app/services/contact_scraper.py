"""
Contact Scraper & Discovery Engine
Domain crawling, email inference, validation
"""
import re
import asyncio
import hashlib
import os
import httpx
import dns.resolver
from bs4 import BeautifulSoup
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin, urlparse

# Optional (1-based page index, total pages, url) progress hook for UI streaming
PageProgressHook = Optional[Callable[[int, int, str], Awaitable[None]]]

def _is_valid_email_format(email: str) -> bool:
    try:
        from validators import email as v
        return v(email) is True
    except Exception:
        return bool(re.match(r"^[^@]+@[^@]+\.[^@]+$", email or ""))

# Role / generic inboxes — excluded from employee outreach (domain scrape & list filters)
ROLE_EMAIL_PREFIXES = frozenset(
    {
        "info",
        "contact",
        "contacts",
        "contactus",
        "hello",
        "hi",
        "sales",
        "support",
        "help",
        "helpdesk",
        "admin",
        "administrator",
        "hr",
        "careers",
        "jobs",
        "recruiting",
        "recruitment",
        "talent",
        "media",
        "press",
        "office",
        "team",
        "enquiries",
        "inquiries",
        "inquiry",
        "billing",
        "legal",
        "privacy",
        "marketing",
        "newsletter",
        "noreply",
        "no-reply",
        "donotreply",
        "mailer",
        "postmaster",
        "webmaster",
        "customer",
        "customers",
        "customerservice",
        "customercare",
        "service",
        "services",
        "feedback",
        "general",
        "reception",
        "frontdesk",
        "main",
        "corporate",
        "business",
        "procurement",
        "purchasing",
        "finance",
        "accounting",
        "accounts",
        "payables",
        "receivables",
        "admissions",
        "registrar",
        "communications",
        "comm",
        "pr",
        "publicrelations",
        "partnerships",
        "partner",
        "vendors",
        "vendor",
        "donations",
        "donate",
        "fundraising",
        "membership",
        "members",
        "subscribe",
        "subscription",
        "unsubscribe",
        "abuse",
        "security",
        "it",
        "tech",
        "technology",
        "systems",
        "orders",
        "order",
        "shipping",
        "returns",
        "compliance",
        "ethics",
        "investor",
        "investors",
        "ir",
        "mailbox",
        "mail",
        "email",
        "all",
        "everyone",
        "company",
        "firm",
        "group",
        "global",
        "welcome",
        "ask",
        "questions",
        "question",
        "reply",
        "replies",
        "solutions",
        "suggestion",
        "suggestions",
        "booking",
        "bookings",
        "appointments",
        "appointment",
        "reservations",
        "reservation",
        "events",
        "event",
        "training",
        "education",
        "school",
        "admission",
        "library",
        "shop",
        "store",
        "ecommerce",
        "wholesale",
        "retail",
        "site",
        "about",
        "leadership",
        "opportunity",
        "opportunities",
        "product",
        "products",
        "feedback",
        "supply",
        "chain",
        "refurbished",
        "certified",
        "official",
        "homepage",
        "footer",
        "navigation",
        "learn",
        "view",
        "click",
        "download",
        "apps",
        "app",
        "developer",
        "developers",
        "newsroom",
        "news",
        "blog",
        "investor",
        "investors",
        "relations",
        "accessibility",
        "environment",
        "sustainability",
        "renewal",
        "warranty",
        "repair",
        "genius",
        "business",
        "enterprise",
        "education",
        "government",
        "healthcare",
        "retail",
        "partner",
        "partners",
        "affiliate",
        "affiliates",
        "reseller",
        "resellers",
    }
)

BOILERPLATE_NAME_TOKENS = frozenset(
    {
        "about",
        "contact",
        "site",
        "leadership",
        "career",
        "careers",
        "opportunities",
        "opportunity",
        "product",
        "products",
        "feedback",
        "supply",
        "chain",
        "store",
        "shop",
        "refurbished",
        "certified",
        "official",
        "home",
        "homepage",
        "page",
        "footer",
        "menu",
        "navigation",
        "learn",
        "more",
        "read",
        "view",
        "click",
        "here",
        "get",
        "started",
        "support",
        "help",
        "sales",
        "media",
        "press",
        "investor",
        "investors",
        "privacy",
        "terms",
        "legal",
        "jobs",
        "hiring",
        "team",
        "company",
        "corporate",
        "global",
        "worldwide",
        "services",
        "solutions",
        "digital",
        "online",
        "web",
        "email",
        "mail",
        "news",
        "blog",
        "events",
        "event",
        "training",
        "education",
        "apple",
        "store",
        "business",
        "enterprise",
        "developer",
        "developers",
        "download",
        "apps",
        "app",
        "repair",
        "warranty",
        "genius",
        "retail",
        "wholesale",
        "partner",
        "partners",
        "affiliate",
        "affiliates",
        "reseller",
        "resellers",
        "accessibility",
        "environment",
        "sustainability",
        "unknown",
        "contact",
        "general",
        "inquiry",
        "inquiries",
        "department",
        "office",
        "main",
        "headquarters",
        "hq",
    }
)

# Website nav, product, policy, and marketing labels — not person names (any company).
NAV_UI_WORDS = frozenset(
    {
        "gift", "gifts", "card", "cards", "account", "accounts", "manage", "entertainment",
        "carrier", "deal", "deals", "employee", "employees", "equity", "racial", "federal",
        "local", "united", "states", "state", "user", "users", "group", "groups", "setup",
        "personal", "studio", "display", "displays", "compare", "mac", "accessory", "accessories",
        "component", "components", "mesh", "networking", "network", "final", "cut", "logic",
        "pixelmator", "find", "augmented", "reality", "engraving", "upgrade", "program", "programs",
        "wireless", "charger", "chargers", "charging", "watch", "model", "models", "essential",
        "essentials", "heart", "rate", "vision", "speaker", "speakers", "hearing", "health",
        "featured", "discover", "software", "update", "updates", "college", "futures", "former",
        "chair", "northrop", "grumman", "why", "buy", "shop", "store", "cart", "checkout",
        "payment", "finance", "financing", "trade", "refurbished", "certified", "warranty",
        "repair", "genius", "bar", "today", "news", "music", "tv", "movies", "podcasts",
        "books", "arcade", "fitness", "wallet", "icloud", "siri", "homepod", "airpods",
        "iphone", "ipad", "imac", "ipod", "ios", "macos", "watchos", "airtag", "beats",
        "headphones", "headphone", "keyboard", "mouse", "trackpad", "cable", "cables", "adapter",
        "adapters", "case", "cases", "band", "bands", "strap", "straps", "screen", "protector",
        "protectors", "education", "business", "enterprise", "government", "military", "student",
        "students", "teacher", "teachers", "school", "schools", "university", "universities",
        "nonprofit", "nonprofits", "charity", "charities", "donation", "donations", "volunteer",
        "volunteers", "diversity", "inclusion", "accessibility", "environment", "sustainability",
        "climate", "carbon", "neutral", "recycling", "privacy", "security", "safety", "family",
        "parental", "controls", "location", "locations", "finder", "maps", "weather", "stocks",
        "calendar", "reminders", "notes", "mail", "messages", "facetime", "photos", "camera",
        "settings", "preferences", "notification", "notifications", "subscription", "subscriptions",
        "membership", "memberships", "premium", "plus", "pro", "max", "mini", "ultra", "super",
        "turbo", "lite", "free", "trial", "demo", "beta", "preview", "release", "version",
        "download", "downloads", "install", "setup", "guide", "guides", "tutorial", "tutorials",
        "documentation", "docs", "api", "apis", "sdk", "developer", "developers", "partner",
        "partners", "vendor", "vendors", "supplier", "suppliers", "distributor", "distributors",
        "reseller", "resellers", "affiliate", "affiliates", "referral", "referrals", "reward",
        "rewards", "coupon", "coupons", "promo", "promotion", "promotions", "offer", "offers",
        "sale", "sales", "clearance", "outlet", "marketplace", "catalog", "catalogue", "inventory",
        "shipping", "delivery", "returns", "exchange", "refund", "refunds", "order", "orders",
        "tracking", "status", "support", "help", "faq", "faqs", "forum", "forums", "community",
        "communities", "feedback", "survey", "surveys", "review", "reviews", "rating", "ratings",
        "compare", "comparison", "specs", "specifications", "features", "feature", "benefits",
        "benefit", "pricing", "price", "prices", "plan", "plans", "package", "packages", "bundle",
        "bundles", "kit", "kits", "combo", "combos", "collection", "collections", "series",
        "line", "lines", "category", "categories", "department", "departments", "section",
        "sections", "browse", "explore", "discover", "trending", "popular", "recommended",
        "suggested", "related", "similar", "recent", "latest", "new", "coming", "soon", "available",
        "stock", "preorder", "backorder", "notify", "alert", "alerts", "wishlist", "favorites",
        "saved", "shared", "public", "private", "profile", "profiles", "dashboard", "overview",
        "summary", "details", "detail", "info", "information", "overview", "introduction",
        "welcome", "hello", "sign", "signup", "signin", "login", "logout", "register",
        "registration", "password", "username", "forgot", "reset", "verify", "verification",
        "authenticate", "authentication", "authorize", "authorization", "permission", "permissions",
        "role", "roles", "admin", "administrator", "moderator", "member", "members", "guest",
        "guests", "visitor", "visitors", "customer", "customers", "client", "clients", "consumer",
        "consumers", "buyer", "buyers", "seller", "sellers", "merchant", "merchants", "retailer",
        "retailers", "wholesale", "wholesaler", "market", "markets", "industry", "industries",
        "sector", "sectors", "segment", "segments", "vertical", "verticals", "region", "regions",
        "regional", "global", "international", "domestic", "national", "worldwide", "country",
        "countries", "territory", "territories", "province", "provinces", "city", "cities",
        "county", "counties", "district", "districts", "zone", "zones", "area", "areas",
        "office", "offices", "headquarters", "branch", "branches", "location", "locations",
        "address", "addresses", "contact", "contacts", "phone", "phones", "email", "emails",
        "fax", "chat", "call", "calls", "message", "messages", "inquiry", "inquiries",
        "request", "requests", "quote", "quotes", "estimate", "estimates", "consultation",
        "consultations", "appointment", "appointments", "schedule", "scheduling", "calendar",
        "event", "events", "webinar", "webinars", "conference", "conferences", "summit",
        "summits", "expo", "expos", "show", "shows", "fair", "fairs", "festival", "festivals",
        "award", "awards", "recognition", "honor", "honors", "achievement", "achievements",
        "milestone", "milestones", "anniversary", "anniversaries", "history", "heritage",
        "legacy", "story", "stories", "mission", "vision", "values", "purpose", "culture",
        "careers", "career", "job", "jobs", "position", "positions", "opening", "openings",
        "hiring", "recruit", "recruiting", "recruitment", "talent", "workforce", "staffing",
        "intern", "interns", "internship", "internships", "graduate", "graduates", "fellowship",
        "fellowships", "apprentice", "apprentices", "apprenticeship", "training", "certification",
        "certifications", "course", "courses", "class", "classes", "lesson", "lessons",
        "workshop", "workshops", "seminar", "seminars", "bootcamp", "bootcamps", "academy",
        "university", "college", "institute", "institution", "foundation", "foundations",
        "trust", "trusts", "fund", "funds", "grant", "grants", "scholarship", "scholarships",
        "fellowship", "endowment", "endowments", "initiative", "initiatives", "programme",
        "project", "projects", "campaign", "campaigns", "cause", "causes", "impact", "impacts",
        "report", "reports", "reporting", "transparency", "compliance", "regulation", "regulations",
        "policy", "policies", "governance", "ethics", "conduct", "code", "guidelines",
        "standard", "standards", "requirement", "requirements", "specification", "specifications",
        "fi", "wi", "my", "pro", "air", "max", "mini", "se", "xr", "xs", "plus",
    }
)

# Second word often a plural nav noun — "Gift Cards", "User Groups", …
NAV_PLURAL_ENDINGS = frozenset(
    {
        "cards", "deals", "employees", "groups", "states", "accessories", "updates", "essentials",
        "chargers", "models", "speakers", "components", "services", "products", "programs",
        "solutions", "resources", "tools", "features", "benefits", "options", "plans", "packages",
        "bundles", "collections", "categories", "departments", "sections", "locations", "offices",
        "contacts", "inquiries", "requests", "quotes", "events", "webinars", "awards", "jobs",
        "openings", "courses", "classes", "workshops", "projects", "campaigns", "reports",
        "policies", "guidelines", "standards", "requirements", "chapters", "articles", "posts",
        "topics", "tags", "labels", "filters", "results", "items", "pages", "links", "menus",
        "tabs", "panels", "widgets", "modules", "plugins", "extensions", "addons", "integrations",
        "connections", "partnerships", "affiliates", "vendors", "suppliers", "distributors",
        "retailers", "customers", "clients", "members", "users", "visitors", "subscribers",
        "followers", "leaders", "managers", "directors", "executives", "officers", "founders",
        "partners", "associates", "consultants", "advisors", "experts", "specialists",
        "professionals", "technicians", "engineers", "developers", "designers", "analysts",
        "coordinators", "administrators", "representatives", "agents", "operators", "drivers",
        "workers", "staff", "teams", "units", "divisions", "branches", "regions", "territories",
        "markets", "segments", "channels", "platforms", "systems", "networks", "devices",
        "machines", "vehicles", "instruments", "equipments", "supplies", "materials", "parts",
        "pieces", "units", "sets", "pairs", "packs", "boxes", "kits", "rolls", "sheets",
        "blocks", "chips", "cores", "nodes", "pods", "hubs", "gates", "ports", "slots",
        "tracks", "streams", "feeds", "sources", "destinations", "routes", "paths", "ways",
        "modes", "types", "kinds", "forms", "styles", "sizes", "colors", "colours", "shades",
        "tones", "themes", "skins", "covers", "wraps", "films", "layers", "levels", "tiers",
        "grades", "ranks", "classes", "orders", "series", "generations", "versions", "editions",
        "releases", "builds", "variants", "configurations", "specifications", "capabilities",
        "functions", "operations", "actions", "tasks", "steps", "stages", "phases", "cycles",
        "periods", "sessions", "rounds", "turns", "attempts", "trials", "tests", "checks",
        "reviews", "audits", "inspections", "assessments", "evaluations", "measurements",
        "metrics", "statistics", "figures", "numbers", "values", "amounts", "totals", "sums",
        "balances", "payments", "charges", "fees", "costs", "expenses", "savings", "discounts",
        "rebates", "credits", "points", "miles", "rewards", "bonuses", "incentives", "perks",
        "privileges", "rights", "freedoms", "powers", "abilities", "skills", "talents", "gifts",
        "traits", "qualities", "attributes", "properties", "characteristics", "aspects",
        "dimensions", "facets", "angles", "views", "perspectives", "opinions", "thoughts",
        "ideas", "concepts", "notions", "theories", "hypotheses", "assumptions", "beliefs",
        "values", "principles", "rules", "laws", "regulations", "statutes", "codes", "acts",
        "bills", "amendments", "clauses", "articles", "sections", "paragraphs", "sentences",
        "words", "terms", "phrases", "expressions", "statements", "claims", "arguments",
        "reasons", "causes", "effects", "impacts", "consequences", "outcomes", "results",
        "findings", "conclusions", "decisions", "judgments", "verdicts", "rulings", "orders",
        "directives", "commands", "instructions", "directions", "guidance", "advice", "tips",
        "hints", "clues", "signs", "signals", "indicators", "markers", "flags", "alerts",
        "warnings", "notices", "announcements", "bulletins", "advisories", "updates", "changes",
        "modifications", "adjustments", "revisions", "amendments", "corrections", "fixes",
        "patches", "upgrades", "improvements", "enhancements", "optimizations", "refinements",
        "customizations", "personalizations", "adaptations", "transformations", "conversions",
        "migrations", "transitions", "transfers", "movements", "shifts", "switches", "swaps",
        "exchanges", "replacements", "substitutions", "alternatives", "options", "choices",
        "selections", "preferences", "settings", "configurations", "parameters", "variables",
        "constants", "factors", "elements", "ingredients", "components", "ingredients",
    }
)

# Job titles alone are not person names when used as "name"
BARE_TITLE_WORDS = frozenset(
    {
        "ceo", "cto", "cfo", "coo", "cmo", "cio", "cpo", "cro", "chro", "cdo", "vp", "svp",
        "evp", "director", "manager", "president", "chairman", "chairwoman", "chairperson",
        "founder", "partner", "owner", "officer", "executive", "lead", "head", "chief",
    }
)

TEAM_NAME_PAGE_HINTS = (
    "team", "people", "leadership", "staff", "our-team", "management", "who-we-are",
    "executive", "bios", "board", "directors", "officers", "executives",
)
NAME_STOPWORDS = frozenset(
    {
        "been", "being", "have", "has", "had", "will", "would", "could", "should",
        "shall", "may", "might", "must", "can", "does", "did", "done", "doing",
        "different", "same", "other", "another", "each", "every", "both", "such",
        "very", "much", "many", "more", "most", "some", "any", "all", "only",
        "just", "also", "still", "even", "well", "back", "over", "under", "into",
        "from", "with", "without", "within", "between", "about", "after", "before",
        "during", "while", "when", "where", "what", "which", "who", "whom", "whose",
        "this", "that", "these", "those", "here", "there", "then", "than", "them",
        "they", "their", "theirs", "your", "yours", "our", "ours", "his", "her",
        "hers", "its", "not", "no", "yes", "new", "old", "good", "best", "better",
        "great", "high", "low", "long", "short", "first", "last", "next", "previous",
        "left", "right", "top", "bottom", "open", "close", "click", "view", "see",
        "read", "learn", "make", "made", "take", "taken", "give", "given", "get",
        "got", "use", "used", "using", "work", "working", "works", "worked",
        "email", "phone", "contact", "address", "follow", "following", "followed",
        "apple", "google", "microsoft", "amazon", "meta", "world", "wide", "web",
    }
)

TITLE_NAME_RE = re.compile(
    r"^([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*[-–—|]\s*",
)
LINKEDIN_SLUG_RE = re.compile(r"linkedin\.com/in/([a-zA-Z0-9_-]+)", re.I)

EMAIL_EXTRACT_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Pages that usually only list generic emails — skip blind regex harvest there
CONTACT_PAGE_PATH_HINTS = ("contact", "contact-us", "contactus", "get-in-touch", "reach-us")
TEAM_PAGE_PATH_HINTS = ("team", "about", "people", "leadership", "staff", "our-team", "management", "who-we-are", "bios", "executive")


def _url_allows_email_harvest(url: str) -> bool:
    """Only team/people pages — skip homepage, contact, and footer nav junk."""
    u = url.lower()
    if any(h in u for h in CONTACT_PAGE_PATH_HINTS):
        return False
    return any(h in u for h in TEAM_PAGE_PATH_HINTS)


def is_role_based_local_part(local: str) -> bool:
    """True if local part looks like a shared inbox, not an employee."""
    if not local:
        return True
    low = local.lower().strip()
    if low in ROLE_EMAIL_PREFIXES:
        return True
    if any(low == p or low.startswith(f"{p}+") or low.startswith(f"{p}.") for p in ROLE_EMAIL_PREFIXES):
        return True
    if any(low.startswith(f"{p}-") or low.startswith(f"{p}_") for p in ROLE_EMAIL_PREFIXES):
        return True
    # Compound boxes: contactapple, productfeedback, aboutapple, …
    for p in sorted(ROLE_EMAIL_PREFIXES, key=len, reverse=True):
        if len(p) >= 4 and low.startswith(p) and len(low) > len(p):
            rest = re.sub(r"[^a-z]", "", low[len(p):])
            if rest and len(rest) >= 2:
                return True
    # Any segment of first.last / first_last is a role token → department inbox
    segments = [s for s in re.split(r"[._+-]", low) if s]
    if segments and any(s in ROLE_EMAIL_PREFIXES for s in segments):
        return True
    # Compound single-token role boxes (customerservice, contactus, …)
    compact = re.sub(r"[^a-z]", "", low)
    if compact in ROLE_EMAIL_PREFIXES:
        return True
    return False


def _company_name_tokens(company_name: str | None) -> set[str]:
    if not company_name:
        return set()
    stop = {"inc", "llc", "corp", "ltd", "co", "company", "group", "the", "and"}
    return {
        t.lower()
        for t in re.findall(r"[A-Za-z]{3,}", company_name)
        if t.lower() not in stop
    }


def _is_inside_chrome(tag) -> bool:
    """Skip navigation, header, footer — where product/menu labels live."""
    for parent in tag.parents:
        if parent.name in ("nav", "footer", "header", "aside"):
            return True
        role = (parent.get("role") or "").lower()
        if role in ("navigation", "banner", "contentinfo", "complementary"):
            return True
        cls = " ".join(parent.get("class") or []).lower()
        id_ = (parent.get("id") or "").lower()
        chrome = (
            "nav", "menu", "footer", "header", "sidebar", "breadcrumb", "toolbar",
            "globalnav", "global-nav", "site-nav", "mega-menu", "megamenu", "dropdown",
            "flyout", "catalog", "category", "product", "shop", "store", "cart",
        )
        if any(c in cls or c in id_ for c in chrome):
            return True
    return False


def _url_allows_name_extraction(url: str) -> bool:
    """Team/people pages only — not generic /about marketing pages."""
    u = url.lower()
    if any(h in u for h in CONTACT_PAGE_PATH_HINTS):
        return False
    return any(h in u for h in TEAM_NAME_PAGE_HINTS)


def _looks_like_ui_label(name: str) -> bool:
    """True for nav/product/policy phrases: Gift Cards, College Futures, Account Account, …"""
    parts = re.findall(r"[A-Za-z]+", (name or "").strip())
    if len(parts) < 2:
        return True
    lowered = [p.lower() for p in parts]
    if len(set(lowered)) == 1:
        return True
    if any(p in NAV_UI_WORDS for p in lowered):
        return True
    if any(p in BOILERPLATE_NAME_TOKENS for p in lowered):
        return True
    if lowered[-1] in NAV_PLURAL_ENDINGS:
        return True
    if all(p in NAV_UI_WORDS or p in NAV_PLURAL_ENDINGS for p in lowered):
        return True
    ui_hits = sum(1 for p in lowered if p in NAV_UI_WORDS or p in NAV_PLURAL_ENDINGS)
    if ui_hits >= len(parts) - 1:
        return True
    if name.strip().lower() in BARE_TITLE_WORDS:
        return True
    return False


#: A company, not a person: SEC Form 4 lists reporting owners that are
#: entities, and "Barclays Plc" became the person "Plc Barclays".
LEGAL_ENTITY_NAME_TOKENS = frozenset({
    "plc", "llc", "ltd", "inc", "corp", "capital", "strategies", "holdings",
    "partners", "fund", "trust", "bank", "branch", "llp", "gmbh",
})
#: "West London | Christian Rudbeck" read as the person "West London": a
#: compass word with a place word is a region.
_COMPASS_WORDS = frozenset({"north", "south", "east", "west", "central"})
_PLACE_WORDS = frozenset({"london", "city", "region", "county", "england", "midlands", "america", "europe", "asia"})


def names_entity_or_place(name: str) -> bool:
    """True for a company ("Plc Barclays", "Matrix Holdings LLC") or a region
    ("West London") in a name field. The part of looks_like_person_name that
    roster ingestion uses: the rest of it rejects five-word names such as
    "De Moraes Pedro Luiz Bodin", which registers list correctly."""
    lowered = [p.lower() for p in re.findall(r"[A-Za-z]+", name or "")]
    if any(p in LEGAL_ENTITY_NAME_TOKENS for p in lowered):
        return True
    return any(p in _COMPASS_WORDS for p in lowered) and any(p in _PLACE_WORDS for p in lowered)


def looks_like_person_name(name: str, company_name: str | None = None) -> bool:
    """Reject nav/footer labels masquerading as people (About Apple, Gift Cards, …)."""
    if not name or len(name.strip()) < 3:
        return False
    n = name.strip()
    if n.lower() in ("unknown", "contact", "n/a", "na", "tbd", "none"):
        return False
    if _looks_like_ui_label(n):
        return False
    parts = re.findall(r"[A-Za-z]+", n)
    if len(parts) < 2 or len(parts) > 4:
        return False
    lowered = [p.lower() for p in parts]
    if any(p in BOILERPLATE_NAME_TOKENS for p in lowered):
        return False
    if any(p in NAME_STOPWORDS for p in lowered):
        return False
    if any(p in ROLE_EMAIL_PREFIXES for p in lowered):
        return False
    if any(p in BARE_TITLE_WORDS for p in lowered):
        return False
    if names_entity_or_place(n):
        return False
    for p in parts:
        if len(p) == 1:
            continue
        if len(p) < 3:
            return False
        if p.isupper() and len(p) > 2:
            return False
    company_tokens = _company_name_tokens(company_name)
    nav_hits = sum(1 for p in lowered if p in BOILERPLATE_NAME_TOKENS or p in NAV_UI_WORDS)
    company_hits = sum(1 for p in lowered if p in company_tokens)
    if company_hits >= 1 and (nav_hits >= 1 or len(parts) == 2):
        non_person = company_hits + nav_hits
        if non_person >= len(parts) - 1:
            return False
    if company_hits >= 2:
        return False
    return True


def is_boilerplate_email_local(local: str, company_name: str | None = None, domain: str | None = None) -> bool:
    """Nav-style locals: apple.site, about.apple, productfeedback, …"""
    if is_role_based_local_part(local):
        return True
    low = local.lower()
    segments = [s for s in re.split(r"[._+-]", low) if s]
    company_tokens = _company_name_tokens(company_name)
    if domain:
        base = normalize_domain(domain).split(".")[0]
        if base and len(base) >= 3:
            company_tokens = company_tokens | {base}
    if segments and any(s in BOILERPLATE_NAME_TOKENS for s in segments):
        return True
    if segments and any(s in NAV_UI_WORDS for s in segments):
        return True
    if segments and any(s in NAV_PLURAL_ENDINGS for s in segments):
        return True
    if company_tokens and segments:
        if sum(1 for s in segments if s in company_tokens) >= 1 and len(segments) <= 3:
            if any(s in BOILERPLATE_NAME_TOKENS or s in ROLE_EMAIL_PREFIXES for s in segments):
                return True
    return False


def extract_person_name_from_title(title: str | None) -> str | None:
    """Parse 'Lauren Anderholm - WW Merchandising …' → Lauren Anderholm."""
    if not title:
        return None
    t = title.strip()
    m = TITLE_NAME_RE.match(t)
    if m:
        candidate = m.group(1).strip()
        if looks_like_person_name(candidate):
            return candidate
    return None


def name_from_linkedin_url(url: str | None) -> str | None:
    if not url:
        return None
    m = LINKEDIN_SLUG_RE.search(url)
    if not m:
        return None
    slug = m.group(1).strip("-_")
    parts = [p for p in re.split(r"[-_]+", slug) if p and not p.isdigit()]
    if len(parts) < 2:
        return None
    name = " ".join(p.capitalize() for p in parts[:4])
    return name if looks_like_person_name(name) else None


def strict_email_name_alignment(name: str, email: str) -> bool:
    """
    first.last gate: last name (≥3 chars) must appear in local part; first name or initial too.
    Rejects 'been different' + been.different@… and prose-derived junk names.
    """
    if not is_employee_outreach_email(email):
        return False
    if not name or not looks_like_person_name(name):
        return False
    parts = name.split()
    first = parts[0]
    last = parts[-1] if len(parts) >= 2 else ""
    if not last or len(last) < 2:
        return False
    if first.lower() in NAME_STOPWORDS or last.lower() in NAME_STOPWORDS:
        return False
    if not email_matches_person_name(email, first, last):
        return False
    local = re.sub(r"[^a-z]", "", email.split("@")[0].lower())
    f = re.sub(r"[^a-z]", "", first.lower())
    l = re.sub(r"[^a-z]", "", last.lower())
    if len(l) >= 3 and l not in local:
        return False
    if len(f) >= 2 and f not in local and f[0] not in local:
        return False
    return True


def is_valid_person_contact(
    contact: dict,
    *,
    company_name: str | None = None,
    domain: str | None = None,
    require_person_name: bool = True,
) -> bool:
    """Gate for scrape/merge/save — real employee-style contact only."""
    email = sanitize_email((contact.get("email") or "").strip())
    if not email or not is_employee_outreach_email(email):
        return False
    local = email.split("@")[0]
    if is_boilerplate_email_local(local, company_name, domain):
        return False
    name = (contact.get("name") or "").strip()
    title_name = extract_person_name_from_title(contact.get("title"))
    li_name = name_from_linkedin_url(contact.get("linkedin_url"))
    canonical = title_name or li_name or name

    if require_person_name:
        if not canonical or not looks_like_person_name(canonical, company_name):
            return False
        parts = canonical.split()
        first = parts[0]
        last = parts[-1] if len(parts) >= 2 else ""
        if not strict_email_name_alignment(canonical, email):
            return False
    elif not looks_like_person_email_local(local):
        return False

    src = (contact.get("contact_source") or "").lower()
    if src == "inferred":
        if not contact.get("_structured_team_name"):
            return False
        title = (contact.get("title") or "").strip()
        if not title or title.upper() in {t.upper() for t in BARE_TITLE_WORDS}:
            return False
        if _looks_like_ui_label(canonical):
            return False
    return True


def looks_like_person_email_local(local: str) -> bool:
    """Heuristic when no name is paired: reject obvious shared inboxes and nav-style locals."""
    if not local or is_role_based_local_part(local):
        return False
    low = local.lower().strip()
    if len(low) < 3:
        return False
    segments = [s for s in re.split(r"[._+-]", low) if s]
    if segments and any(s in NAV_UI_WORDS or s in NAV_PLURAL_ENDINGS for s in segments):
        return False
    if len(segments) >= 2:
        return all(len(s) >= 2 and s not in ROLE_EMAIL_PREFIXES and s not in NAV_UI_WORDS for s in segments[:2])
    return len(low) >= 5 and low.isalpha() and low not in NAV_UI_WORDS


def email_matches_person_name(email: str, first: str, last: str) -> bool:
    """True if mailbox local part plausibly belongs to this person (not a shared inbox)."""
    if not is_employee_outreach_email(email):
        return False
    local = email.split("@")[0].lower()
    f = re.sub(r"[^a-z]", "", (first or "").lower())
    l = re.sub(r"[^a-z]", "", (last or "").lower())
    if not f and not l:
        return looks_like_person_email_local(local)
    if l and len(l) >= 2 and l in local:
        return True
    if f and len(f) >= 2 and f in local:
        return True
    compact = re.sub(r"[^a-z]", "", local)
    if f and l:
        if compact in (f"{f[0]}{l}", f"{f}{l}", f"{l}{f}", f"{l}.{f}"):
            return True
    return False


def extract_employee_emails_from_text(
    text: str,
    domain: str | None = None,
    *,
    first: str | None = None,
    last: str | None = None,
) -> list[str]:
    """Pull only person-style emails from arbitrary web text (Tavily, HTML, etc.)."""
    if not text:
        return []
    dom = normalize_domain(domain or "") if domain else ""
    seen: set[str] = set()
    out: list[str] = []
    for match in EMAIL_EXTRACT_REGEX.finditer(text):
        email = sanitize_email(match.group().lower())
        if not is_employee_outreach_email(email):
            continue
        if dom and dom not in email.split("@")[-1]:
            continue
        local = email.split("@")[0]
        if first or last:
            if not email_matches_person_name(email, first or "", last or ""):
                continue
        elif not looks_like_person_email_local(local):
            continue
        if email in seen:
            continue
        seen.add(email)
        out.append(email)
    return out


def is_employee_outreach_email(email: str) -> bool:
    """Viable person outreach: real domain mailbox, not role-based or synthetic placeholder."""
    if not email or "@" not in email:
        return False
    low = email.lower()
    if "placeholder" in low or ".local" in low.split("@")[-1]:
        return False
    local = email.split("@")[0].lower()
    return not is_role_based_local_part(local)


def _url_is_contact_only_page(url: str) -> bool:
    """True if URL is likely a /contact page without team roster (stricter extraction)."""
    u = url.lower()
    if not any(h in u for h in CONTACT_PAGE_PATH_HINTS):
        return False
    return not any(h in u for h in TEAM_PAGE_PATH_HINTS)


def normalize_domain(domain_or_url: str) -> str:
    """
    Extract clean hostname for email (e.g. lockheedmartin.com) from URL or domain.
    Prevents malformed emails like name@https://example.com/path
    """
    if not domain_or_url or not isinstance(domain_or_url, str):
        return ""
    s = domain_or_url.strip().lower()
    s = s.replace("www.", "")
    if "://" in s:
        s = s.split("://", 1)[1]
    if "/" in s:
        s = s.split("/")[0]
    if "?" in s:
        s = s.split("?")[0]
    if ":" in s and not s.startswith("["):
        s = s.split(":")[0]
    return s


def sanitize_email(email: str) -> str:
    """Fix malformed emails like name@https://domain.com/path -> name@domain.com"""
    if not email or "@" not in email:
        return email or ""
    local, _, domain_part = email.partition("@")
    if "://" in domain_part or "/" in domain_part:
        domain_part = normalize_domain(domain_part)
        if domain_part:
            return f"{local}@{domain_part}"
    return email


def is_heuristic_junk_contact(contact: dict, company_name: str | None = None) -> tuple[bool, str]:
    """Fast local junk gate — skips Bedrock for obvious nav/product/role rows."""
    name = (contact.get("name") or "").strip()
    if not name:
        return True, "missing name"
    if not looks_like_person_name(name, company_name):
        return True, "not a plausible person name"
    email = sanitize_email(contact.get("email") or "")
    if email and "@" in email:
        local = email.split("@", 1)[0].lower()
        if is_boilerplate_email_local(local, company_name, contact.get("company_domain")):
            return True, "boilerplate or nav email local"
        if not is_employee_outreach_email(email):
            return True, "role or shared inbox"
    title = (contact.get("title") or "").strip()
    if title and _looks_like_ui_label(title) and not looks_like_person_name(name, company_name):
        return True, "nav-style title"
    return False, ""


def person_name_key(name: str) -> str:
    """Normalize a name for cross-source matching."""
    parts = re.findall(r"[A-Za-z]+", (name or "").lower())
    return " ".join(parts[:4])


def infer_email(name: str, domain: str) -> list[str]:
    """Infer possible email addresses from name and domain."""
    domain = normalize_domain(domain or "")
    if not name or not domain:
        return []
    parts = name.strip().split()
    emails = []
    if len(parts) >= 2:
        first, last = parts[0].lower(), parts[-1].lower()
        emails.extend([
            f"{first}.{last}@{domain}",
            f"{first}{last}@{domain}",
            f"{first[0]}{last}@{domain}",
            f"{first}@{domain}",
        ])
    elif len(parts) == 1:
        emails.append(f"{parts[0].lower()}@{domain}")
    return [e for e in set(emails) if is_employee_outreach_email(e)]


async def validate_email_mx(email: str) -> bool:
    """Check if domain has MX records (basic deliverability check)."""
    try:
        domain = email.split("@")[-1]
        await asyncio.to_thread(dns.resolver.resolve, domain, "MX")
        return True
    except Exception:
        return False


def _confidence_rank(c: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get((c or "").lower(), 0)


def _best_confidence(*levels: str | None) -> str:
    ordered = sorted((l for l in levels if l), key=_confidence_rank, reverse=True)
    return ordered[0] if ordered else "medium"


def compute_contact_confidence(
    *,
    email: str,
    name: Optional[str] = None,
    title: Optional[str] = None,
    contact_source: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    mx_valid: Optional[bool] = None,
    found_with_name: bool = False,
    source_url: Optional[str] = None,
    company_name: Optional[str] = None,
    domain: Optional[str] = None,
    email_verified: bool = False,
) -> str:
    """
    Confidence for employee outreach contacts (high / medium / low).

    Real people with LinkedIn or name+email corroboration should land medium+.
    MX checks are a small boost only — DNS lookups often fail for valid corporate mail.
    """
    score = 35  # passed person-contact validation

    src = (contact_source or "").lower()
    if src == "domain_scrape":
        score += 20 if (found_with_name or email_verified) else 12
    elif src == "web_discovery":
        score += 22 if email_verified else 14
    elif src.startswith("roster_"):
        score += 20 if email_verified else 14
    elif src == "inferred":
        score += 12
    elif email_verified or found_with_name:
        score += 15

    if title and str(title).strip():
        score += 10
    if linkedin_url and str(linkedin_url).strip():
        score += 15
    if found_with_name:
        score += 8
    if email_verified:
        score += 10
    if source_url and any(p in source_url.lower() for p in TEAM_PAGE_PATH_HINTS):
        score += 5

    parts = (name or "").split()
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) >= 2 else ""
    if name and looks_like_person_name(name, company_name):
        score += 8
        if last and strict_email_name_alignment(name, email):
            score += 15
        elif last and email_matches_person_name(email, first, last):
            score += 5

    if mx_valid is True:
        score += 5

    if score >= 68:
        return "high"
    if score >= 48:
        return "medium"
    return "low"


def _compute_confidence(
    email: str,
    name: Optional[str],
    title: Optional[str],
    mx_valid: bool,
    source_url: str,
    found_with_name: bool,
    company_name: Optional[str] = None,
    domain: Optional[str] = None,
) -> str:
    """Legacy wrapper used by domain crawl."""
    return compute_contact_confidence(
        email=email,
        name=name,
        title=title,
        contact_source="domain_scrape",
        mx_valid=mx_valid,
        found_with_name=found_with_name,
        source_url=source_url,
        company_name=company_name,
        domain=domain,
        email_verified=found_with_name,
    )


def confidence_for_contact_dict(
    contact: dict,
    *,
    company_name: Optional[str] = None,
    domain: Optional[str] = None,
    email_verified: bool | None = None,
    found_with_name: bool = False,
    source_url: Optional[str] = None,
    mx_valid: Optional[bool] = None,
) -> str:
    """Score an assembled contact row (merge / save paths)."""
    email = sanitize_email(contact.get("email") or "")
    verified = email_verified
    if verified is None:
        src = (contact.get("contact_source") or "").lower()
        verified = src in ("domain_scrape", "web_discovery") and not src.endswith("inferred")
        if src == "linkedin_inferred" or src == "inferred":
            verified = False
        if src == "web_discovery" and contact.get("email"):
            verified = bool(contact.get("_email_verified"))
    return compute_contact_confidence(
        email=email,
        name=contact.get("name"),
        title=contact.get("title"),
        contact_source=contact.get("contact_source"),
        linkedin_url=contact.get("linkedin_url"),
        mx_valid=mx_valid,
        found_with_name=found_with_name,
        source_url=source_url,
        company_name=company_name or contact.get("company"),
        domain=domain or contact.get("company_domain"),
        email_verified=bool(verified),
    )


async def scrape_contacts_from_domain(
    domain: str,
    company_name: Optional[str] = None,
    max_pages: int = 10,
    on_page: PageProgressHook = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> list[dict]:
    """HTML crawl. Slot held here only — Tavily/Apify/Bedrock stay ungated."""
    from app.services.discovery_gate import discovery_job

    async with discovery_job() as queued:
        if queued and on_page:
            await on_page(0, 1, "queued")
        return await _scrape_contacts_from_domain_html(
            domain, company_name, max_pages, on_page, cancel_event
        )


CRAWL_FETCH_WORKERS = max(1, int(os.getenv("CRAWL_FETCH_WORKERS", "5") or 5))
CRAWL_PAGE_TIMEOUT_SEC = float(os.getenv("CRAWL_PAGE_TIMEOUT_SEC", "5") or 5)


async def _fetch_crawl_pages(
    client: httpx.AsyncClient,
    base_url: str,
    urls: list[str],
    cancel_event: Optional[asyncio.Event] = None,
) -> list[tuple[str, Optional[httpx.Response]]]:
    """Each candidate page with its 200 response, or None.

    The pages used to be fetched one after another: 6s for Barclays, whose
    site answers every path with its homepage, for no contacts at all. Now they
    go in waves of CRAWL_FETCH_WORKERS alongside the homepage itself, and a
    page that is the homepage again (same final URL, or same bytes) is not a
    team page. When the whole first wave is the homepage, the site is a
    catch-all and the rest are not asked."""
    fetch_timeout = httpx.Timeout(CRAWL_PAGE_TIMEOUT_SEC)

    async def get(url: str) -> Optional[httpx.Response]:
        if cancel_event is not None and cancel_event.is_set():
            return None
        try:
            resp = await client.get(url, timeout=fetch_timeout)
        except Exception:
            return None
        return resp if resp.status_code == 200 else None

    def fingerprint(resp: httpx.Response) -> tuple[str, str]:
        return str(resp.url).rstrip("/"), hashlib.sha256(resp.content).hexdigest()

    first, rest = urls[:CRAWL_FETCH_WORKERS], urls[CRAWL_FETCH_WORKERS:]
    home, *wave = await asyncio.gather(get(base_url.rstrip("/") + "/"), *(get(u) for u in first))
    home_print = fingerprint(home) if home is not None else None

    def team_page(resp: Optional[httpx.Response]) -> Optional[httpx.Response]:
        if resp is None or home_print is None:
            return resp
        final, digest = fingerprint(resp)
        return None if final == home_print[0] or digest == home_print[1] else resp

    answered = [r for r in wave if r is not None]
    pages = [team_page(r) for r in wave]
    catch_all = bool(answered) and all(p is None for p in pages) and home_print is not None
    for i in range(0, len(rest) if not catch_all else 0, CRAWL_FETCH_WORKERS):
        chunk = rest[i:i + CRAWL_FETCH_WORKERS]
        pages += [team_page(r) for r in await asyncio.gather(*(get(u) for u in chunk))]
    pages += [None] * (len(urls) - len(pages))
    return list(zip(urls, pages))


async def _scrape_contacts_from_domain_html(
    domain: str,
    company_name: Optional[str] = None,
    max_pages: int = 10,
    on_page: PageProgressHook = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> list[dict]:
    """
    Scrape a company domain for contact information.
    Returns list of contact dicts with name, email, title, confidence.
    """
    # Callers resolve the domain (MX-verified) before reaching here. Guessing one
    # from the name sent the crawler at hosts that do not exist.
    if not domain:
        return []

    domain = normalize_domain(domain)
    if not domain.startswith("http"):
        base_url = f"https://{domain}"
    else:
        base_url = domain
        domain = normalize_domain(domain)

    contacts = []
    seen_emails = set()
    names_from_pages: list[dict] = []  # {name, title} for email generator fallback
    personal_emails_for_format: list[str] = []  # Non-role emails to detect format

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # More pages = better coverage; team/people pages have higher-quality contacts
            urls_to_check = [
                f"{base_url.rstrip('/')}/team",
                f"{base_url.rstrip('/')}/our-team",
                f"{base_url.rstrip('/')}/people",
                f"{base_url.rstrip('/')}/leadership",
                f"{base_url.rstrip('/')}/staff",
                f"{base_url.rstrip('/')}/about-us",
                f"{base_url.rstrip('/')}/about",
                f"{base_url.rstrip('/')}/management",
                f"{base_url.rstrip('/')}/who-we-are",
                f"{base_url.rstrip('/')}/executive-team",
            ]

            total_pages = min(len(urls_to_check), max_pages)
            fetched = await _fetch_crawl_pages(client, base_url, urls_to_check[:max_pages], cancel_event)
            for page_idx, (url, resp) in enumerate(fetched):
                if cancel_event is not None and cancel_event.is_set():
                    break
                if on_page:
                    await on_page(page_idx + 1, total_pages, url)
                if resp is None:
                    continue
                try:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    if not _url_allows_email_harvest(url):
                        if _url_allows_name_extraction(url):
                            for nd in _extract_names_from_page(soup, url):
                                if not any(n["name"].lower() == nd["name"].lower() for n in names_from_pages):
                                    names_from_pages.append(nd)
                        continue

                    email_regex = EMAIL_EXTRACT_REGEX

                    # Find "name - title - email" blocks first (highest confidence)
                    for elem in soup.find_all(["p", "div", "li", "span", "td"]):
                        text = elem.get_text(separator=" ", strip=True)
                        if "@" in text and domain in text:
                            for match in email_regex.finditer(text):
                                email = match.group().lower()
                                if domain not in email or email in seen_emails:
                                    continue
                                local = email.split("@")[0].lower()
                                if is_boilerplate_email_local(local, company_name, domain):
                                    continue
                                name = _extract_name_from_text(text, email) or _extract_name_near_email(resp.text, email)
                                if not name or not looks_like_person_name(name, company_name):
                                    continue
                                seen_emails.add(email)
                                personal_emails_for_format.append(email)
                                title = _extract_title_from_text(text) or _infer_title_from_context(resp.text, email)
                                mx_valid = True  # deferred — verify pipeline batch-checks MX
                                confidence = _compute_confidence(
                                    email, name, title, mx_valid, url,
                                    found_with_name=True,
                                    company_name=company_name,
                                    domain=domain,
                                )
                                contacts.append({
                                    "name": name,
                                    "email": email,
                                    "title": title,
                                    "company": company_name or domain,
                                    "company_domain": domain,
                                    "confidence": confidence,
                                    "contact_source": "domain_scrape",
                                    "source_url": url,
                                    "discovery_context": text[:300],
                                })

                    for match in email_regex.finditer(resp.text):
                        email = match.group().lower()
                        if domain not in email or email in seen_emails:
                            continue
                        local = email.split("@")[0].lower()
                        if is_boilerplate_email_local(local, company_name, domain):
                            continue
                        name = _extract_name_near_email(resp.text, email)
                        if not name or not looks_like_person_name(name, company_name):
                            continue
                        seen_emails.add(email)
                        personal_emails_for_format.append(email)
                        title = _infer_title_from_context(resp.text, email)
                        mx_valid = True  # deferred — verify pipeline batch-checks MX
                        confidence = _compute_confidence(
                            email, name, title, mx_valid, url,
                            found_with_name=bool(name),
                            company_name=company_name,
                            domain=domain,
                        )
                        contacts.append({
                            "name": name,
                            "email": email,
                            "title": title,
                            "company": company_name or domain,
                            "company_domain": domain,
                            "confidence": confidence,
                            "contact_source": "domain_scrape",
                            "source_url": url,
                            "discovery_context": _extract_snippet_near_email(resp.text, email),
                        })

                    for nd in _extract_names_from_page(soup, url):
                        if not any(n["name"].lower() == nd["name"].lower() for n in names_from_pages):
                            names_from_pages.append(nd)

                except Exception:
                    continue

            # Deduplicate by email (keep highest confidence)
            by_email: dict[str, dict] = {}
            for c in contacts:
                e = c["email"]
                if e not in by_email or _confidence_rank(c["confidence"]) > _confidence_rank(by_email[e]["confidence"]):
                    by_email[e] = c
            contacts = list(by_email.values())

            # Email generator fallback: only structured team-page names when real emails proved format
            names_with_emails = {c["name"].lower() for c in contacts if c.get("name") not in ("Unknown", "Contact")}
            format_order = _detect_email_format(personal_emails_for_format, domain)

            if personal_emails_for_format and format_order:
                for nd in names_from_pages:
                    if not nd.get("_structured_team_name"):
                        continue
                    name = nd.get("name")
                    if not name or not looks_like_person_name(name, company_name):
                        continue
                    if name.lower() in names_with_emails:
                        continue
                    if len(name.split()) < 2:
                        continue
                    title = nd.get("title")
                    if not title or _looks_like_ui_label(title):
                        continue

                    generated_email = None
                    for fmt_name, fmt_fn in format_order:
                        gen = _generate_email_for_name(name, domain, fmt_fn)
                        if gen and gen not in seen_emails:
                            generated_email = gen
                            break

                    if generated_email and is_employee_outreach_email(generated_email):
                        if is_boilerplate_email_local(generated_email.split("@")[0], company_name, domain):
                            continue
                        seen_emails.add(generated_email)
                        row = {
                            "name": name,
                            "email": generated_email,
                            "title": title,
                            "company": company_name or domain,
                            "company_domain": domain,
                            "contact_source": "inferred",
                            "_structured_team_name": True,
                        }
                        row["confidence"] = confidence_for_contact_dict(
                            row, company_name=company_name, domain=domain
                        )
                        contacts.append(row)
                        names_with_emails.add(name.lower())

            # No placeholder role emails — employee outreach only; use LinkedIn/Apify + team pages for people.

    except Exception:
        pass

    return [
        c
        for c in contacts
        if is_valid_person_contact(c, company_name=company_name, domain=domain)
    ]


def _extract_snippet_near_email(text: str, email: str, radius: int = 120) -> str:
    """Short page excerpt around an email for AI / audit logs."""
    idx = text.find(email)
    if idx == -1:
        return ""
    start = max(0, idx - radius)
    end = min(len(text), idx + len(email) + radius)
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    return snippet[:300]


def _extract_name_near_email(text: str, email: str) -> Optional[str]:
    """Extract name near email; reject prose fragments and stopword pairs."""
    idx = text.find(email)
    if idx == -1:
        return None
    before = text[max(0, idx - 200) : idx]
    after = text[idx + len(email) : idx + len(email) + 100]
    for snippet in (before, after):
        words = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+\b", snippet)
        for candidate in words:
            c = candidate.strip()
            if looks_like_person_name(c):
                return c
        caps = re.findall(r"\b[A-Z][a-z]+\b", snippet)
        if len(caps) >= 2:
            candidate = f"{caps[-2]} {caps[-1]}"
            if looks_like_person_name(candidate):
                return candidate
    return None


def _extract_name_from_text(text: str, email: str) -> Optional[str]:
    parts = text.split(email)[0].strip().split()
    if len(parts) >= 2:
        candidate = f"{parts[-2]} {parts[-1]}"
        if looks_like_person_name(candidate):
            return candidate
    return None


def _extract_title_from_text(text: str) -> Optional[str]:
    # Common title patterns (order matters for compound titles)
    patterns = [
        "Chief Executive Officer", "CEO",
        "Chief Technology Officer", "CTO",
        "Chief Financial Officer", "CFO",
        "Chief Marketing Officer", "CMO",
        "Chief Operating Officer", "COO",
        "VP of", "Vice President",
        "Director of", "Director",
        "Manager", "Lead", "Head of",
        "Founder", "Co-Founder", "Partner",
        "President", "Owner",
    ]
    text_upper = text.upper()
    for p in patterns:
        if p.upper() in text_upper:
            return p
    return None


def _infer_title_from_context(text: str, email: str) -> Optional[str]:
    local = email.split("@")[0].lower()
    if "info" in local or "contact" in local:
        return "General Contact"
    if "sales" in local:
        return "Sales"
    if "hr" in local or "careers" in local:
        return "HR"
    return None


def _apply_custom_pattern(pattern: str, first: str, last: str) -> str:
    """Apply custom format pattern.

    Placeholders: {first}, {last}, {first_initial}, {last_initial}. Initials are
    substituted before the full names so that neither {first_initial} nor
    {last_initial} can be partly consumed by the {first}/{last} replacement.
    """
    return (
        pattern.replace("{first_initial}", first[:1])
        .replace("{last_initial}", last[:1])
        .replace("{first}", first)
        .replace("{last}", last)
    )


def infer_email_from_name(
    name: str,
    domain: str,
    custom_patterns: Optional[list[str]] = None,
) -> Optional[str]:
    """Infer likely email from name and domain. Tries custom patterns first if provided."""
    domain = normalize_domain(domain or "")
    if not name or not domain or "Unknown" in name or "Contact" in name:
        return None
    parts = name.strip().split()
    if len(parts) >= 2:
        first, last = parts[0].lower(), parts[-1].lower()
        if custom_patterns:
            for p in custom_patterns:
                try:
                    local = _apply_custom_pattern(p, first, last)
                    if local and "@" not in local:
                        candidate = f"{local}@{domain}"
                        if is_employee_outreach_email(candidate):
                            return candidate
                except Exception:
                    pass
        candidate = f"{first}.{last}@{domain}"
        return candidate if is_employee_outreach_email(candidate) else None
    elif len(parts) == 1:
        candidate = f"{parts[0].lower()}@{domain}"
        return candidate if is_employee_outreach_email(candidate) else None
    return None


# Email format generators: (name_parts) -> local_part
def _fmt_first_dot_last(first: str, last: str) -> str:
    return f"{first}.{last}"


def _fmt_first_last(first: str, last: str) -> str:
    return f"{first}{last}"


def _fmt_flast(first: str, last: str) -> str:
    return f"{first[0]}{last}" if first and last else ""


def _fmt_first_underscore_last(first: str, last: str) -> str:
    return f"{first}_{last}"


def _fmt_last_first(first: str, last: str) -> str:
    return f"{last}.{first}" if first and last else ""


def _fmt_first_only(first: str, last: str) -> str:
    return first


EMAIL_FORMATS = [
    ("first.last", _fmt_first_dot_last),
    ("firstlast", _fmt_first_last),
    ("flast", _fmt_flast),
    ("first_last", _fmt_first_underscore_last),
    ("last.first", _fmt_last_first),
    ("first", _fmt_first_only),
]


def _detect_email_format(emails: list[str], domain: str) -> list[tuple[str, callable]]:
    """
    Analyze found emails to infer this site's format.
    Returns list of (format_name, formatter_func) ordered by likelihood.
    """
    domain = domain.lower()
    scored: list[tuple[float, tuple[str, callable]]] = []

    for email in emails:
        if domain not in email:
            continue
        local = email.split("@")[0].lower()
        if is_role_based_local_part(local):
            continue  # Skip role-based for format detection

        # Try to parse as first.last (e.g. john.doe)
        if "." in local and "_" not in local:
            parts = local.split(".")
            if len(parts) == 2 and len(parts[0]) <= 2 and len(parts[1]) > 2:
                scored.append((2.0, ("flast", _fmt_flast)))  # j.doe -> flast
            elif len(parts) >= 2:
                scored.append((3.0, ("first.last", _fmt_first_dot_last)))
        elif "_" in local:
            scored.append((2.5, ("first_last", _fmt_first_underscore_last)))
        elif len(local) > 3 and not "." in local:
            # Could be firstlast or flast
            scored.append((1.5, ("firstlast", _fmt_first_last)))
            scored.append((1.0, ("flast", _fmt_flast)))

    # Count format votes, return by frequency
    from collections import Counter
    fmt_by_name = {n: f for n, f in EMAIL_FORMATS}
    if not scored:
        return EMAIL_FORMATS

    format_counts: Counter = Counter()
    for _, (fmt_name, _) in scored:
        format_counts[fmt_name] += 1

    ordered = [(fmt_name, fmt_by_name[fmt_name]) for fmt_name, _ in format_counts.most_common() if fmt_name in fmt_by_name]
    for fmt_name, fmt_fn in EMAIL_FORMATS:
        if fmt_name not in format_counts:
            ordered.append((fmt_name, fmt_fn))
    return ordered


def _generate_email_for_name(name: str, domain: str, format_func) -> Optional[str]:
    """Generate email using the given format function."""
    domain = normalize_domain(domain or "")
    if not domain:
        return None
    parts = name.strip().split()
    if len(parts) >= 2:
        first, last = parts[0].lower(), parts[-1].lower()
        local = format_func(first, last)
        if local:
            candidate = f"{local}@{domain}"
            if is_employee_outreach_email(candidate) and email_matches_person_name(candidate, first, last):
                return candidate
    elif len(parts) == 1:
        candidate = f"{parts[0].lower()}@{domain}"
        if is_employee_outreach_email(candidate):
            return candidate
    return None


def _extract_names_from_page(soup: BeautifulSoup, url: str) -> list[dict]:
    """
    Extract person names (and titles) from team/people pages only.
    Returns list of {name, title, _structured_team_name} — skips nav/footer chrome.
    """
    names_found: list[dict] = []
    seen_names: set[str] = set()

    if not _url_allows_name_extraction(url):
        return []

    for tag in soup.find_all(["h2", "h3", "h4", "div", "span", "p", "li"]):
        if _is_inside_chrome(tag):
            continue
        text = tag.get_text(separator=" ", strip=True)
        if not text or len(text) > 80 or "@" in text:
            continue

        # Structured "Name - Title" / "Name, Title" in same short line (high confidence)
        for sep in [",", "–", "-", "|"]:
            if sep in text and len(text) < 60:
                parts = text.split(sep, 1)
                if len(parts) != 2:
                    continue
                name_part = parts[0].strip()
                title_part = parts[1].strip()
                if not re.match(r"^[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+", name_part):
                    continue
                if not looks_like_person_name(name_part):
                    continue
                if _looks_like_ui_label(title_part):
                    continue
                title = _extract_title_from_text(title_part) or title_part[:80]
                if not title or title.upper() in {t.upper() for t in BARE_TITLE_WORDS} and len(title) <= 4:
                    continue
                key = name_part.lower()
                if key in seen_names:
                    continue
                seen_names.add(key)
                names_found.append({
                    "name": name_part,
                    "title": title,
                    "_structured_team_name": True,
                })

    return names_found
