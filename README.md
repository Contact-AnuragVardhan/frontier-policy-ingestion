# Frontier Policy Ingestion v2

Standalone **offline** data-preparation project for the Frontier Education Project **EdTech Map**.

It consumes the source Julia supplied for the map:

- MultiState — **How States Are Regulating AI in Education this Legislative Session**
- Source date: **2026-04-09**
- URL: `https://www.multistate.us/insider/2026/4/9/how-states-are-regulating-ai-in-education-this-legislative-session`

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
cd frontier-policy-ingestion-v2
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

When Frontier later re-checks an official source, `last_verified_at` can be populated and the relevant status/date fields can be updated through a verification workflow.

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
