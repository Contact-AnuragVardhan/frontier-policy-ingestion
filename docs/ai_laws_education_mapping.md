# AI Laws by State — Education AI Tracker mapping

Source: `https://www.ailawsbystate.com/tools/education-ai-tracker`

This source is an independent research/aggregator source. It feeds the same normalized
`public.policies` dataset as MultiState but is handled by its own adapter. The existing
MultiState adapter is not modified or mixed with this parser.

## Source → AI Choice model

| Education AI Tracker field | AI Choice field | Rule |
| --- | --- | --- |
| State | `state_name`, `state_code` | Normalize with the existing jurisdiction map. DC is supported as `DC`. Federal rows are retained in review output but are not loaded into the state map. |
| Bill / Statute | `policy_identifier` | Normalize display spacing without changing the legal identifier. A separate comparison key removes formatting differences such as `SB2909`, `SB 2909`, and `SB-2909`. |
| Year | review/dedup metadata | Preserved for review and used to distinguish reused identifiers. It is not written into a different production field. |
| Category | `category`, `categories[]` | Deterministically mapped into the existing AI Choice categories. The original tracker category remains in review/raw output. |
| Status | `status_detail`, `status` | Exact source value goes to `status_detail`; a stable existing UI status is assigned only when semantically safe. |
| Key Requirement | `summary` | Used as the source-supported concise summary. No legal conclusion is generated. |
| Detail-page title | `title` | Use the source-supported detail-page heading when available; otherwise use a safe `State + identifier` title. |
| Detail-page official source | `source_url` | Must be the original government/legislative URL. The aggregator URL is never substituted here. |
| Detail-page URL | `research_source_url` | Prefer the specific AI Laws by State record URL; fall back to the tracker URL. |
| Data Updated | `research_source_date`, `last_updated`, candidate `status_as_of_date` | Source-supported date only. |
| Last Verified | `last_verified_at`, candidate `status_as_of_date` | Source-supported date only. `status_as_of_date` uses the later of Data Updated and Last Verified so a recent status update is not dated earlier than the update that produced it. |
| Effective date | `effective_date` | Populated only when the public detail page explicitly exposes an Effective date. The tracker year, last action, verification date, and update date are never repurposed as an effective date. |
| Implementation timeline | `implementation_timeline` | Left null unless explicitly source-supported. |

## Category normalization

- `AI Literacy & Curriculum` → primary `AI Literacy`.
- `Generative AI in Classrooms` → primary `AI Use`.
- `Student Data Privacy & AI` → primary `Student Privacy`; add `AI Use` only when the Key Requirement itself clearly refers to regulating AI use/processing.
- `Teacher Use of AI / PD` → `AI Use` for explicit teacher-AI-use requirements, `AI Literacy` for explicit training/professional-development requirements, or both only when the requirement clearly supports both. For the tracker's generic either/or wording, the source-supported detail title is used only as a narrow second signal: explicit AI + professional-development/training wording can support `AI Literacy`, and explicit AI + teacher-use/evaluation wording can support `AI Use`. A title pointing to another topic is treated as a possible source mismatch rather than silently recategorized. If the title still does not support a conservative mapping, the record remains review-only.
- `Parental Consent` and `School Procurement` are added only when the Key Requirement explicitly supports those concepts.

## Status normalization

- `Enacted` → `Enacted`.
- `Introduced`, `In Committee`, `Passed One Chamber`, `Passed Both Chambers` → `Pending`.
- `Dead`, `Dead/Failed`, `Failed`, `Vetoed` → `Inactive`.
- `Unknown` → `Unknown`.
- Unrecognized values are invalid/review-only; they are never silently treated as pending.

## Policy type

Tracker records are treated as `Bill` unless the source explicitly identifies a record as a
statute/law. Enacted status alone is not used to relabel an identifier as a law.

## Provenance and duplicates

Deduplication uses `state_code + normalized policy identifier` as the primary identity and
uses explicit year/session evidence from the existing official source URL only when necessary.
The MultiState research publication/snapshot date is never treated as a bill year. If the incoming
and existing official URLs are the same record, a tracker-year difference does not create a false
new policy. Known odd/even two-year session handling is applied narrowly for NY/WA/CA so a
second-year tracker label is not mistaken for a reused bill number. Existing MultiState records are
never blindly overwritten. A same-policy match is emitted to the duplicate/merge review output,
field disagreements are emitted to the conflict report, and the output carries a review recommendation
instead of applying the incoming value automatically.

Because the production schema currently stores only one `research_source_name/url` pair,
a duplicate MultiState row is not rewritten with AI Laws by State provenance. That avoids
destroying the previously approved source history.

## Load safety

A record is not eligible for generated SQL when required production facts are unsupported,
including a missing official government source URL or missing source-supported status date.
No production Supabase write is performed by `ingest-ai-laws-education`.

Structurally valid rows can also be held as `POSSIBLE_MATCH_REVIEW` when source-internal consistency
is questionable. The guard checks explicit official-URL year evidence and whether the detail title has
at least some visible AI, education/student, or privacy relevance for the tracker row. This is deliberately
a review gate, not a legal semantic classifier.

## Jurisdiction and compatibility notes

- District of Columbia is normalized to `DC`; the included database schema accepts two-letter jurisdiction codes.
- Federal rows currently exposed by the Education AI Tracker are retained in review output but classified invalid for the state/DC map model, so they cannot be silently loaded as state policies.
- The supplied archive contains the ingestion/database code but not the deployed Node policy API or React EdTech Map source. No frontend/backend code is changed by this task; API/UI compatibility must be verified in those repositories before production import, especially for `Inactive`/`Unknown` statuses and DC discoverability.

## Live-source integrity checks

When the tracker page exposes counts such as `Showing N of N bills`, the ingestion compares that source-declared total with the number of rows actually parsed. A mismatch is a blocking validation error so a partial scrape cannot silently generate SQL.

## Post-review disposition layer

The generic source adapter remains conservative. After manual review, a separate
`app/ai_laws_review_resolutions.py` layer records narrow, auditable dispositions without
changing the general normalization heuristics.

The original ingestion classification (`NEW`, `DUPLICATE_EXISTING`,
`POSSIBLE_MATCH_REVIEW`, `INVALID`) is preserved. Review output also contains:

- `review_disposition`
- `review_resolution_note`

Current reviewed includes:

- IL SB1677 → `AI Use`
- IN HB1296 → `AI Use`
- LA HR320 → `AI Literacy`
- MS SB2429 → `AI Use`, `AI Literacy`
- MS SB2062 → `AI Use`, `AI Literacy`
- NY A06972 → `AI Use`, `AI Literacy`, `Student Privacy`, `School Procurement`
- NY A06720 → `Student Privacy`
- NY S03827 → `Student Privacy`

Current reviewed exclusions:

- CT SB325
- HI HR202
- IL HB5321
- MD HB807
- NY A07838
- NY A10217
- OK HB3544

Federal tracker records are marked `EXCLUDE_OUT_OF_SCOPE`. Rows that fail the
tracker/detail title-alignment guard are marked `EXCLUDE_SOURCE_MISMATCH`. These rows remain
in the review/validation artifacts, but they are never emitted to SQL.

Once every non-duplicate review row has either a verified include or an explicit exclusion,
SQL may be generated for the independently valid `NEW` subset. This does not load Supabase;
the SQL artifact remains manual-review output only. Any genuinely unresolved `INVALID` or
`POSSIBLE_MATCH_REVIEW` row still blocks SQL generation.

## Bill identity consistency

Before SQL generation, the adapter compares three pieces of evidence: the tracker row, the title encoded in the AI Laws detail URL, and the official-government title obtained through that detail record. A high-confidence subject mismatch becomes a blocking `Bill identity mismatch` review flag. A strong non-education official title paired with a generic Education AI tracker requirement becomes an `Education-scope mismatch` review flag. These flags are intentionally conservative and require review; they never cause a silent merge or overwrite.

## Reused bill numbers / session identity

Bill numbers can be reused in later sessions. In addition to comparing the AI Laws
detail slug/title and official-source metadata, the adapter compares the tracker row
`Year` with the detail page's source-supported `Last Action` date. A difference greater
than one year is a blocking bill-identity review signal. `Last Verified` and `Data
Updated` are deliberately not used for this check because historical records can be
re-verified or refreshed in the current year.

The 2026-09-28 review also records explicit exclusions for TN HB1455, TX HB2400, and
WA SB6254 after their tracker/detail/official-source records were found to resolve to
conflicting legislative sessions or subjects.

## Year/session handling

The tracker `Year` value is preserved as source metadata but is **not treated as a legislative-session year**. The live tracker can use values that align with effective years or otherwise differ from the bill session (for example, CA AB 1651 is shown as 2028 while its legislative action occurred in 2026). Session identity is therefore determined from the bill/detail identity and official source, not from tracker year vs. Last Action year arithmetic.

## K-12 scope guard

AI Choice's EdTech Map is K-12 scoped. Records that are clearly postsecondary-only (for example, community-college-only or higher-education-only measures) or professional-licensing-only are excluded after review. Mixed bills that explicitly include K-12 school districts remain eligible.
