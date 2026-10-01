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



def run_ai_laws_education(
    *,
    source_url: str | None = None,
    existing_csv: Path | None = None,
    fixture_path: Path | None = None,
    timeout: int = 30,
) -> int:
    from app.ai_laws_pipeline import run_ai_laws_ingestion

    exit_code, _ = run_ai_laws_ingestion(
        source_url=source_url or config.AI_LAWS_EDUCATION_SOURCE_URL,
        existing_csv=existing_csv,
        fixture_path=fixture_path,
        timeout=timeout,
    )
    return exit_code



def run_edchoice_universal_school_choice(
    *,
    source_url: str | None = None,
    existing_csv: Path | None = None,
    fixture_path: Path | None = None,
    input_csv: Path | None = None,
    source_date: str | None = None,
    overrides_path: Path | None = None,
    timeout: int = 30,
) -> int:
    from app.edchoice_pipeline import run_edchoice_ingestion

    try:
        exit_code, summary = run_edchoice_ingestion(
            source_url=source_url or config.EDCHOICE_SCHOOL_CHOICE_SOURCE_URL,
            existing_csv=existing_csv,
            fixture_path=fixture_path,
            input_csv=input_csv,
            overrides_path=overrides_path,
            source_date_override=source_date,
            timeout=timeout,
        )
    except (OSError, ValueError) as exc:
        print(f"EdChoice ingestion stopped before policy generation: {exc}", file=sys.stderr)
        print("No production Supabase write occurred.", file=sys.stderr)
        print(
            "The command already tries EdChoice's first-party Universal School Choice page when the dashboard table is client-rendered. "
            "If both first-party HTML paths fail, export/copy the dashboard table to CSV and rerun with --input-csv and --source-date YYYY-MM-DD.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # requests/network errors can vary by installed urllib3/requests versions.
        print(f"EdChoice ingestion stopped before policy generation: {exc}", file=sys.stderr)
        print("No production Supabase write occurred; no source facts were invented.", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2))
    print(f"Review artifacts written to: {config.OUTPUT_DIR}")
    print("Production Supabase write: NO")
    return exit_code

def main() -> int:
    parser = argparse.ArgumentParser(description="AI Choice Map structured policy ingestion")
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

    ai_laws = sub.add_parser(
        "ingest-ai-laws-education",
        help=(
            "Fetch/normalize the AI Laws by State Education AI Tracker and generate "
            "review artifacts only; never writes production Supabase."
        ),
    )
    ai_laws.add_argument(
        "--source-url",
        default=None,
        help="Override the Education AI Tracker URL (normally not needed).",
    )
    ai_laws.add_argument(
        "--existing-csv",
        default=None,
        help="Existing policies.csv snapshot to use for duplicate/conflict comparison.",
    )
    ai_laws.add_argument(
        "--fixture",
        default=None,
        help="Use a local AI Laws fixture JSON instead of the live source (tests/review only).",
    )
    ai_laws.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds per request (default: 30).",
    )

    edchoice = sub.add_parser(
        "ingest-edchoice-universal-school-choice",
        help=(
            "Extract EdChoice Universal=Full school-choice records and generate review artifacts only; "
            "never writes production Supabase."
        ),
    )
    edchoice.add_argument(
        "--source-url",
        default=None,
        help="Override the EdChoice School Choice Dashboard URL (normally not needed).",
    )
    edchoice.add_argument(
        "--existing-csv",
        default=None,
        help="Existing policies.csv snapshot used for duplicate/conflict comparison.",
    )
    edchoice.add_argument(
        "--fixture",
        default=None,
        help="Use a local EdChoice fixture JSON instead of the live source (tests/review only).",
    )
    edchoice.add_argument(
        "--input-csv",
        default=None,
        help=(
            "Use a manually exported EdChoice dashboard CSV when the live table cannot be extracted reliably. "
            "Program detail pages are still fetched for qualifying Full rows."
        ),
    )
    edchoice.add_argument(
        "--source-date",
        default=None,
        help="Dashboard last-modified date as YYYY-MM-DD, mainly for --input-csv review runs.",
    )
    edchoice.add_argument(
        "--official-source-overrides",
        default=None,
        help="Optional reviewed JSON mapping for verified official government URLs.",
    )
    edchoice.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds per request (default: 30).",
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
    if args.command == "ingest-ai-laws-education":
        return run_ai_laws_education(
            source_url=args.source_url,
            existing_csv=Path(args.existing_csv) if args.existing_csv else None,
            fixture_path=Path(args.fixture) if args.fixture else None,
            timeout=args.timeout,
        )
    if args.command == "ingest-edchoice-universal-school-choice":
        return run_edchoice_universal_school_choice(
            source_url=args.source_url,
            existing_csv=Path(args.existing_csv) if args.existing_csv else None,
            fixture_path=Path(args.fixture) if args.fixture else None,
            input_csv=Path(args.input_csv) if args.input_csv else None,
            source_date=args.source_date,
            overrides_path=Path(args.official_source_overrides) if args.official_source_overrides else None,
            timeout=args.timeout,
        )
    if args.command == "load-supabase":
        return load_to_supabase(Path(args.file), args.apply)
    return 2


if __name__ == "__main__":
    sys.exit(main())
