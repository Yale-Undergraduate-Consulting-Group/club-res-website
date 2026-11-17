# Wix to the existing AWS public-site stack

## Decision

Migrate the public Wix site into the repository's existing Vite/static publication path and the existing production CloudFront + private S3 `site` bucket. Do **not** create a second hosting stack or a new paid AWS service. Keep Wix live until every public page, form, redirect, and DNS prerequisite has passed acceptance; then make a reversible DNS cutover.

Expected incremental monthly AWS cost: **approximately $0 at current traffic**, because the S3 bucket, CloudFront distribution (`PriceClass_100`), EC2/FastAPI container, deployment pipeline, and logs already exist. Static bytes add only usage-priced S3 storage/requests and CloudFront transfer/requests. ACM-issued public certificates used by CloudFront cost **$0** ([ACM pricing](https://aws.amazon.com/certificate-manager/pricing)). If the current DNS provider can point the apex to CloudFront, keep it and add **$0 fixed cost**. Use a Route 53 public hosted zone only if the current provider cannot support the necessary apex alias/ANAME; its fixed cost is **$0.50/month** plus DNS queries ([Route 53 pricing](https://aws.amazon.com/route53/pricing)). The known AWS estate remains about $29/month. The Wix subscription saving is unknown until the President supplies the current plan and domain-renewal invoices; net monthly impact is `- cancelled Wix hosting + $0 to $0.50 fixed AWS DNS + small usage charges`.

## Hard prerequisite and owners

The owner and DNS host of **`yaleconsulting.org` are unknown**. Before implementation, the President must identify:

1. the registrar account and renewal owner;
2. the authoritative DNS provider and an officer who can edit it;
3. every current DNS record, especially MX, SPF, DKIM, DMARC, Google/Microsoft verification, and any subdomains;
4. whether Wix owns only hosting, also DNS, or also the registration;
5. who can approve an ACM DNS-validation record and the final apex/`www` change;
6. the current Wix plan/renewal date and the earliest safe cancellation date.

Do not change nameservers just to move the website. Preserve mail and verification records exactly. If Route 53 is needed, first copy and independently check the complete zone; a missing mail record is a larger outage than a delayed website cutover.

Operational roles:

- **Content owner:** President/Marketing officer signs off page copy, images, blog completeness, legal/privacy text, and SEO metadata.
- **Forms owner:** President names recipients, response SLA, retention period, and which submissions are sensitive.
- **Technical owner:** repository maintainer implements through the normal feature → integration → dev → prod gates and a reviewed Terraform change for certificate/domain/redirect configuration.
- **DNS owner:** identified registrar/DNS administrator performs only the approved validation and cutover records.

## Wix extraction limits and inventory

Wix does not provide a portable site export: its own help page says a Wix site must run on Wix infrastructure and cannot be externally hosted. This is a **content and behavior rebuild**, not an HTML copy. [Wix: exporting or embedding a site elsewhere](https://support.wix.com/en/article/exporting-or-embedding-your-wix-site-elsewhere).

Create one inventory sheet before writing code, with a row for every live URL and columns:

`old URL | page type | title | H1 | description | canonical | index/noindex | body owner | media | downloads | forms/embeds | structured data | inbound links/top traffic | new URL | redirect | acceptance owner`.

Collect it from four independent views so hidden content is not missed:

1. Wix Pages/menus, hidden pages, Members/Bookings/Events, dynamic pages, and mobile-only elements.
2. The XML sitemap and a read-only crawl of `yaleconsulting.org`, including response status, canonical, headings, images, downloadable files, internal links, and third-party embeds. Crawl public pages only; do not put Wix customer/form data in crawl files or Git.
3. Wix Analytics and Google Search Console, if available: top landing pages, indexed URLs, search queries, backlinks, and 404s for the last 12 months. This prevents a low-navigation but high-search URL from disappearing.
4. Wix dashboards for Forms & Submissions, Blog, CMS collections, Media Manager, automations, contacts, and domain/DNS.

Export and handle each content class deliberately:

- **Ordinary pages:** copy approved text and layout manually. The site cannot be exported as reusable code.
- **CMS collections/dynamic pages:** export each live collection as UTF-8 CSV, including hidden/system fields where needed; Wix supports whole, filtered, or schema-only exports. [Wix CMS CSV export](https://support.wix.com/en/article/cms-formerly-content-manager-exporting-content-from-your-collection). Convert reviewed rows into versioned public content files; do not commit form submissions, contacts, or client data.
- **Blog:** Wix currently says posts cannot be exported to another platform. [Wix Blog export limitation](https://support.wix.com/en/article/wix-blog-exporting-blog-posts-to-other-platforms). Inventory every post from sitemap/dashboard, then manually copy or use a one-off authenticated read/export approved by the site owner. Preserve slug, title, author display, publish/update dates, body structure, alt text, canonical, and social image. Compare post count and sampled rendered content before acceptance.
- **Media:** Media Manager allows single/multi-select download only for files the club uploaded; free Wix/Shutterstock media cannot be downloaded, and there is no download-all across folders. [Wix media download limits](https://support.wix.com/en/article/downloading-files-from-the-media-manager). Download folder-by-folder, record source/license/alt text, deduplicate by checksum, optimize responsive images, and replace media that lacks a transferable license.
- **Forms:** Wix can export form submissions to CSV (dates are UTC), including a whole or filtered table depending on old/new Forms. [Wix submissions export](https://support.wix.com/en/article/wix-forms-viewing-and-managing-a-submissions-table). Export for the named data owner outside Git, define retention/deletion, and migrate only records that remain needed and lawful. Historical submissions do not belong in static assets or tests.
- **Redirects:** export Wix's existing redirect CSV and add it to the URL map. Wix documents redirect CSV import/export and a 500-entry import limit; preserve any redirects that already absorb old campaigns. [Wix redirect CSV](https://support.wix.com/en/article/importing-or-exporting-url-redirects-with-a-csv-file).

Take dated screenshots of desktop and mobile pages for visual acceptance, but do not treat screenshots as accessible content.

## Build target in this repository

The existing static publication pipeline is:

1. `frontend/package.json`: `npm run build` runs TypeScript plus Vite and writes `frontend/dist`.
2. `.github/actions/frontend/action.yml`: downstream verification requires `frontend/dist/index.html`, packages all of `dist` into `site.tar.gz`, and records its SHA-256.
3. `.github/actions/ship/action.yml`: production uploads content-hashed `dist/assets` first with immutable caching, syncs the remaining pages to the existing private S3 site bucket, deletes obsolete objects, invalidates CloudFront, and compares live `index.html` byte-for-byte with the artifact.
4. `terraform/cloudfront.tf`: `/api/*` goes to the private EC2 origin; static paths go to the private S3 origin through OAC. Its viewer-request function currently rewrites extensionless client routes to `/index.html`.

Implement public content as ordinary repository-owned frontend routes/components and public assets, using the existing design tokens and accessible UI primitives where applicable. Keep member/client data out of the bundle. For SEO-critical public routes, extend the build to emit route-specific HTML such as `dist/about/index.html` and `dist/blog/<slug>/index.html` (with unique title, description, canonical, Open Graph data and body content), rather than relying on one JavaScript-only `index.html`. The current S3 sync already publishes additional HTML files; the CloudFront function must prefer an existing route document or explicitly map known public routes before its SPA fallback.

Generate from one reviewed content source:

- route-specific metadata and canonical URLs;
- `sitemap.xml` containing only canonical, indexable production URLs;
- `robots.txt` pointing at that sitemap;
- organization/site structured data where accurate;
- an RSS/Atom feed if the existing Wix blog exposes one and stakeholders want continuity.

Do not scrape Wix at runtime. Content changes become reviewed repository changes and use the existing deployment rollback/audit path.

## Forms replacement without a paid service

For each retained public form, implement a narrowly scoped unauthenticated FastAPI endpoint such as `POST /api/public/forms/contact`; never reuse an admin/member endpoint. Serve the form markup from the static site and post only to the same origin.

Minimum controls before launch:

- allowlisted fields, strict types and small per-field/body limits; reject attachments unless a separate reviewed storage/threat design exists;
- same-origin check, hidden honeypot, minimum-fill-time token, duplicate suppression, and a durable per-source token bucket; verify the trusted CloudFront/nginx client-IP forwarding path before using an IP-derived key;
- neutral success responses so bots cannot enumerate addresses or internal routing;
- escaped/plain-text rendering in notifications and admin views; never interpolate visitor HTML;
- SQLite storage on the existing encrypted data volume with an explicit form name, received time, status, and audit fields; no submission body in application logs, analytics, email subject, static export, or test fixtures;
- synthetic tests for valid, invalid, oversized, honeypot, rate-limited, duplicate, and hostile HTML input;
- an admin-only review/export/delete surface, or delivery into an already-owned club workflow with retry and audit evidence; a successful HTTP response must never mean a submission was silently discarded;
- the President selects a retention duration (recommended starting point: 180 days unless a form has a shorter purpose), an owner, deletion procedure, and privacy notice before collection begins.

Begin with these no-cost controls. If measured spam remains operationally significant, present a separate privacy/cost decision for a challenge service; do not silently add reCAPTCHA, Turnstile, WAF, or another processor. WAF is currently off and would add about $8/month under the documented three-rule estimate, so it is not justified solely for launch.

During migration, submit Wix and replacement forms side-by-side with synthetic values and confirm timestamps, notification routing, database visibility, error UX, keyboard use, and mobile no-clipping. Do not copy real submissions into dev.

## URL redirects and SEO acceptance

Keep every useful old slug when practical. For changed/removed URLs, maintain a reviewed exact redirect map and return **HTTP 301/308 at CloudFront**, before the SPA rewrite. Extend the existing CloudFront viewer-request function with the bounded redirect map, or generate an equivalent edge rule in reviewed Terraform. A client-side React redirect returning HTTP 200 is not an SEO substitute. Redirect each page to its closest replacement, not all pages to Home; prevent chains and loops; preserve query strings only when meaningful.

SEO acceptance before DNS cutover:

- every old indexed URL is either a matching 200 page or one-hop permanent redirect;
- each new page has one H1, unique title/description/canonical, semantic landmarks, image dimensions and useful alt text;
- public pages render meaningful content without a logged-in session and are not accidentally blocked by the member-app shell;
- internal links, downloadable assets, Open Graph/Twitter previews, structured data, `robots.txt`, sitemap, 404, keyboard navigation, contrast, mobile layout, and reduced motion are checked;
- Search Console ownership survives DNS changes; submit the new sitemap after cutover and monitor indexing/404s weekly for at least a month;
- preserve analytics only after a privacy review and consent decision; do not blindly transplant Wix tracking scripts.

## Domain and DNS cutover

CloudFront requires every alternate domain name to be lowercase, covered by the attached certificate, and routed by DNS. The certificate SAN must cover both `yaleconsulting.org` and `www.yaleconsulting.org` if both are served. [CloudFront alternate-domain requirements](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/CNAMEs.html).

For a CloudFront viewer certificate, request/validate the ACM certificate in **`us-east-1` (N. Virginia)** even though the application runs in `us-east-2`. [CloudFront certificate-region requirement](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cnames-and-https-requirements.html). Add the validated certificate and both aliases to the existing distribution through reviewed Terraform. Choose one canonical host and edge-redirect the other.

Cutover runbook:

1. At least 48 hours before: inventory/export DNS, lower only the website A/AAAA/CNAME TTL where supported, confirm domain renewal, and leave MX/TXT records unchanged.
2. Request the `us-east-1` ACM certificate and have the DNS owner add its validation CNAME. Certificate validation does not move traffic.
3. Deploy and test the candidate at the existing CloudFront hostname using synthetic forms. Because TLS/Host routing must match an attached alias, do final custom-host checks only after the alias/certificate is attached; do not use insecure certificate bypasses.
4. Add aliases and certificate to CloudFront, verify distribution status, then change `www` CNAME to CloudFront and the apex via the current provider's alias/ANAME (or Route 53 A/AAAA aliases). Do not place a CNAME at the zone apex; Route 53 aliases support the apex.
5. Verify HTTPS/certificate chain, apex↔`www` canonical redirect, representative pages/assets, every form, old-URL redirects, sitemap/robots, `/api/health`, and mail DNS. Monitor CloudFront and application errors.
6. After at least 7–14 stable days and an SEO/form audit, restore normal TTLs. Keep the Wix plan/site available but unadvertised through the rollback window; cancel hosting only after content and submission exports are archived under the named owner. Confirm separately that cancellation will not cancel the domain registration.

## Rollback

DNS rollback is the primary brake: restore the prior Wix website records while preserving all mail/TXT records. Lowered TTL reduces—but does not eliminate—resolver cache time. Keep Wix published and unchanged through the rollback window.

If only content is bad, redeploy the last known-good repository commit through the normal pipeline; S3 versioning retains noncurrent objects for 30 days, but publication is not atomic and browser/CloudFront copies cannot be recalled. If forms are bad, disable only the form route/CTA or redeploy; preserve already accepted submissions and show a truthful unavailable state rather than reporting false success. If the certificate/alias is bad, restore DNS first, then fix Terraform forward. Record cutover and rollback timestamps and every DNS value in the change record.

## Phased timeline and gates

| Phase | Typical duration | Work | Exit gate |
|---|---:|---|---|
| 0. Ownership and freeze | 2–3 days | Identify Wix/domain/DNS owners, invoices, IdP/mail dependencies; export DNS; name content/forms owners; freeze nonessential Wix edits | President supplies access/owners and approves canonical host, retention, and rollback window |
| 1. Inventory and export | 3–5 days | Crawl sitemap/public site; page/SEO/redirect inventory; export CMS, forms and redirects; folder-by-folder media; manually inventory blog; screenshots and analytics | Counts reconcile across navigation, sitemap, Wix dashboards, and analytics; every row has a disposition |
| 2. Rebuild and forms | 1–2 weeks | Accessible public routes, route HTML/metadata, optimized licensed media, sitemap/robots, FastAPI forms and admin handling, edge redirect map | Local/full relevant checks green; synthetic content only; content and forms owners accept desktop/mobile/error states |
| 3. Dev and production candidate | 3–5 days | Normal integration/dev gates; reviewed certificate/domain Terraform; deploy behind existing CloudFront hostname; test redirects/forms/SEO | Exact production candidate passes checks; ACM is issued; DNS and rollback records are ready |
| 4. Cutover | One low-traffic window | Attach aliases/cert, change website DNS, run the checklist, monitor | All critical pages, forms, old URLs, TLS, API health, and mail DNS pass; no unexplained elevated errors |
| 5. Stabilize and retire Wix | 2–4 weeks | Monitor Search Console/404s/forms, fix redirect gaps, restore TTL, final exports, then cancel only Wix hosting | President signs off SEO/forms; registrar remains controlled; Wix rollback is no longer needed |

## Decisions required from the President

1. Who controls `yaleconsulting.org` registrar and authoritative DNS, and is the domain bundled with Wix?
2. Which host is canonical: apex or `www`?
3. Which pages/blog posts/forms are still required, and who signs off each?
4. Who owns public submissions, who receives them, and what retention/privacy notice applies?
5. Is current DNS kept (recommended), or is Route 53's additional $0.50/month justified?
6. What is the current Wix monthly/annual cost and renewal date, so the exact net saving and safe cancellation date can be calculated?

No code, AWS, Wix, DNS, or production data was changed for this plan.
