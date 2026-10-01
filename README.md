# AI Choice Policy Ingestion

Standalone **offline** data-preparation project for the **AI Choice Map**.

It owns the structured policy ingestion sources for the map:

1. MultiState — **How States Are Regulating AI in Education this Legislative Session** (source snapshot: **2026-04-09**)
2. AI Laws by State — **Education AI Tracker**
3. EdChoice — **School Choice in America Dashboard** (`Universal=Full` and `Universal=Eligibility` for the `Universal School Choice` category)

Source URLs:

- `https://www.multistate.us/insider/2026/4/9/how-states-are-regulating-ai-in-education-this-legislative-session`
- `https://www.ailawsbystate.com/tools/education-ai-tracker`
- `https://www.edchoice.org/school-choice/dashboard/`

This project is separate from the React/Vite UI, Node/Express runtime backend, and chatbot RAG ingestion project.

## What changed in v2

The original importer flattened too much information. v2 now preserves:

- a stable `policy_identifier` such as `SB 1227`,
- `policy_type` separately from status,
- a primary `category` plus all applicable `categories[]`,
- a stable filter `status` plus the source's detailed `status_detail`,
- implementation timing separately from legal `effective_date`,
- official source URL separately from research/discovery provenance,
- `status_as_of_date`, `last_updated`, and optional independent `last_verified_at`.

It also enriches the policy summaries so that useful details from the MultiState article are not silently dropped. For example, Illinois SB 3735 now reflects family opt-out rights and consent-related limits on student-data use for AI training.

## Source coverage

The article says MultiState was tracking **134 AI-in-education bills across 31 states**. The page itself explicitly names only the highlighted **13 bill records across 11 states** in its three `Key Bills` sections.

This adapter ingests **all 13 named bill records**. It does **not** pretend that the page supplies all 134 tracked bills or a complete 50-state dataset.

For safety, the live parser knows the expected 13 bill identifiers. If the dated article changes and a named bill is missing or an unexpected bill appears, ingestion stops for review instead of silently loading a partial dataset.

## Database schema

Use:

```text
database/01_table.sql
```

for a fresh `public.policies` table.

Important: if the old v1 table already exists, rerunning a `CREATE TABLE IF NOT EXISTS` script will **not** upgrade it. Since the current map data is still mock/early-stage, the simplest approach is usually to recreate the table with the v2 schema before loading real records. If existing production records must be preserved, create a migration instead of dropping them.

## Data flow

```text
Julia's MultiState article
        ↓
identify every explicitly named Key Bill + official bill link
        ↓
preserve article status and primary section
        ↓
source-specific normalization/enrichment
        ↓
validate 13 expected policy rows
        ↓
review CSV + DB CSV + idempotent SQL + source metadata
        ↓
optional explicit Supabase upsert
```

No OpenAI call is required for this ingestion.

## Setup

Windows PowerShell:

```powershell
cd frontier-policy-ingestion
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

## 1. Test with the bundled normalized snapshot

```powershell
python -m app.cli ingest-multistate --use-fixture
```

Expected result:

- 13 policy rows
- 11 states
- 1 enacted / 12 pending status groups
- 0 validation errors

Generated files:

```text
output/policies_review.csv
output/policies.csv
output/policies.sql
output/validation.json
output/source_metadata.json
output/ingestion_report.md
```

### `policies_review.csv`

Review this first. It includes the complete database-facing representation in an easy-to-read form. Multiple categories are separated with ` | `.

### `policies.csv`

Matches the v2 database columns. The `categories` column is encoded as a JSON array so the optional direct loader can send it correctly to Supabase/PostgREST.

### `source_metadata.json`

Keeps source-level context that should not be repeated in every policy record, including the article's broader reported tracking totals and the three policy sections.

## 2. Run against the live dated article

```powershell
python -m app.cli ingest-multistate
```

The parser extracts:

- the three relevant policy sections,
- every explicitly named bill in those sections,
- official state legislative/government links,
- detailed legislative status.

The adapter then applies reviewed source-specific normalization for richer summaries, multiple categories, and implementation timelines.

## 3. Review before loading

Check:

```text
output/policies_review.csv
output/validation.json
output/ingestion_report.md
```

Do not load if validation reports errors.

## 4A. Controlled load with Supabase SQL Editor

Review and run:

```text
output/policies.sql
```

The upsert key is:

```text
(state_code, policy_identifier, source_url)
```

so rerunning the same source updates matching records rather than creating duplicates.

## 4B. Optional direct Supabase load

`.env`:

```env
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SECRET_KEY=YOUR_SERVER_SIDE_SECRET_KEY
```

Dry run:

```powershell
python -m app.cli load-supabase
```

Actual write only after review:

```powershell
python -m app.cli load-supabase --apply
```

Never put the Supabase secret key in React or commit `.env`.

## Important date semantics

For this source:

```text
research_source_date = 2026-04-09
status_as_of_date     = 2026-04-09
last_updated          = 2026-04-09
last_verified_at      = null
```

That means: *this status is supported by the Julia-provided April 9 source snapshot.* It does not claim Frontier independently checked each official bill page on the day the ingestion script ran.

When AI Choice later re-checks an official source, `last_verified_at` can be populated and the relevant status/date fields can be updated through a verification workflow.

## Tests

```powershell
pytest -q
```

## Adding another source later

Create another adapter under:

```text
app/sources/
```

Normalize it into `PolicyRecord`. Do not put source scraping/parsing into the React frontend or runtime Express server.


# AI Laws by State — Education AI Tracker


Julia supplied the public **AI Laws by State — Education AI Tracker** as an additional research source for the existing AI Choice Map. It is additive to MultiState and uses a separate adapter; the MultiState parser is not mixed with or replaced by this source.

Source:

```text
https://www.ailawsbystate.com/tools/education-ai-tracker
```

## First-run review command

```powershell
python -m app.cli ingest-ai-laws-education
```

Optional explicit existing-policy snapshot for duplicate/conflict comparison:

```powershell
python -m app.cli ingest-ai-laws-education --existing-csv ".\output\policies.csv"
```

Offline fixture run used for tests/review:

```powershell
python -m app.cli ingest-ai-laws-education --fixture ".\data\fixtures\ai_laws_education\sample.json"
```

This command **never writes to Supabase**. It fetches/normalizes the source, compares it with the existing MultiState dataset, validates it, and writes review artifacts under `output/`. SQL is generated only if there are no blocking validation/review errors.

Generated artifacts:

```text
output/ai_laws_education_raw.html
output/ai_laws_education_raw.json
output/ai_laws_education_normalized.csv
output/ai_laws_education_review.csv
output/ai_laws_education_validation.json
output/ai_laws_education_duplicates.csv
output/ai_laws_education_conflicts.csv
output/ai_laws_education_source_metadata.json
output/ai_laws_education_ingestion_report.md
output/ai_laws_education.sql   # only when validation fully passes
```

## Normalization and provenance rules

- `research_source_name` is `AI Laws by State — Education AI Tracker`.
- `research_source_url` prefers the specific AI Laws record page.
- `source_url` must be an external official government/legislative source; the aggregator URL is never substituted.
- `Key Requirement` is used as the concise summary; the ingestion does not generate new legal conclusions.
- Existing AI Choice categories are retained. Source categories are normalized deterministically. For generic `Teacher Use of AI / PD` rows, the public detail-page title is used as a second source-supported signal; rows still ambiguous after that check remain review-only.
- `Introduced`, `In Committee`, `Passed One Chamber`, and `Passed Both Chambers` map to `Pending`; `Enacted` remains `Enacted`; terminal `Dead`/`Dead/Failed`/`Failed`/`Vetoed` map to `Inactive`; `Unknown` remains `Unknown`.
- The exact source status is preserved in `status_detail`.
- `effective_date` is populated only when the detail page explicitly exposes an effective date.
- Deduplication primarily uses normalized `state_code + policy_identifier`; explicit official-source year/session evidence is used only when needed. Known odd/even two-year session handling prevents false year conflicts for NY/WA/CA. The MultiState research snapshot date is never treated as a bill year.
- Existing MultiState provenance is preserved. Duplicate matches are review-only and are excluded from generated SQL. Duplicate/conflict outputs include merge recommendations; no incoming value automatically overwrites an existing reviewed record.
- District of Columbia is supported as `DC`. Federal tracker rows are retained in review output but are not eligible for the state/DC map.
- If the tracker publishes a total such as `Showing N of N bills`, the extracted row count must match or validation blocks SQL generation.
- The ingestion also performs a conservative source-consistency check between tracker year/category/requirement and the public detail title/official-source URL. Suspicious but structurally valid rows become `POSSIBLE_MATCH_REVIEW` instead of `NEW`.

Full mapping and review rules are documented in:

```text
docs/ai_laws_education_mapping.md
```

## Important repository boundary

The supplied archive contains the ingestion/database schema code but not the deployed Node policy API or React AI Choice Map source. No frontend/backend code is changed here. Before production import, verify the deployed API/UI handles `Inactive`, `Unknown`, and `DC` as intended.

### Reviewed AI Laws dispositions

`ingest-ai-laws-education` now has a separate post-review disposition layer in
`app/ai_laws_review_resolutions.py`. It does not weaken the generic parser. Explicitly
verified rows can receive a reviewed category mapping, while reviewed bad-source rows,
federal rows, and tracker/detail title mismatches are retained in CSV/JSON review artifacts
and excluded from SQL. The review CSV includes `review_disposition` and
`review_resolution_note` for auditability.

SQL is generated only for `NEW` records that pass DB validation and are not excluded. A
reviewed exclusion does not block SQL for other clean records; an unresolved review item still
does. The command never writes to production Supabase.

### Bill identity consistency gate

The AI Laws Education adapter also cross-checks the identity encoded in the AI Laws detail URL against the official-government title returned by the detail page. Rows with conflicting subject matter (for example a school bill title paired with a political-advertising or autonomous-vehicle detail identity) are classified for manual review and are not SQL-eligible. A separate scope guard also holds rows whose official title is clearly centered on a non-education domain even when it contains AI/privacy terminology. These are conservative review gates; they do not rewrite or infer legal content.

### Reviewed bill-identity / scope exclusions

The bill-identity safety pass may deliberately stop SQL generation when an AI Laws detail
record resolves to an official bill whose identity or subject matter does not align with the
Education AI Tracker record.  After manual review of the 2026-09-28 live snapshot, those
specific rows are recorded in `REVIEWED_IDENTITY_SCOPE_EXCLUDES` in
`app/ai_laws_review_resolutions.py`.

They remain present in normalized/review outputs for auditability, but receive either
`EXCLUDE_BILL_IDENTITY_MISMATCH` or `EXCLUDE_NON_EDUCATION_SCOPE` and are never emitted to
SQL.  This list is identifier-specific; future newly detected mismatches are still unresolved
blockers until reviewed rather than being silently excluded.

### Reused bill-number/session safety

The AI Laws adapter also compares the tracker `Year` with the bill detail page's
`Last Action` date. A gap greater than one year is treated as a bill-identity review
because state bill numbers are routinely reused across legislative sessions. This
protects against a tracker row or detail page silently resolving to a different bill
with the same number. Normal same-year and adjacent-year legislative-session activity
remains allowed. Reviewed session collisions stay in review artifacts and are excluded
from generated SQL.

### K-12 scope and tracker Year handling

The Education AI Tracker's `Year` value is retained as source metadata, but it is not used as a legislative-session identifier. Live records can use a year that aligns with an effective date or otherwise differs from the bill session. Bill identity is instead checked using the AI Laws detail identity and the official source/title.

Because the AI Choice Map is K-12 scoped, clearly postsecondary-only and professional-licensing-only records are kept in review artifacts but excluded from generated SQL. Mixed measures that explicitly cover K-12 schools or school districts remain eligible.

# EdChoice — Universal School Choice

The AI Choice Map now supports the exact normalized category:

```text
Universal School Choice
```

EdChoice is additive to MultiState and AI Laws by State. It is not copied into chatbot/document RAG; the map and chatbot continue to consume structured policy rows from the existing `policies` dataset.

Research source:

```text
https://www.edchoice.org/school-choice/dashboard/
```

## Review command

Live dashboard review:

```powershell
python -m app.cli ingest-edchoice-universal-school-choice --existing-csv ".\output\policies.csv"
```

Offline source-supported fixture review:

```powershell
python -m app.cli ingest-edchoice-universal-school-choice --fixture ".\data\fixtures\edchoice_school_choice\sample.json" --existing-csv ".\output\policies.csv"
```

The live command first looks for the dashboard's server-rendered table. If the dashboard table is client-rendered and absent from the HTML response, it automatically falls back to EdChoice's first-party `Universal School Choice` page. Every program listed under **Universal Eligibility** qualifies for the AI Choice `Universal School Choice` category. Programs that also appear under **Universal Options** and **Universal Funding** for a state EdChoice lists as **Truly Universal** are retained as `Full`; the remaining qualifying programs are retained as `Eligibility`.

If both first-party HTML paths become unavailable or ambiguous, do not scrape visual cards. Export/copy the dashboard table to CSV and use the manual review path:

```powershell
python -m app.cli ingest-edchoice-universal-school-choice `
  --input-csv ".\data\edchoice_dashboard_export.csv" `
  --source-date "2026-08-27" `
  --existing-csv ".\output\policies.csv"
```

Primary qualification is dashboard `Universal=Full` **or** `Universal=Eligibility`. In automatic fallback mode, the Universal School Choice page supplies equivalent first-party evidence from its **Universal Eligibility** section. `N/A` and other non-qualifying dashboard values remain excluded.

Generated artifacts:

```text
output/edchoice_universal_school_choice_raw.html
output/edchoice_universal_school_choice_universal_page_raw.html
output/edchoice_universal_school_choice_raw.json
output/edchoice_universal_school_choice_normalized.csv
output/edchoice_universal_school_choice_review.csv
output/edchoice_universal_school_choice_validation.json
output/edchoice_universal_school_choice_duplicates.csv
output/edchoice_universal_school_choice_conflicts.csv
output/edchoice_universal_school_choice_changes.csv
output/edchoice_universal_school_choice_source_metadata.json
output/edchoice_universal_school_choice_ingestion_report.md
output/edchoice_universal_school_choice.sql
```

The command is review-only: it never invokes the Supabase loader. SQL is emitted only when all `NEW` rows are DB-ready and there are no unresolved validation/review blockers. Existing records are never automatically overwritten or deleted.

Official/legal URLs remain separate from EdChoice research provenance. `source_url` is a verified government/statute/program URL; EdChoice remains in `research_source_*`. A small reviewed override file (`data/edchoice_official_source_overrides.json`) resolves official government URLs when EdChoice itself links to a non-government statute mirror. These overrides cannot make a program qualify; qualification always comes from EdChoice's own dashboard or Universal School Choice classification.

For an existing database, review and apply this schema-only category migration before inserting approved rows:

```text
database/02_add_universal_school_choice_category.sql
```

It only extends the existing category CHECK constraints; it does not insert or modify policy records.

Full extraction, mapping, validation, deduplication, and change-detection rules are documented in:

```text
docs/edchoice_universal_school_choice_mapping.md
```

## Refresh procedure

Refresh the structured map sources independently and review each source before any database load:

```powershell
python -m app.cli ingest-multistate
python -m app.cli ingest-ai-laws-education --existing-csv ".\output\policies.csv"
python -m app.cli ingest-edchoice-universal-school-choice --existing-csv ".\output\policies.csv"
pytest -q
```

The first command refreshes the reviewed MultiState snapshot outputs. The second refreshes AI Laws by State. The third reviews EdChoice Universal School Choice records, accepting dashboard `Universal=Full` and `Universal=Eligibility` and automatically using the first-party Universal Eligibility section as the fallback when needed. Both additive-source commands compare incoming records against the supplied current structured-policy snapshot and never write to Supabase. Review `validation.json`, `policies_review.csv`, the `ai_laws_education_*` files, and the `edchoice_universal_school_choice_*` files before any manual load.
