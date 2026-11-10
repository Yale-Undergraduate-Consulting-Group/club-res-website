# One place for the club's documents (replacing SharePoint)

Status: agreed direction, not built. Recorded 2026-11-10. SharePoint is a drag
on the club: files live in a separate system with its own folders and access
rules. The aim is one place where every project's documents, tasks and
updates are organized and managed, next to the outreach that feeds them.

## What already exists in code

- A document register (`backend/app/routers/workspace.py`, `workspace_schema.py`):
  upload by presigned URL, immutable versions, private / project / club
  visibility enforced server-side, expiring revocable share links, per-member
  storage quotas, audit entries. `frontend/src/pages/Documents.tsx` is a full UI
  for it but is not in the menu.
- Projects and project membership (`projects`, `user_project_assignments`,
  Admin → Projects). No status, milestones or tasks.
- Slack: read-only assistant and a daily digest of audit events.
- One admin-only OneDrive import (`services/graph_onedrive.py`, global token).
- Missing: tasks, announcements, calendar, comments, activity feed, delete or
  rename of documents. Deleting a project leaves its documents' `project_id`
  dangling (`routers/admin.py`). Legacy `email_attachments` is a separate store.

## Decision

The platform is the hub; editing stays in Google Docs / Office. Rebuilding
co-editing is not worth it. Members already sign in with Yale Google accounts.

## Phases

| Phase | What | Size |
|---|---|---|
| 1. Project spaces | Each project gets a page: team, client, status, milestones, files, its outreach. Documents in the menu, with delete and rename. Guard project deletion. Move `email_attachments` into the register. | 1-2 weeks |
| 2. Coordination | Tasks per project (owner, due date, status); announcements (club or project, also posted to Slack); activity feed per project; weekly Slack digest of changes and what is due. | 1-2 weeks |
| 3. Templates and past work | A library of past decks, templates and case notes with tags; title and tag search on the same FTS5 approach as the register; Drafts can cite it. | 1 week |
| 4. Calendar | An ICS feed of milestones and due dates that members subscribe to in Google Calendar, instead of building a calendar. | 2-3 days |
| 5. Moving off SharePoint | One-time import: export the SharePoint folders, upload, map to projects and the library. Optional direct Microsoft import needs a Yale admin's consent. | 3-5 days |
| 6. Client access | Separate reviewed design (a client-facing app gets its own deployment and data boundary, docs/PLATFORM.md). Until then client files stay in projects with expiring share links. | Separate |

Cost: about $0 more on AWS. Files go in the existing private, encrypted
documents bucket; no new service.

## Open questions before Phase 1

1. What SharePoint holds today: project folders, templates, client
   deliverables, roughly how much data, and who has access.
2. Whether members co-edit Office files in SharePoint (link Google Docs,
   Office files, or both).
3. Which content is under a client NDA (visibility and retention rules).
