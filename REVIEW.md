# REVIEW.md

The diff-triggered checklist for reviewers, human or Claude. Check only the rules whose trigger appears in the diff. Project-wide policy stays in [AGENTS.md](AGENTS.md).

Each rule is backed by an incident. Add one only when it recurs or an incident shows it, and say how to read its trigger from a diff. When a finding rests on a rule, name the rule so the author need not work it out.

## Rules

### Parsing Office, PDF or zip files

- **Trigger:** code that parses Office, PDF or zip files, or imports member or club files.
- **Check:** it never loads a whole file into memory (`python-pptx` `Presentation()`, a whole-member `read()`). It streams, or reads only the parts it needs. The PR states the peak memory for the largest real file.
- **Incident (2026-11-16):** a 685 MB deck needed about 700 MB in python-pptx, and the production import was OOM-killed three times. Fixed in `31f995d` by reading the slide XML.

### Staging large files

- **Trigger:** ops scripts, SSM runbooks, or import and staging paths.
- **Check:** large files never stage on `/data`, the 8 GB volume holding the live SQLite database. Use the root volume.
- **Incident (2026-11-16):** a 6 GB staging zip filled `/data` to 100% on prod for about a minute.

### Fields derived from client sheets or resources

- **Trigger:** a new field derived from client sheets or resources (revenue, contacts, links, team), or a change to resources visibility.
- **Check:** a test asserts the field's keys are **absent**, not merely empty, for non-admins. A positive control shows admins do get them.
- **Incident (2026-11-16):** a live check reported "no leak" while also showing 0 for admins. It inspected the wrong layer.

### Deleting member or client data from S3

- **Trigger:** S3 deletes of member or client data.
- **Check:** the documents bucket is versioned. A plain delete leaves noncurrent versions for 30 days (lifecycle). The PR says so, or deletes by `VersionId` with a principal that is allowed to.
- **Basis:** no incident is recorded for this rule. It follows from the bucket configuration in `terraform/storage.tf` (versioning on, noncurrent versions expire after 30 days), and AGENTS.md separates object deletion from deletion of all copies.

## How this file is used

The review gate reads `REVIEW.md` from the default branch once merged, never from the pull request, so a change cannot edit the rules that judge it. Until it is merged, nothing reads it automatically, and no workflow in this repository uses it yet.
