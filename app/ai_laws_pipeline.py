from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app import config
from app.ai_laws_review_resolutions import apply_review_resolutions
from app.ai_laws_exporters import (
    summarize,
    write_conflicts_csv,
    write_duplicates_csv,
    write_normalized_csv,
    write_raw_json,
    write_report,
    write_review_csv,
    write_source_metadata,
    write_sql,
    write_validation_json,
)
from app.dedupe import classify_against_existing
from app.models import PolicyRecord
from app.sources.ai_laws_education import (
    SOURCE_NAME,
    SOURCE_URL,
    Candidate,
    DetailInfo,
    TrackerRow,
    candidate_from_source,
    fetch_details_for_rows,
    fetch_html,
    parse_tracker_html,
    parse_tracker_page_metadata,
)
from app.sources.multistate import load_fixture as load_multistate_fixture
from app.supabase_loader import read_db_csv
from app.validate import validate_records

OUTPUT_NAMES = [
    "ai_laws_education_raw.html",
    "ai_laws_education_raw.json",
    "ai_laws_education_normalized.csv",
    "ai_laws_education_review.csv",
    "ai_laws_education_validation.json",
    "ai_laws_education_duplicates.csv",
    "ai_laws_education_conflicts.csv",
    "ai_laws_education_source_metadata.json",
    "ai_laws_education_ingestion_report.md",
    "ai_laws_education.sql",
]


def _records_from_db_csv(path: Path) -> list[PolicyRecord]:
    rows = read_db_csv(path)
    return [PolicyRecord(**row) for row in rows]


def _load_existing_records(existing_csv: Path | None):
    if existing_csv:
        if not existing_csv.exists():
            raise FileNotFoundError(f"Existing-policy CSV not found: {existing_csv}")
        return _records_from_db_csv(existing_csv), str(existing_csv), "explicit_csv"

    default_csv = config.OUTPUT_DIR / "policies.csv"
    if default_csv.exists():
        return _records_from_db_csv(default_csv), str(default_csv), "default_output_csv"

    # The bundled MultiState fixture is the approved source snapshot shipped with this
    # archive. Using it is explicit in the report so it is never mistaken for a live DB read.
    return (
        load_multistate_fixture(config.FIXTURE_PATH),
        str(config.FIXTURE_PATH),
        "bundled_multistate_fixture",
    )


def _fixture_candidates(fixture_path: Path) -> tuple[list[TrackerRow], list[Candidate], list[dict], str]:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    rows: list[TrackerRow] = []
    candidates: list[Candidate] = []
    fetch_log: list[dict] = []
    for index, item in enumerate(payload["records"], start=1):
        row = TrackerRow(**item["row"])
        detail = DetailInfo(**item.get("detail", {}))
        candidate = candidate_from_source(row, detail)
        rows.append(row)
        candidates.append(candidate)
        fetch_log.append(
            {
                "row_number": index,
                "state": row.state_name,
                "identifier": row.identifier_raw,
                "detail_url": row.research_record_url,
                "detail_fetch_error": None,
                "official_source_url": detail.official_source_url,
                "last_verified_date": detail.last_verified_date,
                "data_updated_date": detail.data_updated_date,
                "effective_date": detail.effective_date,
                "detail_title": detail.title,
                "detail_status": detail.source_status,
                "last_action_date": detail.last_action_date,
            }
        )
    return rows, candidates, fetch_log, payload.get("tracker_html", "")


def _source_consistency_warnings(rows: list[TrackerRow], candidates: list[Candidate]) -> list[str]:
    warnings: list[str] = []
    mismatch_count = 0
    for row, candidate in zip(rows, candidates):
        detail_status = candidate.detail.source_status
        if detail_status and detail_status.strip().lower() != row.source_status.strip().lower():
            mismatch_count += 1
    if mismatch_count:
        warnings.append(
            f"{mismatch_count} tracker row(s) had a different status on the detail page; "
            "the detail-page status was retained in status_detail and should be reviewed."
        )
    return warnings


def run_ai_laws_ingestion(
    *,
    source_url: str = SOURCE_URL,
    existing_csv: Path | None = None,
    fixture_path: Path | None = None,
    timeout: int = 30,
) -> tuple[int, dict]:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_started_at = datetime.now(timezone.utc).isoformat()

    if fixture_path:
        if not fixture_path.exists():
            raise FileNotFoundError(f"AI Laws fixture not found: {fixture_path}")
        rows, candidates, fetch_log, tracker_html = _fixture_candidates(fixture_path)
        mode = "fixture"
    else:
        tracker_html = fetch_html(source_url, timeout=timeout)
        rows = parse_tracker_html(tracker_html, source_url)
        candidates, fetch_log = fetch_details_for_rows(rows, timeout=timeout, continue_on_error=True)
        mode = "live"

    page_metadata = parse_tracker_page_metadata(tracker_html) if tracker_html else None

    # Apply only documented post-review decisions. The generic parser stays conservative.
    candidates = apply_review_resolutions(candidates)

    existing_records, existing_source, existing_source_kind = _load_existing_records(existing_csv)
    candidates, duplicates, conflicts = classify_against_existing(candidates, existing_records)

    # Validate only NEW records as production candidates. Duplicates are review-only and must not
    # replace existing MultiState provenance. Possible matches and invalid rows are also review-only.
    new_records = [c.to_policy_record() for c in candidates if c.is_valid_for_db()]
    db_errors, db_summary = validate_records(new_records)

    validation_errors = list(db_errors)
    if page_metadata:
        expected_total = page_metadata.total_count or page_metadata.declared_bill_count
        if expected_total is not None and expected_total != len(rows):
            validation_errors.append(
                f"Tracker declared {expected_total} bills but parser extracted {len(rows)} rows; "
                "source structure/filter state must be reviewed."
            )

    for candidate in candidates:
        if candidate.is_reviewed_exclusion():
            # A reviewed exclusion remains visible in review artifacts but is intentionally
            # out of the SQL candidate set, so it is not an unresolved validation blocker.
            continue
        if candidate.classification == "INVALID":
            details = candidate.issues + candidate.review_flags
            validation_errors.append(
                f"{candidate.raw.state_name} {candidate.raw.identifier_raw}: "
                + "; ".join(details or ["invalid record"])
            )
        elif candidate.classification == "POSSIBLE_MATCH_REVIEW":
            details = candidate.review_flags + candidate.issues
            validation_errors.append(
                f"{candidate.raw.state_name} {candidate.raw.identifier_raw}: "
                + "; ".join(details or ["manual duplicate/source review required"])
            )

    warnings = [
        "Existing MultiState duplicates are intentionally excluded from SQL to preserve existing provenance.",
        "Reviewed exclusions and source-mismatch exclusions remain in review artifacts but are intentionally omitted from SQL.",
        "Federal rows from the tracker are review-only because the current structured policy model is state/DC based.",
        "Frontend/backend source code was not present in this archive; UI/API compatibility must be verified before loading production data.",
    ]
    warnings.extend(_source_consistency_warnings(rows, candidates))

    summary = summarize(candidates, conflicts)
    summary["new_db_validation"] = db_summary
    summary["existing_comparison_source"] = existing_source
    summary["existing_comparison_source_kind"] = existing_source_kind
    summary["mode"] = mode
    summary["source_declared_counts"] = page_metadata.as_dict() if page_metadata else {}

    raw_html_path = config.OUTPUT_DIR / "ai_laws_education_raw.html"
    raw_html_path.write_text(
        tracker_html or "<!-- fixture mode: no raw tracker HTML bundled -->\n",
        encoding="utf-8",
    )
    write_raw_json(config.OUTPUT_DIR / "ai_laws_education_raw.json", rows, fetch_log)
    write_normalized_csv(config.OUTPUT_DIR / "ai_laws_education_normalized.csv", candidates)
    write_review_csv(config.OUTPUT_DIR / "ai_laws_education_review.csv", candidates)
    write_duplicates_csv(config.OUTPUT_DIR / "ai_laws_education_duplicates.csv", duplicates)
    write_conflicts_csv(config.OUTPUT_DIR / "ai_laws_education_conflicts.csv", conflicts)

    metadata = {
        "research_source_name": SOURCE_NAME,
        "url": source_url,
        "mode": mode,
        "run_started_at_utc": run_started_at,
        "source_row_count": len(rows),
        "state_or_dc_count": len({c.state_code for c in candidates if c.state_code}),
        "source_declared_counts": page_metadata.as_dict() if page_metadata else {},
        "existing_comparison_source": existing_source,
        "existing_comparison_source_kind": existing_source_kind,
        "detail_pages_attempted": sum(1 for row in rows if row.research_record_url),
        "detail_fetch_failures": sum(1 for item in fetch_log if item.get("detail_fetch_error")),
        "production_supabase_modified": False,
        "note": "Counts are derived from the source at run time; no fixed bill/state count is hardcoded.",
    }
    write_source_metadata(config.OUTPUT_DIR / "ai_laws_education_source_metadata.json", metadata)
    write_validation_json(
        config.OUTPUT_DIR / "ai_laws_education_validation.json",
        summary=summary,
        errors=validation_errors,
        warnings=warnings,
    )

    # Stop SQL generation on blocking validation errors. Remove any stale SQL from an older run.
    sql_path = config.OUTPUT_DIR / "ai_laws_education.sql"
    if validation_errors:
        if sql_path.exists():
            sql_path.unlink()
        output_names = [name for name in OUTPUT_NAMES if name != "ai_laws_education.sql"]
    else:
        write_sql(sql_path, new_records)
        output_names = OUTPUT_NAMES

    write_report(
        config.OUTPUT_DIR / "ai_laws_education_ingestion_report.md",
        source_url=source_url,
        summary=summary,
        validation_errors=validation_errors,
        warnings=warnings,
        output_names=output_names,
    )

    print(json.dumps(summary, indent=2))
    print(f"Outputs written to: {config.OUTPUT_DIR}")
    if validation_errors:
        print("Blocking review items were found. SQL was NOT generated; production Supabase was NOT modified.")
        return 1, summary
    print("Review artifacts and SQL were generated. Production Supabase was NOT modified.")
    return 0, summary
