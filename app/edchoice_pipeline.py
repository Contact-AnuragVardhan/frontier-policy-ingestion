from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app import config
from app.edchoice_exporters import (
    summarize,
    write_changes_csv,
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
from app.models import PolicyRecord
from app.sources.edchoice_school_choice import (
    CATEGORY,
    SOURCE_NAME,
    SOURCE_URL,
    UNIVERSAL_SOURCE_URL,
    Candidate,
    DashboardRow,
    DetailInfo,
    candidate_from_source,
    fetch_details_for_rows,
    fetch_html,
    load_official_source_overrides,
    parse_dashboard_csv,
    parse_dashboard_html,
    parse_dashboard_source_date,
    parse_universal_school_choice_html,
)
from app.sources.multistate import load_fixture as load_multistate_fixture
from app.supabase_loader import read_db_csv
from app.validate import validate_records

OUTPUT_NAMES = [
    "edchoice_universal_school_choice_raw.html",
    "edchoice_universal_school_choice_universal_page_raw.html",
    "edchoice_universal_school_choice_raw.json",
    "edchoice_universal_school_choice_normalized.csv",
    "edchoice_universal_school_choice_review.csv",
    "edchoice_universal_school_choice_validation.json",
    "edchoice_universal_school_choice_duplicates.csv",
    "edchoice_universal_school_choice_conflicts.csv",
    "edchoice_universal_school_choice_changes.csv",
    "edchoice_universal_school_choice_source_metadata.json",
    "edchoice_universal_school_choice_ingestion_report.md",
    "edchoice_universal_school_choice.sql",
]


def _records_from_db_csv(path: Path) -> list[PolicyRecord]:
    return [PolicyRecord(**row) for row in read_db_csv(path)]


def _load_existing_records(existing_csv: Path | None):
    if existing_csv:
        if not existing_csv.exists():
            raise FileNotFoundError(f"Existing-policy CSV not found: {existing_csv}")
        return _records_from_db_csv(existing_csv), str(existing_csv), "explicit_csv"

    default_csv = config.OUTPUT_DIR / "policies.csv"
    if default_csv.exists():
        return _records_from_db_csv(default_csv), str(default_csv), "default_output_csv"

    return (
        load_multistate_fixture(config.FIXTURE_PATH),
        str(config.FIXTURE_PATH),
        "bundled_multistate_fixture",
    )


def _text_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _url_key(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))


def _classify_against_existing(candidates: list[Candidate], existing: list[PolicyRecord]):
    by_identity: dict[tuple[str, str], list[PolicyRecord]] = {}
    by_source: dict[tuple[str, str], list[PolicyRecord]] = {}
    for record in existing:
        by_identity.setdefault((record.state_code, _text_key(record.policy_identifier)), []).append(record)
        if record.source_url:
            by_source.setdefault((record.state_code, _url_key(record.source_url)), []).append(record)

    duplicates: list[dict] = []
    conflicts: list[dict] = []
    seen_incoming: set[tuple[str, str]] = set()

    for candidate in candidates:
        if candidate.classification == "EXCLUDED":
            continue
        if candidate.issues:
            candidate.classification = "INVALID"
            candidate.validation_result = "FAIL"
            continue
        if not candidate.state_code or not candidate.policy_identifier:
            candidate.classification = "INVALID"
            candidate.validation_result = "FAIL"
            candidate.issues.append("Missing normalized state/program identity")
            continue

        incoming_key = (candidate.state_code, _text_key(candidate.policy_identifier))
        if incoming_key in seen_incoming:
            candidate.classification = "INVALID"
            candidate.validation_result = "FAIL"
            candidate.issues.append("Duplicate EdChoice source row for the same state + program name")
            continue
        seen_incoming.add(incoming_key)

        matches = by_identity.get(incoming_key, [])
        if len(matches) > 1:
            candidate.classification = "POSSIBLE_MATCH_REVIEW"
            candidate.validation_result = "REVIEW"
            candidate.review_flags.append(
                f"Multiple existing policies share the same state + program identifier ({len(matches)} matches)."
            )
            continue
        if len(matches) == 1:
            existing_record = matches[0]
            candidate.classification = "DUPLICATE_EXISTING"
            candidate.validation_result = "REVIEW"
            candidate.duplicate_existing_title = existing_record.title
            candidate.duplicate_existing_source_url = existing_record.source_url
            duplicates.append(
                {
                    "classification": "DUPLICATE_EXISTING",
                    "state_code": candidate.state_code,
                    "program_name": candidate.policy_identifier,
                    "existing_title": existing_record.title,
                    "existing_source_url": existing_record.source_url,
                    "existing_research_source_name": existing_record.research_source_name,
                    "existing_research_source_url": existing_record.research_source_url,
                    "reason": "Same state + normalized source-supported program identifier.",
                }
            )
            for field in ("title", "summary", "status", "status_detail", "source_url"):
                incoming = getattr(candidate, field)
                current = getattr(existing_record, field)
                same = _url_key(incoming) == _url_key(current) if field == "source_url" else incoming == current
                if not same:
                    conflicts.append(
                        {
                            "state_code": candidate.state_code,
                            "program_name": candidate.policy_identifier,
                            "field": field,
                            "existing_value": current,
                            "edchoice_value": incoming,
                            "resolution": "REVIEW_PRESERVE_EXISTING_UNTIL_APPROVED",
                        }
                    )
            continue

        if candidate.source_url:
            source_matches = by_source.get((candidate.state_code, _url_key(candidate.source_url)), [])
            if source_matches:
                candidate.classification = "POSSIBLE_MATCH_REVIEW"
                candidate.validation_result = "REVIEW"
                candidate.review_flags.append(
                    "Official source URL matches an existing policy with a different identifier; review before insert."
                )
                continue

        candidate.classification = "POSSIBLE_MATCH_REVIEW" if candidate.has_blocking_review_flags() else "NEW"
        candidate.validation_result = "REVIEW" if candidate.has_blocking_review_flags() else "PASS"

    return candidates, duplicates, conflicts


def _source_changes(candidates: list[Candidate], existing: list[PolicyRecord]) -> list[dict]:
    current_all = {
        (c.state_code, _text_key(c.policy_identifier or c.raw.program_name)): c
        for c in candidates
        if c.state_code and (c.policy_identifier or c.raw.program_name)
    }
    changes: list[dict] = []
    for record in existing:
        if record.research_source_name != SOURCE_NAME or CATEGORY not in record.categories:
            continue
        key = (record.state_code, _text_key(record.policy_identifier))
        current = current_all.get(key)
        if current is None:
            changes.append(
                {
                    "change_type": "REMOVED_FROM_CURRENT_SOURCE_REVIEW",
                    "state_code": record.state_code,
                    "program_name": record.policy_identifier,
                    "existing_title": record.title,
                    "existing_source_url": record.source_url,
                    "reason": "Existing EdChoice Universal School Choice record is absent from the current source rows; do not delete automatically.",
                }
            )
        elif current.classification == "EXCLUDED":
            changes.append(
                {
                    "change_type": "NO_LONGER_UNIVERSAL_ELIGIBILITY_REVIEW",
                    "state_code": record.state_code,
                    "program_name": record.policy_identifier,
                    "existing_title": record.title,
                    "existing_source_url": record.source_url,
                    "reason": f"Current dashboard Universal value is {current.raw.universal!r}; do not delete automatically.",
                }
            )
    return changes


def _fixture_source(fixture_path: Path):
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    rows: list[DashboardRow] = []
    details: dict[tuple[str, str], DetailInfo] = {}
    detail_log: list[dict] = []
    for item in payload["records"]:
        row = DashboardRow(**item["row"])
        detail = DetailInfo(**item.get("detail", {}))
        rows.append(row)
        details[(row.state_name, row.program_name)] = detail
        detail_log.append({"state": row.state_name, "program_name": row.program_name, "detail_url": row.program_url, "error": None, **detail.as_dict()})
    return rows, details, detail_log, payload.get("source_date"), payload.get("dashboard_html", "")


def run_edchoice_ingestion(
    *,
    source_url: str = SOURCE_URL,
    existing_csv: Path | None = None,
    fixture_path: Path | None = None,
    input_csv: Path | None = None,
    overrides_path: Path | None = None,
    source_date_override: str | None = None,
    timeout: int = 30,
) -> tuple[int, dict]:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_started_at = datetime.now(timezone.utc).isoformat()
    universal_html = ""
    extraction_method = ""
    dashboard_parse_error = None

    if fixture_path:
        if not fixture_path.exists():
            raise FileNotFoundError(f"EdChoice fixture not found: {fixture_path}")
        rows, details, detail_log, source_date, dashboard_html = _fixture_source(fixture_path)
        source_date = source_date_override or source_date
        mode = "fixture"
        extraction_method = "fixture"
    elif input_csv:
        if not input_csv.exists():
            raise FileNotFoundError(f"EdChoice CSV not found: {input_csv}")
        rows = parse_dashboard_csv(input_csv.read_text(encoding="utf-8-sig"))
        details, detail_log = fetch_details_for_rows(rows, timeout=timeout, continue_on_error=True)
        source_date = source_date_override
        dashboard_html = "<!-- manual CSV input; dashboard HTML not fetched -->\n"
        mode = "manual_csv"
        extraction_method = "manual_dashboard_csv"
    else:
        dashboard_html = fetch_html(source_url, timeout=timeout)
        source_date = source_date_override or parse_dashboard_source_date(dashboard_html)
        try:
            rows = parse_dashboard_html(dashboard_html, source_url)
            extraction_method = "dashboard_server_html_table"
        except ValueError as exc:
            dashboard_parse_error = str(exc)
            universal_html = fetch_html(UNIVERSAL_SOURCE_URL, timeout=timeout)
            rows = parse_universal_school_choice_html(universal_html, UNIVERSAL_SOURCE_URL)
            extraction_method = "universal_school_choice_page_fallback"
        details, detail_log = fetch_details_for_rows(rows, timeout=timeout, continue_on_error=True)
        mode = "live"

    overrides = load_official_source_overrides(
        overrides_path if overrides_path is not None else config.EDCHOICE_OFFICIAL_SOURCE_OVERRIDES_PATH
    )
    candidates = [
        candidate_from_source(
            row,
            details.get((row.state_name, row.program_name), DetailInfo()),
            source_date=source_date,
            source_url=source_url,
            official_source_overrides=overrides,
        )
        for row in rows
    ]

    existing_records, existing_source, existing_source_kind = _load_existing_records(existing_csv)
    candidates, duplicates, conflicts = _classify_against_existing(candidates, existing_records)
    changes = _source_changes(candidates, existing_records)

    new_records = [c.to_policy_record() for c in candidates if c.is_valid_for_db()]
    db_errors, db_summary = validate_records(new_records)
    validation_errors = list(db_errors)
    for candidate in candidates:
        if candidate.classification == "INVALID":
            validation_errors.append(
                f"{candidate.raw.state_name} / {candidate.raw.program_name}: "
                + "; ".join(candidate.issues or candidate.review_flags or ["invalid record"])
            )
        elif candidate.classification == "POSSIBLE_MATCH_REVIEW":
            validation_errors.append(
                f"{candidate.raw.state_name} / {candidate.raw.program_name}: "
                + "; ".join(candidate.review_flags or candidate.issues or ["manual review required"])
            )

    warnings = [
        "Universal School Choice now includes EdChoice dashboard Universal=Full and Universal=Eligibility; Universal=N/A and other values remain excluded.",
        "Enacted/launched years are not converted into effective_date.",
        "Existing records are never silently overwritten or deleted when EdChoice changes classification.",
        "Official-source overrides are review metadata only; they do not determine which states qualify as Universal School Choice.",
        "This pipeline never writes to production Supabase and never creates chatbot/RAG embeddings.",
    ]
    if mode == "manual_csv" and not source_date:
        warnings.append(
            "Manual CSV mode cannot derive the dashboard last-modified date from the export alone; rerun with --source-date YYYY-MM-DD before a record can be SQL-ready."
        )
    if extraction_method == "universal_school_choice_page_fallback":
        warnings.append(
            "The dashboard table was not present in server HTML. Classification was derived from EdChoice's first-party Universal School Choice page: every program under Universal Eligibility qualifies; programs also appearing under Universal Options and Universal Funding for a Truly Universal state are classified as Full."
        )

    summary = summarize(candidates, conflicts, changes)
    summary["new_db_validation"] = db_summary
    summary["existing_comparison_source"] = existing_source
    summary["existing_comparison_source_kind"] = existing_source_kind
    summary["mode"] = mode
    summary["source_date"] = source_date
    summary["extraction_method"] = extraction_method

    raw_html_path = config.OUTPUT_DIR / "edchoice_universal_school_choice_raw.html"
    raw_html_path.write_text(dashboard_html or "<!-- fixture mode: no raw dashboard HTML bundled -->\n", encoding="utf-8")
    universal_raw_path = config.OUTPUT_DIR / "edchoice_universal_school_choice_universal_page_raw.html"
    universal_raw_path.write_text(
        universal_html or "<!-- Universal School Choice fallback page was not used for this run -->\n",
        encoding="utf-8",
    )
    write_raw_json(config.OUTPUT_DIR / "edchoice_universal_school_choice_raw.json", rows, detail_log, source_date)
    write_normalized_csv(config.OUTPUT_DIR / "edchoice_universal_school_choice_normalized.csv", candidates)
    write_review_csv(config.OUTPUT_DIR / "edchoice_universal_school_choice_review.csv", candidates)
    write_duplicates_csv(config.OUTPUT_DIR / "edchoice_universal_school_choice_duplicates.csv", duplicates)
    write_conflicts_csv(config.OUTPUT_DIR / "edchoice_universal_school_choice_conflicts.csv", conflicts)
    write_changes_csv(config.OUTPUT_DIR / "edchoice_universal_school_choice_changes.csv", changes)

    metadata = {
        "research_source_name": SOURCE_NAME,
        "url": source_url,
        "source_date": source_date,
        "mode": mode,
        "extraction_method": extraction_method,
        "dashboard_parse_error": dashboard_parse_error,
        "universal_fallback_url": UNIVERSAL_SOURCE_URL if universal_html else None,
        "run_started_at_utc": run_started_at,
        "source_row_count": len(rows),
        "qualifying_universal_school_choice_count": summary["qualifying_universal_school_choice_entries"],
        "qualifying_full_count": summary.get("qualifying_full_entries", 0),
        "qualifying_eligibility_count": summary.get("qualifying_eligibility_entries", 0),
        "qualifying_states": summary["qualifying_states"],
        "existing_comparison_source": existing_source,
        "existing_comparison_source_kind": existing_source_kind,
        "official_source_overrides_file": str(
            overrides_path if overrides_path is not None else config.EDCHOICE_OFFICIAL_SOURCE_OVERRIDES_PATH
        ),
        "production_supabase_modified": False,
        "chatbot_rag_modified": False,
        "qualification_rule": (
            "Include dashboard Universal=Full and Universal=Eligibility; exclude Universal=N/A and other values. "
            "If the dashboard table is client-rendered and absent from server HTML, use every program in EdChoice's "
            "Universal Eligibility section; mark a program Full only when it also appears under Universal Options and "
            "Universal Funding for a state EdChoice lists as Truly Universal."
        ),
    }
    write_source_metadata(config.OUTPUT_DIR / "edchoice_universal_school_choice_source_metadata.json", metadata)
    write_validation_json(
        config.OUTPUT_DIR / "edchoice_universal_school_choice_validation.json",
        summary=summary,
        errors=validation_errors,
        warnings=warnings,
    )

    sql_path = config.OUTPUT_DIR / "edchoice_universal_school_choice.sql"
    if validation_errors:
        sql_path.write_text(
            "-- SQL NOT GENERATED: blocking validation/review issues exist.\n"
            "-- Review edchoice_universal_school_choice_validation.json and review CSV first.\n",
            encoding="utf-8",
        )
    else:
        write_sql(sql_path, new_records)

    write_report(
        config.OUTPUT_DIR / "edchoice_universal_school_choice_ingestion_report.md",
        source_url=source_url,
        summary=summary,
        validation_errors=validation_errors,
        warnings=warnings,
        output_names=OUTPUT_NAMES,
        mode=mode,
    )

    return (1 if validation_errors else 0), summary
