# Product design system

The interface should feel like one calm operations workspace. It should expose responsibility, evidence, and the next decision without presenting every backend capability at once.

## Information architecture

Use five top-level destinations:

1. **Home** — assigned work, recent outreach, sync health, and exceptions that need attention.
2. **Projects** — project overview, members, documents, and external shares.
3. **Contacts** — companies, people, discovery/import, address evidence, and saved target lists.
4. **Outreach** — Drafts, Campaigns, Pipeline, and Results.
5. **Admin** — Members, Invitations, Projects, Audit, Integrations, Usage, and Infrastructure status.

Personal profile, Gmail connection, signature, notifications, theme, and sign-out belong in the member menu. They are not primary product destinations.

Existing routes can migrate without a flag day:

| Current route | Destination |
| --- | --- |
| `/scraper` and company discovery | Contacts → Find contacts |
| `/yucgoutreach` | Contacts → Target lists |
| `/studio` | Outreach → Drafts |
| `/campaigns` | Outreach → Campaigns |
| `/outreach` | Outreach → Pipeline |
| `/analytics` | Outreach → Results |
| `/documents` | Projects → Documents, with a club-library saved view |

## Product language

Labels should describe a member action or a real object. Avoid internal implementation terms.

| Avoid | Use | Meaning |
| --- | --- | --- |
| Week | Target lists | A saved set of companies and people for an outreach effort |
| Slate | Companies | Candidate companies in a target list |
| Comb | People | Review people and keep the ones to contact |
| Mint candidate | Add suggested contact | Generate a possible address from an observed company pattern; still unconfirmed |
| Release | Target list | The frozen selection feeding contact review; reserve “release” for software delivery |
| Rebuild Think-Cell pack | Refresh outreach workbook | Recreate the export after selection changes |
| MX inbox check | Mail-domain check | Confirms a domain mail route, not a person's inbox |
| AI verdict | Evidence review | A model may summarize evidence; it does not verify identity |
| Generate email | Create starting draft | The member remains responsible for claims and final text |
| Cache | Draft history | Member-owned saved/generated drafts |
| Pipeline run | Campaign delivery | A controlled attempt to send frozen messages |

Use sentence case. Prefer “Save draft,” “Review recipients,” and “Send campaign” over title-case controls. Status language must state whether the system observed, inferred, or awaits an outcome.

## Page anatomy

Every ordinary page uses this order:

1. Breadcrumb only when below a list/detail route.
2. Page header with title, one-sentence purpose, scope control, and at most one primary action.
3. Status strip only for actionable system state such as disconnected Gmail, stale sync, quota, or unavailable data.
4. Filter/search toolbar.
5. Primary content.
6. Context panel or routable detail view.

The document should scroll once. Studio and dense pipeline boards may use a viewport workbench, but their header and primary actions stay visible and inner scroll regions must have labels, keyboard access, and tested focus behavior.

## Visual foundation

Preserve Yale navy, the YUCG mark, and Lato. Operational pages use quiet surfaces and minimal decoration. Photography belongs on login and editorial/help content.

Adopt one semantic token layer:

```css
:root {
  --canvas: #f5f7fa;
  --surface: #ffffff;
  --surface-subtle: #eef2f6;
  --ink: #102442;
  --ink-muted: #52647a;
  --border: #cbd5df;
  --accent: #00356b;
  --accent-hover: #002b57;
  --focus: #286dc0;
  --success: #16794b;
  --warning: #9a5b00;
  --danger: #b42318;
  --radius-control: 0.375rem;
  --radius-panel: 0.5rem;
  --space-unit: 0.25rem;
}
```

Spacing uses multiples of 4px, with 8/12/16/24/32px as the normal scale. Use one-pixel borders, a restrained panel radius, and elevation only for overlays. Remove competing `--bg-*`, `--text-*`, color-name, and late override layers only as each component migrates; do not add a third compatibility layer.

## Required primitives

- **PageHeader**: title, purpose, scope, one primary action.
- **TaskTabs**: real tab semantics, keyboard navigation, and linked panels.
- **Toolbar**: search, filters, selection count, saved view.
- **DataList**: table on wide screens and equivalent cards on narrow screens.
- **DetailPanel**: routable on desktop and mobile; browser back restores context.
- **StatusBanner**: info, success, warning, and error with a recovery action.
- **EmptyState**: distinguishes no records, no matches, unavailable data, forbidden access, and first-use guidance.
- **Dialog/Drawer**: shared focus trap, escape handling, focus restoration, and destructive confirmation.
- **OwnerScope**: member, project, visibility, and sender metadata shown in the same compact pattern.
- **IntegrationStatus**: provider identity, granted purpose, last sync, failure, reconnect, and disconnect.
- **FilePicker**: upload, S3 document, Drive, or OneDrive source with destination scope and transfer state.

Native `alert` and `confirm` calls should disappear as touched flows migrate to shared notices and dialogs. Request failures must not become empty arrays.

## Core experiences

### Contacts

Use a single acquisition inbox for spreadsheet imports, company discovery, and suggested addresses. Each row shows source, last checked time, confidence, and an evidence label. Members explicitly accept a result into the shared contact catalog. A model recommendation is displayed as a recommendation with cited inputs.

### Studio

The default canvas is the message editor. Recipient and campaign context sit in a compact left rail; optional draft assistance opens in a right panel. The member supplies an outcome and verified facts. Claude returns a starting draft without signature, invented research, proof, or client claims.

Before a draft enters a campaign, show a review checklist: recipient, sender account, subject, unsupported-claim warning, attachment/share access, signature, and last saved time. Preview Gmail rendering as a secondary mode rather than a competing permanent column.

### Campaigns

Separate **My campaigns** from **Club activity**. Only My campaigns contains send controls. The review screen names the exact Gmail sender and recipient count before release. Shared activity shows sender attribution and outcomes without message bodies or credentials.

### Projects and files

A project detail page contains Overview, Members, Documents, and Sharing. “Upload file” creates the record and first version together. Version history and share links live in a side panel/detail route. Every file states owner, location, visibility, version, and whether an external recipient can open it.

### Results

Start with sent, delivered/bounced, replies, and meetings. Opens remain a supporting signal with explanatory text. Filters for period, campaign, sender, and project apply consistently to every chart and table. Always show last successful Gmail synchronization.

## AI drafting quality contract

The generator receives structured, escaped reference data under a system instruction that treats it as untrusted. It may use only supplied facts and a small approved YUCG organization profile. It returns subject/body JSON, and the application appends the member signature separately.

Quality evaluation should include fixed briefs for sparse context, an announced company event, a supplied case study, adversarial instructions inside a contact field, and an unsupported-metric request. A passing draft:

- contains no invented fact, relationship, metric, or research claim;
- has one clear reason for contact and one modest call to action;
- stays within the selected length;
- omits a duplicate signature and automation language;
- preserves all policy rules when brief data contains instructions.

Model output never skips member review or campaign release controls.

## Connector experience

Google Drive and OneDrive use the same product flow even though their APIs differ:

1. Connect a personal provider identity for the explicit file-selection purpose.
2. Pick an authorized file or folder using paginated provider results.
3. Choose **Copy to project** or **Link from provider**.
4. Choose private/project/club visibility.
5. Show transfer progress, checksum/version evidence, and final location.
6. Before email use, choose a small attachment or an expiring recipient link.

A club service account is reserved for an explicitly administered club library. It cannot act as a generic member connector. Revoked consent, moved files, provider permission changes, and oversized transfers need distinct recovery states.

## Delivery gates for design work

| Stage | Required experience evidence |
| --- | --- |
| Topic → develop (Intake) | Type/lint/build; targeted backend tests; changed-route accessibility; truthful terminology check; IaC synthesis/static contract when infrastructure changed; no deployment |
| develop → feature (Beta) | Full backend suite and critical coverage; browser tests across desktop/mobile, light/dark, populated/empty/error/forbidden states; image scan and non-root smoke; beta delivery disabled; external providers mocked |
| feature → main (Production) | All security boundaries; full browser regression; immutable image digest; Terraform validation and reviewed plan metadata when applicable; production environment approval; controlled post-deploy health/rollback evidence |

A small patch may select fewer Intake/Beta checks only when it changes documentation, presentation, or an isolated tested behavior. Authentication, authorization, sending, tracking, dependencies, workflows, containers, storage, integrations, and infrastructure always use the standard lane. Production expands every executable change to the full gate.

## Implementation sequence

1. Close safety gaps in beta bootstrap, Gmail disconnect, campaign mutations, and the legacy OneDrive connector.
2. Fix false or operator-facing language and introduce distinct unavailable/empty/forbidden states.
3. Consolidate tokens and primitives on Contacts and Documents, then delete the replaced CSS rules.
4. Introduce the five-destination shell and route-backed detail views with redirects for old links.
5. Rebuild Studio around editor-first composition and the grounded drafting contract.
6. Move pooled attachments and generated objects into the authorized file register.
7. Add delegated Drive/OneDrive connectors and multipart S3 transfers behind integration tests.
8. Rework Results and Home once the shared scope/filter model is stable.

Each slice should remain deployable, pass its selected gates, and improve one complete member journey. Avoid simultaneous visual rewrites of every route.
