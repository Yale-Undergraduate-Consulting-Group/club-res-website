# Club Resources: plan and data rules

Status: built on `feature/Andylol111` 2026-11-16; local preview imported
(`DATABASE_URL=sqlite:////tmp/preview.db`, files under
`/tmp/yucg-resources-files`). Not deployed. Supersedes the "Templates and past
work" phase of [WORKSPACE-PLAN.md](WORKSPACE-PLAN.md).

## What it is

A new main page, **Resources**: the club's library of past projects, slide
templates, interview and survey guides and analytics resources, typed,
searchable and faceted by type, semester, industry and disclosure. The compact
list groups projects by semester and other resources by type; rows expand
inline. Files use the existing document register
(`backend/app/routers/workspace.py`, files in the private documents bucket).
The Slack assistant and AI drafting search it through the same access check
as the page, so they never return what the asking member may not see.

**Ask about this file**: a member can put a question to one file they may open
(`POST /api/resources/{id}/files/{fid}/ask`, and the Slack assistant's
`ask_resource_file`). The access check is the download's. The model is sent
only the file's best-matching slides or pages (top 8 by full-text rank over
per-page text), as quoted data, and the answer cites slide or page numbers.
Admin-only files have no stored text, so they cannot be asked about. Each ask
counts against `RESOURCE_ASK_PER_MEMBER_PER_HOUR` (20) and
`RESOURCE_ASK_PER_CLUB_PER_HOUR` (200) and the club's `AI_MONTHLY_BUDGET_USD`;
asks about files not open to every member are audited like downloads. Files
imported before page text existed answer from sections of their stored text
until they are re-imported or re-uploaded.

Source of the first import: the club's OneDrive export (136 items, about 6 GB:
Past Project Database by semester, Slide Templates, Interview Resources,
Survey Resources, YUCG Analytics Resources) and its
"YUCG Project Database Table of Contents.xlsx" (about 90 projects).

The table's **Project File Link** hyperlinks are relative
(`../../../../:f:/r/sites/...`) and are stored from the host on
(`/:f:/r/sites/...`, query dropped). `SHAREPOINT_BASE_URL` (for example,
`https://tenant.sharepoint.com`) supplies the host when read, so the sharing
prefix sits straight after it; without it the page shows no button. A link
follows the same access rule as the resource's files. Admins can edit links or
add link-only resources; a SharePoint link must be https on exactly the
configured host, otherwise only Google Drive or Docs https links are accepted.

## Data store rules (AGENTS.md: client data)

| Rule | Decision |
|---|---|
| Owner | Club admins (the president and board). |
| Who reads project files | Follows the club's own **Public Disclosure?** column. "Yes - Deliverable & Name": every member. "Yes - Name Only": members see client, industry and description; files only to that project's team and admins. "No": only that project's team and admins see the project at all. |
| Who reads guides and templates | Every member. |
| Revenue, client contact names and emails | Admins only. Never returned by the Slack assistant or the AI to anyone else. |
| Project team | Engagement directors, project managers and associates named in the table, matched to member accounts by name; an unmatched name grants nothing. |
| Who manages files | Admins on every resource; on a past project, a member of its team (the same match as above, or an assignment to the linked project). Managing is uploading a new file, replacing a file with a new version, restoring an earlier version, and deleting a file. Team members never see or touch admin-only files (statements of work). Everyone else may open files as above but not change them. |
| Where it lives | Rows in the app's SQLite database; files in the private, encrypted documents bucket. Nothing in Git, fixtures, logs or static exports. Dev gets none of it (synthetic data only). |
| Retention | Kept until an admin deletes it. Replacing a file keeps every earlier version (counted in storage) so a manager can download or restore it; versions go only when the file or the resource is deleted. |
| Deletion | An admin deletes a resource, or a manager deletes one file: the rows go at once; the file versions in the bucket are deleted by the app, and any copy in the database backups ages out with those backups. Downloads already made cannot be recalled. |
| Backups | Rows are in the existing daily database backup; files rely on the documents bucket. |
| Audit | Every download of a file that is not open to all members is recorded (who, what, when) in the existing audit log; admins can read it. Every upload, restore and file delete is recorded too. |

## Cost

About $0.15 a month: 6 GB in S3 standard. Search is SQLite full-text search on
the existing server. No new AWS service. Page uploads use the documents bucket's
existing presigned PUT and count toward the uploader's storage quota (the
import's server-side stored files do not); extra versions add a little storage.

## Order

1. Resources page, import, search, compact groups and visibility-safe facets (preview on localhost, then prod on approval).
2. Slack assistant and AI drafting search tool over Resources.
3. SharePoint and Google Drive links: one-time table imports and admin-managed link-only resources. Live sync remains separate and requires Yale to allow an app (test with Graph Explorer).
4. Outreach links: a company being contacted shows related past projects the member may see.
5. Later, separately planned: cost trims, security hardening, custom domain, alumni database (needs its own data rules first), client share links and a client portal design.
