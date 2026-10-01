# EdChoice Universal School Choice source mapping

## Boundary

This adapter belongs to `frontier-policy-ingestion`, not chatbot/document RAG. It creates structured review artifacts for the existing `public.policies` model and never writes to Supabase itself.

Research source: **EdChoice School Choice in America Dashboard**  
Canonical URL: `https://www.edchoice.org/school-choice/dashboard/`

## Extraction strategy

1. Fetch the first-party dashboard HTML.
2. Parse the first-party structured HTML table when it contains the expected columns: State, Program Type, Program Name, Enacted, Launched, and Universal.
3. Detail-fetch rows where `Universal` is `Full` or `Eligibility`; `N/A` and other values are excluded.
4. If the dashboard table is absent because EdChoice populated it client-side, fetch the first-party `https://www.edchoice.org/universal-school-choice` page instead of scraping visual dashboard cards.
5. On that fallback page, ingest every program listed in **Universal Eligibility**. If the same program also appears in **Universal Options** and **Universal Funding** for a state listed as **Truly Universal**, retain the source classification as `Full`; otherwise retain it as `Eligibility`.
6. If both first-party HTML paths fail, use a dashboard CSV export with `--input-csv` and provide the dashboard citation date with `--source-date`.
7. Detail pages are used for source-supported title/universality metadata and to locate official government/statute links. A reviewed government-source override file may resolve a non-government aggregator statute link, but cannot make a state qualify.

## Qualification rule

The normalized category is exactly:

`Universal School Choice`

A row qualifies when EdChoice itself supplies one of the following equivalent source signals:

1. the dashboard `Universal` column is `Full` or `Eligibility` (case-insensitive after whitespace normalization), or
2. the dashboard table is absent from server HTML and the first-party Universal School Choice page lists the program under **Universal Eligibility**.

Dashboard `N/A` and all other non-qualifying values are retained as `EXCLUDED`. The fallback never promotes a state based on general knowledge.

## Field mapping

| Existing policy field | EdChoice mapping |
| --- | --- |
| `state_code` | Existing `state_code_for(state_name)` normalization |
| `state_name` | Dashboard State |
| `policy_identifier` | EdChoice program-detail H1 when available, otherwise dashboard/program listing name; this is a source identity/program name, not a fabricated bill number |
| `title` | EdChoice program detail H1 when present, otherwise dashboard Program Name |
| `summary` | Deterministic source-grounded statement of program type + EdChoice `Full`/`Eligibility` classification evidence + detail universality flags when present + dashboard eligibility rate when present |
| `policy_type` | `Other` (existing normalized enum; no new policy-type schema value introduced) |
| `category` | `Universal School Choice` |
| `categories` | `['Universal School Choice']` |
| `status` | `Enacted` only when EdChoice exposes an enacted year; otherwise `Unknown` |
| `status_detail` | Source facts: dashboard/program-page enacted/launched years plus dashboard `Full`/`Eligibility` or universality-page eligibility evidence, and detail universality flags |
| `effective_date` | Always null unless a future source explicitly provides an effective date; enacted/launched years are not converted |
| `implementation_timeline` | EdChoice-reported launch year, when present |
| `source_url` | Verified official government/statute/program URL only |
| `research_source_name` | `EdChoice School Choice Dashboard` |
| `research_source_url` | Dashboard canonical URL |
| `research_source_date` | Dashboard suggested-citation last-modified date, when available |
| `status_as_of_date` | Dashboard suggested-citation last-modified date |
| `last_updated` | Dashboard suggested-citation last-modified date |
| `last_verified_at` | Null unless a verification workflow explicitly supplies it |

The dashboard warns that its year may be school-year ending or calendar year. If dashboard and program-detail launch/enacted years differ, both are retained in `status_detail`; the mismatch is a warning and never becomes an inferred effective date.

## Official-source verification

`source_url` must be a government URL recognized by the adapter (`.gov` or legacy state-legislature `.us`). EdChoice URLs remain research provenance and are never inserted as the official source. If no verified official URL is available, the record remains review-only (`POSSIBLE_MATCH_REVIEW`) and no SQL row is generated.

`data/edchoice_official_source_overrides.json` is intentionally narrow. It may contain a manually reviewed official government URL when EdChoice links to a non-government statute mirror. Overrides do not affect Universal classification.

As of the 2026-10-01 review, explicit official-source mappings also cover the five live Universal Eligibility rows that EdChoice did not expose with a directly usable government URL: Alaska Correspondence School Allotment Program, Idaho Parental Choice Tax Credit, North Carolina Opportunity Scholarships, Tennessee Education Freedom Scholarship Act, and Texas Education Savings Account Program. These mappings use only official state/legislative domains and leave EdChoice as research provenance.

## Deduplication

Comparison is against the supplied/current structured-policy CSV, not only EdChoice rows.

- same state + normalized program identity -> `DUPLICATE_EXISTING`
- same state + official URL but different program identity -> `POSSIBLE_MATCH_REVIEW`
- repeated incoming state + program identity -> `INVALID`
- same state alone -> **not a duplicate**
- clean qualifying row -> `NEW`
- dashboard row outside `Full` / `Eligibility` -> `EXCLUDED`

Exact duplicates are compared field-by-field and differences are emitted to the conflict artifact. Existing values are preserved until human approval. Existing EdChoice records that disappear or cease to qualify as `Full` / `Eligibility` are emitted to the changes artifact and are never automatically deleted.

## Review-first load safety

`python -m app.cli ingest-edchoice-universal-school-choice` only generates review artifacts and optional SQL. It does not invoke `upsert_policies` and does not create embeddings. Production data requires a separate, explicit reviewed load workflow.
