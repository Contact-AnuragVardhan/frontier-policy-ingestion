import json
from pathlib import Path

from app import config
from app.edchoice_pipeline import _classify_against_existing, run_edchoice_ingestion
from app.models import PolicyRecord
from app.sources.edchoice_school_choice import (
    CATEGORY,
    SOURCE_NAME,
    SOURCE_URL,
    UNIVERSAL_SOURCE_URL,
    DashboardRow,
    DetailInfo,
    candidate_from_source,
    load_official_source_overrides,
    parse_dashboard_html,
    parse_dashboard_source_date,
    parse_detail_html,
    parse_universal_school_choice_html,
)
from app.sources.multistate import load_fixture as load_multistate_fixture
from app.validate import validate_records

FIXTURE_DIR = config.ROOT / "data" / "fixtures" / "edchoice_school_choice"
FIXTURE_PATH = FIXTURE_DIR / "sample.json"


def _payload():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _candidate(index=0, *, overrides=True):
    item = _payload()["records"][index]
    row = DashboardRow(**item["row"])
    detail = DetailInfo(**item.get("detail", {}))
    override_map = (
        load_official_source_overrides(config.EDCHOICE_OFFICIAL_SOURCE_OVERRIDES_PATH)
        if overrides
        else {}
    )
    return candidate_from_source(
        row,
        detail,
        source_date="2026-08-27",
        official_source_overrides=override_map,
    )


def _existing_from(candidate, **overrides):
    record = candidate.to_policy_record()
    data = record.__dict__.copy()
    data.update(overrides)
    return PolicyRecord(**data)


def test_dashboard_parser_extracts_full_and_nonmatching_rows():
    html = (FIXTURE_DIR / "dashboard_sample.html").read_text(encoding="utf-8")
    rows = parse_dashboard_html(html)

    assert len(rows) == 6
    assert [r.state_name for r in rows if r.universal == "Full"] == [
        "Arizona",
        "Arkansas",
        "Florida",
        "New Hampshire",
        "West Virginia",
    ]
    assert rows[0].program_name == "Empowerment Scholarship Accounts"
    assert rows[0].program_url.endswith("arizona-empowerment-scholarship-accounts/")
    assert rows[-1].universal == "Eligibility"
    assert parse_dashboard_source_date(html) == "2026-08-27"


def test_detail_parser_extracts_edchoice_universality_and_official_url():
    html = (FIXTURE_DIR / "arizona_detail_sample.html").read_text(encoding="utf-8")
    detail = parse_detail_html(
        html,
        "https://www.edchoice.org/school-choice/programs/arizona-empowerment-scholarship-accounts/",
    )

    assert detail.title == "Arizona Empowerment Scholarship Accounts"
    assert detail.enacted_year == 2011
    assert detail.launched_year == 2011
    assert detail.truly_universal is True
    assert detail.universal_eligibility is True
    assert detail.universal_usage is True
    assert detail.universal_funding is True
    assert detail.official_source_url == "https://www.azed.gov/esa/resources"


def test_universal_page_fallback_includes_full_and_universal_eligibility_programs():
    html = (FIXTURE_DIR / "universal_school_choice_sample.html").read_text(encoding="utf-8")
    rows = parse_universal_school_choice_html(html)

    assert [row.state_name for row in rows] == [
        "Alabama",
        "Arizona",
        "Arkansas",
        "Florida",
        "New Hampshire",
        "West Virginia",
    ]
    by_state = {row.state_name: row for row in rows}
    assert by_state["Alabama"].universal == "Eligibility"
    assert all(by_state[state].universal == "Full" for state in ["Arizona", "Arkansas", "Florida", "New Hampshire", "West Virginia"])
    assert all(row.classification_source == "universal_school_choice_page" for row in rows)
    assert all(row.classification_source_url == UNIVERSAL_SOURCE_URL for row in rows)
    assert by_state["Arizona"].program_url.endswith("arizona-empowerment-scholarship-accounts/")


def test_universal_page_fallback_handles_nested_wordpress_section_wrappers():
    html = (FIXTURE_DIR / "universal_school_choice_nested_sample.html").read_text(encoding="utf-8")
    rows = parse_universal_school_choice_html(html)

    assert [row.state_name for row in rows] == [
        "Alabama",
        "Arizona",
        "Arkansas",
        "Florida",
        "New Hampshire",
        "West Virginia",
    ]
    assert rows[0].universal == "Eligibility"
    assert rows[0].program_name == "The Creating Hope and Opportunity for Our Students’ Education (CHOOSE) Act of 2024"
    assert all(row.universal == "Full" for row in rows[1:])


def test_exact_category_and_state_normalization_for_valid_full_row():
    candidate = _candidate(0)

    assert candidate.state_code == "AZ"
    assert candidate.category == "Universal School Choice"
    assert candidate.categories == ("Universal School Choice",)
    assert candidate.research_source_name == SOURCE_NAME
    assert candidate.research_source_url == SOURCE_URL
    assert candidate.research_source_date == "2026-08-27"
    assert candidate.last_verified_at == "2026-09-30"
    assert candidate.effective_date is None
    assert candidate.status == "Enacted"


def test_universal_eligibility_state_is_included_in_category():
    candidate = _candidate(5)

    assert candidate.raw.state_name == "Alabama"
    assert candidate.raw.universal == "Eligibility"
    assert candidate.classification == "NEW"
    assert candidate.category == "Universal School Choice"
    assert candidate.categories == ("Universal School Choice",)
    assert candidate.source_url == "https://www.revenue.alabama.gov/tax-policy/the-choose-act/"
    assert candidate.last_verified_at == "2026-10-01"
    assert "EdChoice dashboard Universal: Eligibility" in candidate.status_detail


def test_non_universal_na_row_is_excluded():
    row = DashboardRow(
        state_name="Georgia",
        program_type="Education Savings Account",
        program_name="Non-universal example",
        enacted_year=2025,
        launched_year=2025,
        universal="N/A",
    )
    candidate = candidate_from_source(row, DetailInfo(), source_date="2026-08-27", official_source_overrides={})

    assert candidate.classification == "EXCLUDED"
    assert candidate.category is None
    assert "Full, Eligibility" in candidate.exclusion_reason


def test_missing_official_source_stays_review_only_and_no_date_is_invented():
    row = DashboardRow(
        state_name="Arizona",
        program_type="Education Savings Account",
        program_name="Test Source-Supported Program",
        enacted_year=2026,
        launched_year=2026,
        universal="Full",
        program_url="https://www.edchoice.org/example",
    )
    detail = DetailInfo(
        title="Test Source-Supported Program",
        enacted_year=2026,
        launched_year=2026,
        truly_universal=True,
    )
    candidate = candidate_from_source(
        row,
        detail,
        source_date="2026-08-27",
        official_source_overrides={},
    )
    classified, _, _ = _classify_against_existing([candidate], [])

    assert classified[0].classification == "POSSIBLE_MATCH_REVIEW"
    assert classified[0].effective_date is None
    assert any("Missing verified official government source URL" in flag for flag in classified[0].review_flags)


def test_unknown_status_when_edchoice_does_not_supply_enacted_year():
    row = DashboardRow(
        state_name="Arizona",
        program_type="Education Savings Account",
        program_name="Program Without Enacted Year",
        enacted_year=None,
        launched_year=None,
        universal="Full",
    )
    detail = DetailInfo(title="Program Without Enacted Year", truly_universal=True)
    candidate = candidate_from_source(
        row,
        detail,
        source_date="2026-08-27",
        official_source_overrides={},
    )
    assert candidate.status == "Unknown"
    assert candidate.effective_date is None


def test_dashboard_detail_year_difference_is_preserved_as_warning_not_effective_date():
    candidate = _candidate(1)

    assert candidate.raw.state_name == "Arkansas"
    assert "EdChoice dashboard launched year: 2024" in candidate.status_detail
    assert "EdChoice program page launched year: 2023" in candidate.status_detail
    assert any(flag.startswith("WARNING:") for flag in candidate.review_flags)
    assert candidate.has_blocking_review_flags() is False
    assert candidate.effective_date is None


def test_official_source_override_is_program_specific_and_auditable():
    candidate = _candidate(3)

    assert candidate.raw.state_name == "New Hampshire"
    assert candidate.source_url == "https://gc.nh.gov/rsa/html/XV/194-F/194-F-mrg.htm"
    assert candidate.last_verified_at == "2026-09-30"
    assert "edchoice.org" not in candidate.source_url


def test_validator_rejects_edchoice_as_official_source_url():
    candidate = _candidate(0)
    record = _existing_from(candidate, source_url="https://www.edchoice.org/school-choice/dashboard/")
    errors, _ = validate_records([record])

    assert any("source_url must be an official source, not EdChoice" in error for error in errors)


def test_same_state_alone_is_not_a_duplicate():
    candidate = _candidate(0)
    existing = _existing_from(
        candidate,
        policy_identifier="Completely Different Policy",
        title="Completely Different Policy",
        source_url="https://www.azleg.gov/example",
    )

    classified, duplicates, _ = _classify_against_existing([candidate], [existing])
    assert classified[0].classification == "NEW"
    assert duplicates == []


def test_exact_state_program_identity_is_duplicate_existing():
    candidate = _candidate(0)
    existing = _existing_from(candidate)

    classified, duplicates, conflicts = _classify_against_existing([candidate], [existing])
    assert classified[0].classification == "DUPLICATE_EXISTING"
    assert len(duplicates) == 1
    assert conflicts == []


def test_matching_official_url_with_different_identifier_requires_review():
    candidate = _candidate(0)
    existing = _existing_from(
        candidate,
        policy_identifier="Different Source Identity",
        title="Different Source Identity",
    )

    classified, _, _ = _classify_against_existing([candidate], [existing])
    assert classified[0].classification == "POSSIBLE_MATCH_REVIEW"
    assert any("Official source URL matches" in flag for flag in classified[0].review_flags)


def test_rerun_is_idempotent_against_previously_inserted_records():
    first_pass = [_candidate(i) for i in range(6)]
    first_classified, _, _ = _classify_against_existing(first_pass, [])
    inserted = [c.to_policy_record() for c in first_classified if c.is_valid_for_db()]
    assert len(inserted) == 6

    second_pass = [_candidate(i) for i in range(6)]
    second_classified, duplicates, _ = _classify_against_existing(second_pass, inserted)
    assert {c.classification for c in second_classified} == {"DUPLICATE_EXISTING"}
    assert len(duplicates) == 6


def test_fixture_pipeline_generates_review_artifacts_without_supabase_write(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    exit_code, summary = run_edchoice_ingestion(fixture_path=FIXTURE_PATH)

    assert exit_code == 0
    assert summary["raw_source_entries"] == 6
    assert summary["qualifying_universal_school_choice_entries"] == 6
    assert summary["qualifying_full_entries"] == 5
    assert summary["qualifying_eligibility_entries"] == 1
    assert summary["classifications"]["NEW"] == 6
    assert summary["classifications"].get("EXCLUDED", 0) == 0
    assert summary["new_records_ready_for_sql"] == 6
    assert summary["qualifying_states"] == [
        "Alabama",
        "Arizona",
        "Arkansas",
        "Florida",
        "New Hampshire",
        "West Virginia",
    ]

    metadata = json.loads(
        (tmp_path / "edchoice_universal_school_choice_source_metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["production_supabase_modified"] is False
    assert metadata["chatbot_rag_modified"] is False
    assert (tmp_path / "edchoice_universal_school_choice_review.csv").exists()
    assert (tmp_path / "edchoice_universal_school_choice_validation.json").exists()
    sql = (tmp_path / "edchoice_universal_school_choice.sql").read_text(encoding="utf-8")
    assert sql.count("Universal School Choice") >= 6
    assert "insert into public.policies" in sql


def test_live_pipeline_falls_back_when_dashboard_table_is_client_rendered(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    dashboard_html = """
    <html><body>
      <h1>School Choice in America Dashboard</h1>
      <p>Suggested Citation</p>
      <p>School Choice in America Dashboard, EdChoice, last modified August 27, 2026.</p>
      <div id="client-rendered-dashboard"></div>
    </body></html>
    """
    universal_html = (FIXTURE_DIR / "universal_school_choice_sample.html").read_text(encoding="utf-8")
    detail_by_state = {
        item["row"]["state_name"]: DetailInfo(**item.get("detail", {}))
        for item in _payload()["records"]
    }

    def fake_fetch_html(url, **kwargs):
        if url == SOURCE_URL:
            return dashboard_html
        if url == UNIVERSAL_SOURCE_URL:
            return universal_html
        raise AssertionError(f"Unexpected fetch URL: {url}")

    def fake_fetch_details(rows, **kwargs):
        details = {(row.state_name, row.program_name): detail_by_state[row.state_name] for row in rows}
        return details, []

    monkeypatch.setattr("app.edchoice_pipeline.fetch_html", fake_fetch_html)
    monkeypatch.setattr("app.edchoice_pipeline.fetch_details_for_rows", fake_fetch_details)

    exit_code, summary = run_edchoice_ingestion()

    assert exit_code == 0
    assert summary["qualifying_universal_school_choice_entries"] == 6
    assert summary["qualifying_full_entries"] == 5
    assert summary["qualifying_eligibility_entries"] == 1
    assert summary["classifications"]["NEW"] == 6
    assert summary["extraction_method"] == "universal_school_choice_page_fallback"
    metadata = json.loads(
        (tmp_path / "edchoice_universal_school_choice_source_metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["extraction_method"] == "universal_school_choice_page_fallback"
    assert metadata["universal_fallback_url"] == UNIVERSAL_SOURCE_URL
    assert metadata["production_supabase_modified"] is False
    assert (tmp_path / "edchoice_universal_school_choice_universal_page_raw.html").exists()


def test_existing_multistate_fixture_still_validates_after_category_extension():
    records = load_multistate_fixture(config.FIXTURE_PATH)
    errors, summary = validate_records(records)
    assert errors == []
    assert summary["record_count"] == len(records)
    assert all(CATEGORY not in record.categories for record in records)


def test_reviewed_official_source_overrides_cover_live_universal_eligibility_gaps():
    overrides = load_official_source_overrides(config.EDCHOICE_OFFICIAL_SOURCE_OVERRIDES_PATH)
    expected = {
        ("alaska", "alaska correspondence school allotment program"): "https://www.akleg.gov/statutesPDF/Title-14.pdf",
        ("idaho", "idaho parental choice tax credit"): "https://tax.idaho.gov/taxes/income-tax/individual-income/popular-credits-and-deductions/parental-choice-tax-credit-and-advance-payment/",
        ("north carolina", "north carolina opportunity scholarships"): "https://www3.ncleg.gov/EnactedLegislation/Statutes/HTML/ByArticle/Chapter_115C/Article_39.html",
        ("tennessee", "tennessee education freedom scholarship act"): "https://www.tn.gov/education/efs.htm.html",
        ("texas", "texas education savings account program"): "https://capitol.texas.gov/billlookup/BillSummary.aspx?Bill=SB2&LegSess=89R",
    }
    for key, url in expected.items():
        assert overrides[key]["official_source_url"] == url
        assert overrides[key]["verified_at"] == "2026-10-01"
