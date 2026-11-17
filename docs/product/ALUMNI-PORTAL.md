# Alumni portal

## Product boundary

The alumni portal is a separate signed-in area at `/alumni`, for alumni (mainly
the alumni board) to communicate and keep in touch: news posts, events, an
opt-in directory, and messages to the alumni board. Alumni get no club data: no
Resources, projects, members, clients, outreach or any other member page or API.
Alumni sign in on YUCG Connect (`/login`), the same page and Google sign-in as
members; the server sends each person to the member site or the portal. Someone
who is both a member and on the alumni lists lands on the member site with a
"Switch to alumni" link (a member with two-factor gets the alumni session only
once the code is accepted), and the portal shows a "Member site" link while their
member session is valid; signing out of either ends both. The sign-in itself (who
may sign in, the alumni session, revocation) is described in
[../night/security.md](../night/security.md).

Club admins decide who may sign in (Admin → Alumni → Access: domains and the
allow-list) and which alumni accounts are board members. Board members and club
admins publish posts and events and work the board inbox; only club admins manage
the sign-in lists and the board role. Messages to the board stay in the portal: no
email is sent, there is no new mail service, an alumni account has no member Gmail
connection, and the application does not borrow a member's mailbox for board mail.

Current members do not see alumni posts: the two areas are kept separate. Showing
alumni news to members later is an option; it is a separate product and
authorization decision (who among members, and whether alumni are told).

Code: `backend/app/routers/alumni_portal.py` (routes, tables: migration `core:7`),
`backend/app/services/alumni_covers.py` (cover images),
`backend/app/services/safe_html.py` (rich-text sanitiser),
`frontend/src/pages/AlumniHome.tsx` and `frontend/src/components/alumni/`.
Tests: `backend/tests/test_alumni_portal.py`, `backend/tests/test_alumni.py`.

## Data-store specification

Owners: the alumni board and club admins. Every table below lives in the existing
SQLite database, so it is in the daily encrypted SQLite backup to the backups
bucket and the seven daily EBS snapshots. Backups expire within 30 days, so a
deleted row can remain in a backup for up to 30 days; that is the deletion limit to
tell an alumnus who asks to be forgotten. Audit rows go to the existing `audit_log`
(Admin → Activity); an action by an alumni account has no member `user_id` and
names the account as `alumni_account_id` in the details.

| Table | Data | Readers | Retention and deletion | Audit |
|---|---|---|---|---|
| `alumni_domains` | Eligible Google email domain, adding club admin and time | Club admins only | Until a club admin removes it. Removing it ends affected sessions on the next request and hides their directory profiles. | Add/remove |
| `alumni_allowlist` | Eligible email, admin-only note, adding club admin and time | Club admins only | Until a club admin removes it. Removal ends access unless a domain still allows the address. | Add/remove |
| `alumni_accounts` | Email, Google id and name, sign-in times, revocation, board flag (`board_since`) and the club admin who last changed it (`board_set_by`) | The account reads its own email, name and board status; club admins read all | Until a club admin deletes it. Revocation ends access at once and hides the directory profile. Deletion removes the account, its sign-ins, its directory profile and its rate-limit records; published posts and events stay with the generic `YUCG Alumni Board` byline, and messages stay without a sender so the board keeps what it received. | Revoke, restore, delete, board add/remove |
| `alumni_sign_ins` | Time, known account id, email domain, outcome | Club admins only | 365 days; account-linked rows are removed with the account | The table is the sign-in audit |
| `alumni_posts` | Title, server-sanitised rich-text body, draft/published state, pin, optional cover image, author (alumni account or club admin) and timestamps | Published posts: signed-in alumni and club admins. Drafts: board members and club admins | Until a board member or club admin deletes it; deleting a post deletes its cover. Account deletion clears only the author link. | Create, update (with the previous state and pin), delete |
| `alumni_post_images` | Private object key and S3 version, byte size, declared and verified type (PNG, JPEG or WebP), uploader, state | Through a short-lived, access-checked app link: anyone who may read the published post it covers; board members and club admins for drafts and unattached uploads | A cover lasts as long as its post. Replacing or deleting a cover deletes its row and object. An upload no post uses is deleted at the next cover upload once it is a day old. Refused uploads are deleted at once. In S3 the named version is deleted; any other noncurrent version expires with the documents bucket's 30-day lifecycle. | Reservation (with the number of stale uploads removed), completion, refusal; replacement and deletion are in the post's update/delete record |
| `alumni_events` | Title, start time (UTC) with its IANA time zone, place and/or HTTPS online link, server-sanitised description, author and timestamps | Signed-in alumni and club admins | Until a board member or club admin deletes it. Account deletion clears only the author link. | Create, update, delete |
| `alumni_profiles` | Display name, class year, company, role, city, LinkedIn URL, short note, open-to-coffee-chats flag, listed flag, show-email flag | The owner always. Listed profiles: signed-in alumni and club admins. The email appears only when `show_email` is on | No row until the alumnus saves one; a saved profile is unlisted unless they choose otherwise. The owner can hide it (unlist) or delete the row; both take effect at once. Revocation or removal from the lists hides it; account deletion removes it. | Save (with listed and show-email), hide, delete |
| `alumni_board_messages` | Sender account, subject, plain-text message, time, read and archived times | Board members and club admins. The sender sees only the confirmation that it was sent | Until a board member or club admin archives and then deletes it. Account deletion clears the sender link and keeps the message. No email copy exists. | Create, every opening by the board (read), archive, restore, delete |
| `alumni_rate_events` | Actor (alumni account or club admin), action kind, time | Nobody reads it; the server counts it | Rows older than an hour are deleted at the next counted action; deleted with the account | — |

## Authorization and abuse controls

- Every `/api/alumni/*` request reads only the alumni cookie and re-checks the
  account (not revoked, still allowed). Every `/api/admin/alumni/*` request needs
  a club admin's member session. The board flag grants authoring and inbox access
  only inside the alumni portal: it never opens a member or admin route. A member
  session cannot call the alumni routes, and an alumni token is refused by every
  member route however it is presented. `test_alumni_portal.py` checks every portal
  route for anonymous, member, club admin, alumnus and board member;
  `test_alumni.py` checks every other route the server serves.
- Ordinary alumni read published posts and events and the listed directory, edit
  their own profile and write to the board. They cannot write, edit or delete
  posts, manage events, read the inbox, mark board members or change the lists.
- Rate limits, per actor per rolling hour: 5 messages to the board; 60 writes
  (post and event create/update/delete and cover uploads). Over the limit: 429.
- Post bodies and event descriptions are sanitised on the server before storage
  (an allow-list: paragraphs, line breaks, bold/italic/underline/strike, quote,
  code, two heading levels, lists, links) and again in the browser before
  rendering. Links keep only absolute `http(s)` or `mailto` addresses; scripts,
  styles, embedded content, event-handler attributes, `javascript:` and `data:`
  links are removed.
- Cover images use the document register's flow: the server reserves a private
  key in the documents bucket, the browser PUTs to a presigned URL (with no
  bucket, on localhost, to the app's own short-lived upload link), and the server
  completes the upload. At most 5 MB; PNG, JPEG or WebP only. On completion the
  server reads the first 16 bytes (a ranged read, never the whole file) and
  checks the magic bytes match the declared type; SVG, HTML and anything else is
  deleted and refused. Covers are served only by the app, streamed, behind the
  reader's session and a 10-minute signed link, with the verified
  `Content-Type`, `Content-Disposition: inline` and
  `X-Content-Type-Options: nosniff`.
