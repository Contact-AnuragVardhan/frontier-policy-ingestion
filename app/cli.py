import argparse
import json
import sys
from pathlib import Path

from app import config
from app.exporters import (
    write_db_csv,
    write_report,
    write_review_csv,
    write_source_metadata,
    write_upsert_sql,
    write_validation_json,
)
from app.sources.multistate import extract_source_metadata, fetch_html, load_fixture, parse_html
from app.supabase_loader import read_db_csv, upsert_policies
from app.validate import validate_records


def _fixture_metadata(records) -> dict:
    return {
        "research_source_name": "MultiState",
        "title": "How States Are Regulating AI in Education this Legislative Session",
        "url": config.MULTISTATE_SOURCE_URL,
        "source_date": config.ARTICLE_DATE,
        "tracked_bill_count_reported": 134,
        "tracked_state_count_reported": 31,
        "named_policy_rows_ingested": len(records),
        "named_states_ingested": len({r.state_code for r in records}),
        "sections": [
            "Student Data Privacy and Protection Measures",
            "AI Usage Boundaries and Oversight Requirements",
            "AI Literacy and Graduation Requirements",
        ],
        "note": "The article reports broader tracking totals but explicitly names only the highlighted Key Bills ingested by this adapter.",
    }


def build_outputs(use_fixture: bool = False) -> int:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if use_fixture:
        records = load_fixture(config.FIXTURE_PATH)
        source_metadata = _fixture_metadata(records)
        mode = "fixture"
    else:
        html = fetch_html(config.MULTISTATE_SOURCE_URL)
        records = parse_html(html, config.MULTISTATE_SOURCE_URL, config.ARTICLE_DATE)
        source_metadata = extract_source_metadata(html, config.MULTISTATE_SOURCE_URL, config.ARTICLE_DATE)
        mode = "live"

    errors, summary = validate_records(records)

    write_review_csv(config.OUTPUT_DIR / "policies_review.csv", records)
    write_db_csv(config.OUTPUT_DIR / "policies.csv", records)
    write_upsert_sql(config.OUTPUT_DIR / "policies.sql", records)
    write_validation_json(config.OUTPUT_DIR / "validation.json", errors, summary)
    write_source_metadata(config.OUTPUT_DIR / "source_metadata.json", source_metadata)
    write_report(
        config.OUTPUT_DIR / "ingestion_report.md",
        config.MULTISTATE_SOURCE_URL,
        config.ARTICLE_DATE,
        summary,
        errors,
    )

    print(json.dumps({"mode": mode, **summary}, indent=2))
    print(f"Outputs written to: {config.OUTPUT_DIR}")
    return 1 if errors else 0


def load_to_supabase(csv_path: Path, apply: bool) -> int:
    rows = read_db_csv(csv_path)
    print(f"Prepared {len(rows)} policies from {csv_path}")
    if not apply:
        print("DRY RUN only. Nothing was written. Re-run with --apply after reviewing policies_review.csv.")
        return 0
    result = upsert_policies(
        supabase_url=config.SUPABASE_URL,
        secret_key=config.SUPABASE_SECRET_KEY,
        rows=rows,
    )
    print(f"Supabase upsert completed. Returned rows: {len(result)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Frontier Education Project policy ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "ingest-multistate",
        help="Extract, enrich, validate and export Julia's dated MultiState source",
    )
    ingest.add_argument(
        "--use-fixture",
        action="store_true",
        help="Use the bundled Apr 9 source snapshot instead of fetching the website",
    )

    load = sub.add_parser("load-supabase", help="Upsert generated policies.csv into public.policies")
    load.add_argument("--file", default=str(config.OUTPUT_DIR / "policies.csv"))
    load.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to Supabase. Without this flag the command is a dry run.",
    )

    args = parser.parse_args()
    if args.command == "ingest-multistate":
        return build_outputs(args.use_fixture)
    if args.command == "load-supabase":
        return load_to_supabase(Path(args.file), args.apply)
    return 2


if __name__ == "__main__":
    sys.exit(main())
