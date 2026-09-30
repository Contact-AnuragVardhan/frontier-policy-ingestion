import json
from pathlib import Path

from app import config
from app.dedupe import classify_against_existing
from app.sources.ai_laws_education import (
    DetailInfo,
    TrackerRow,
    candidate_from_source,
    categories_for_source,
    normalize_identifier_display,
    normalized_identifier_key,
    parse_detail_html,
    parse_tracker_html,
    parse_tracker_page_metadata,
    status_for_source,
)
from app.sources.multistate import load_fixture

FIXTURE_DIR = config.ROOT / "data" / "fixtures" / "ai_laws_education"


def test_tracker_parser_reads_public_table_rows_and_detail_links():
    html = (FIXTURE_DIR / "tracker_sample.html").read_text(encoding="utf-8")
    rows = parse_tracker_html(html)

    assert len(rows) == 5
    assert rows[0].state_name == "Illinois"
    assert rows[0].identifier_raw == "SB 3735"
    assert rows[0].research_record_url == "https://www.ailawsbystate.com/law/IL/sb-3735"
    assert rows[1].state_name == "District of Columbia"


def test_detail_parser_extracts_official_source_and_source_dates():
    html = (FIXTURE_DIR / "detail_sample.html").read_text(encoding="utf-8")
    detail = parse_detail_html(html, "https://www.ailawsbystate.com/law/IL/sb-3735")

    assert detail.title == "IL SB 3735: Student data privacy and artificial intelligence."
    assert detail.official_source_url.startswith("https://www.ilga.gov/")
    assert detail.source_status == "In Committee"
    assert detail.last_action_date == "2026-09-18"
    assert detail.last_verified_date == "2026-09-20"
    assert detail.data_updated_date == "2026-09-24"


def test_identifier_normalization_detects_formatting_variations():
    assert normalized_identifier_key("SB2909") == normalized_identifier_key("SB 2909")
    assert normalized_identifier_key("SB-2909") == normalized_identifier_key("SB 2909")
    assert normalized_identifier_key("A09190") == normalized_identifier_key("A 9190")
    assert normalize_identifier_display("SB2909") == "SB 2909"
    assert normalize_identifier_display("A09190") == "A 9190"
    assert normalize_identifier_display("B26-0491") == "B26-0491"


def test_category_mapping_is_conservative_and_deterministic():
    primary, categories, issues = categories_for_source(
        "AI Literacy & Curriculum", "Requires AI literacy education or curriculum standards"
    )
    assert (primary, categories, issues) == ("AI Literacy", ("AI Literacy",), [])

    primary, categories, issues = categories_for_source(
        "Student Data Privacy & AI", "Protects student data from AI collection or processing"
    )
    assert primary == "Student Privacy"
    assert categories == ("Student Privacy", "AI Use")
    assert issues == []

    primary, categories, issues = categories_for_source(
        "Teacher Use of AI / PD", "Requires teacher use of AI under educator supervision"
    )
    assert primary == "AI Use"
    assert categories == ("AI Use",)
    assert issues == []

    primary, categories, issues = categories_for_source(
        "Teacher Use of AI / PD", "Requires professional development for teachers using AI"
    )
    assert primary == "AI Literacy"
    assert categories == ("AI Literacy",)
    assert issues == []

    primary, categories, issues = categories_for_source(
        "Teacher Use of AI / PD", "Governs teacher use of AI or professional development requirements"
    )
    assert primary is None
    assert categories == ()
    assert "manual category review required" in issues[0]


def test_status_mapping_does_not_treat_terminal_or_unknown_as_pending():
    assert status_for_source("Introduced") == "Pending"
    assert status_for_source("In Committee") == "Pending"
    assert status_for_source("Passed One Chamber") == "Pending"
    assert status_for_source("Passed Both Chambers") == "Pending"
    assert status_for_source("Enacted") == "Enacted"
    assert status_for_source("Dead") == "Inactive"
    assert status_for_source("Dead/Failed") == "Inactive"
    assert status_for_source("Vetoed") == "Inactive"
    assert status_for_source("Unknown") == "Unknown"


def test_dc_is_supported_without_frontend_schema_change():
    row = TrackerRow(
        state_name="District of Columbia",
        identifier_raw="B26-0491",
        year=2025,
        source_category="Generative AI in Classrooms",
        source_status="Introduced",
        key_requirement="Regulates generative AI use in educational settings",
        research_record_url="https://www.ailawsbystate.com/law/DC/b26-0491",
    )
    detail = DetailInfo(
        title="DC B26-0491: Artificial intelligence in education.",
        official_source_url="https://lims.dccouncil.gov/Legislation/B26-0491",
        source_status="Introduced",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert candidate.state_code == "DC"
    assert candidate.issues == []


def test_missing_official_source_is_review_only_invalid():
    row = TrackerRow(
        state_name="California",
        identifier_raw="AB2876",
        year=2025,
        source_category="AI Literacy & Curriculum",
        source_status="Enacted",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/CA/ab2876",
    )
    detail = DetailInfo(
        title="CA AB 2876: Artificial intelligence literacy.",
        source_status="Enacted",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert "Official government/legislative source URL is missing" in candidate.issues


def test_duplicate_detection_against_existing_multistate_preserves_provenance():
    existing = load_fixture(config.FIXTURE_PATH)
    row = TrackerRow(
        state_name="Illinois",
        identifier_raw="SB3735",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="In Committee",
        key_requirement="Protects student data from AI collection or processing",
        research_record_url="https://www.ailawsbystate.com/law/IL/sb-3735",
    )
    detail = DetailInfo(
        title="IL SB 3735: Student data privacy and artificial intelligence.",
        official_source_url="https://www.ilga.gov/Legislation/BillStatus?DocNum=3735&DocTypeID=SB&GAID=18&LegId=167016&SessionID=114",
        source_status="In Committee",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    candidates, duplicates, conflicts = classify_against_existing([candidate], existing)

    assert candidates[0].classification == "DUPLICATE_EXISTING"
    assert len(duplicates) == 1
    assert duplicates[0]["existing_research_source_name"] == "MultiState"
    assert any(item["field"] == "status_detail" for item in conflicts)


def test_effective_date_is_never_invented_from_tracker_dates():
    row = TrackerRow(
        state_name="Florida",
        identifier_raw="S1194",
        year=2026,
        source_category="AI Literacy & Curriculum",
        source_status="Dead",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/FL/s1194",
    )
    detail = DetailInfo(
        official_source_url="https://www.flsenate.gov/Session/Bill/2026/1194",
        source_status="Dead",
        last_action_date="2026-05-01",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert candidate.effective_date is None
    assert candidate.status == "Inactive"
    assert candidate.status_as_of_date == "2026-09-24"
    assert candidate.last_updated == "2026-09-24"
    assert candidate.last_verified_at == "2026-09-20"


def test_tracker_page_metadata_reads_live_counts_when_present():
    html = """
    <html><body>
      <p>36 states, 185 bills</p>
      <p>Showing 185 of 185 bills</p>
    </body></html>
    """
    metadata = parse_tracker_page_metadata(html)
    assert metadata.declared_state_count == 36
    assert metadata.declared_bill_count == 185
    assert metadata.showing_count == 185
    assert metadata.total_count == 185


def test_status_as_of_uses_latest_source_freshness_date():
    row = TrackerRow(
        state_name="California",
        identifier_raw="AB2504",
        year=2026,
        source_category="AI Literacy & Curriculum",
        source_status="Enacted",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/CA/ab-2504",
    )
    detail = DetailInfo(
        official_source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260AB2504",
        source_status="Enacted",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert candidate.status_as_of_date == "2026-09-24"
    assert candidate.last_updated == "2026-09-24"


def test_explicit_effective_date_is_preserved_but_not_inferred():
    row = TrackerRow(
        state_name="Utah",
        identifier_raw="SB0322",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="Enacted",
        key_requirement="Protects student data from AI collection or processing",
        research_record_url="https://www.ailawsbystate.com/law/UT/sb-0322",
    )
    detail = DetailInfo(
        official_source_url="https://le.utah.gov/~2026/bills/static/SB0322.html",
        source_status="Enacted",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
        effective_date="2026-07-01",
    )
    candidate = candidate_from_source(row, detail)
    assert candidate.effective_date == "2026-07-01"


def test_research_snapshot_date_is_not_used_as_existing_bill_year():
    existing = load_fixture(config.FIXTURE_PATH)
    # IL SB 3735 has no explicit four-digit year in its official URL. The MultiState
    # research snapshot date is 2026-04-09 and must not be treated as bill-year evidence.
    current = next(r for r in existing if r.state_code == "IL" and r.policy_identifier == "SB 3735")
    row = TrackerRow(
        state_name="Illinois",
        identifier_raw="SB3735",
        year=2025,
        source_category="Student Data Privacy & AI",
        source_status="In Committee",
        key_requirement="Protects student data from AI collection or processing",
        research_record_url="https://www.ailawsbystate.com/law/IL/sb-3735",
    )
    detail = DetailInfo(
        official_source_url=current.source_url,
        source_status="In Committee",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    candidates, _, _ = classify_against_existing([candidate], [current])
    assert candidates[0].classification == "DUPLICATE_EXISTING"


def test_same_official_source_overrides_tracker_year_difference():
    existing = load_fixture(config.FIXTURE_PATH)
    current = next(r for r in existing if r.state_code == "NY" and r.policy_identifier == "A 9190")
    row = TrackerRow(
        state_name="New York",
        identifier_raw="A09190",
        year=2026,
        source_category="AI Literacy & Curriculum",
        source_status="In Committee",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/NY/a-09190",
    )
    detail = DetailInfo(
        official_source_url=current.source_url,
        source_status="In Committee",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    candidates, _, _ = classify_against_existing([candidate], [current])
    assert candidates[0].classification == "DUPLICATE_EXISTING"


def test_aggregator_url_is_never_accepted_as_official_source():
    row = TrackerRow(
        state_name="California",
        identifier_raw="AB2876",
        year=2025,
        source_category="AI Literacy & Curriculum",
        source_status="Enacted",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/CA/ab2876",
    )
    detail = DetailInfo(
        official_source_url="https://www.ailawsbystate.com/law/CA/ab2876",
        source_status="Enacted",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert any("research aggregator" in issue for issue in candidate.issues)


def test_teacher_pd_generic_requirement_uses_detail_title_conservatively():
    primary, categories, issues = categories_for_source(
        "Teacher Use of AI / PD",
        "Governs teacher use of AI or professional development requirements",
        "MD HB1057 : Education - Artificial Intelligence - Guidelines, Professional Development, and Collaborative",
    )
    assert primary == "AI Literacy"
    assert categories == ("AI Literacy",)
    assert issues == []

    primary, categories, issues = categories_for_source(
        "Teacher Use of AI / PD",
        "Governs teacher use of AI or professional development requirements",
        "NY A06720 : Prohibits the use of biometric identifying technology in schools",
    )
    assert primary is None
    assert categories == ()
    assert any("manual category review" in issue for issue in issues)

    primary, categories, issues = categories_for_source(
        "Teacher Use of AI / PD",
        "Governs teacher use of AI or professional development requirements",
        "HI HR202 : Congratulating the librarian and school principal of the year",
    )
    assert primary is None
    assert categories == ()
    assert any("manual category review" in issue for issue in issues)


def test_source_consistency_flags_obviously_unrelated_tracker_detail_title():
    row = TrackerRow(
        state_name="Alabama",
        identifier_raw="HB197",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="In Committee",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url="https://www.ailawsbystate.com/law/AL/hb197",
    )
    detail = DetailInfo(
        title="AL HB197 : Community development district; annexation provisions",
        official_source_url="https://alison.legislature.state.al.us/bill-search",
        source_status="In Committee",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert candidate.issues == []
    assert any("no obvious AI, education/student, or privacy signal" in flag for flag in candidate.review_flags)

    candidates, _, _ = classify_against_existing([candidate], [])
    assert candidates[0].classification == "POSSIBLE_MATCH_REVIEW"
    assert not candidates[0].is_valid_for_db()


def test_source_consistency_flags_explicit_official_url_year_mismatch():
    row = TrackerRow(
        state_name="Maryland",
        identifier_raw="HB807",
        year=2023,
        source_category="Teacher Use of AI / PD",
        source_status="Unknown",
        key_requirement="Governs teacher use of AI or professional development requirements",
        research_record_url="https://www.ailawsbystate.com/law/MD/hb807",
    )
    detail = DetailInfo(
        title="MD HB807 : Education - Teacher Preparation Programs - English Language Learner Teacher Competency Requirements",
        official_source_url="https://mgaleg.maryland.gov/mgawebsite/Legislation/Details/hb0807?ys=2026RS",
        source_status="Unknown",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert any("Tracker year 2023" in flag for flag in candidate.review_flags)
    assert any("manual category review" in issue for issue in candidate.issues)


def test_new_york_odd_even_session_years_are_same_bill_even_with_different_official_urls():
    existing = load_fixture(config.FIXTURE_PATH)
    current = next(r for r in existing if r.state_code == "NY" and r.policy_identifier == "A 9190")
    row = TrackerRow(
        state_name="New York",
        identifier_raw="A09190",
        year=2026,
        source_category="AI Literacy & Curriculum",
        source_status="In Committee",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/NY/a-09190",
    )
    detail = DetailInfo(
        title="NY A09190 : Prohibits the use of most artificial intelligence in classrooms prior to high school",
        official_source_url="https://www.nysenate.gov/legislation/bills/2025/A9190",
        source_status="In Committee",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert candidate.review_flags == []
    candidates, duplicates, _ = classify_against_existing([candidate], [current])
    assert candidates[0].classification == "DUPLICATE_EXISTING"
    assert duplicates[0]["merge_recommendation"].startswith("PRESERVE_EXISTING") or duplicates[0]["merge_recommendation"].startswith("REVIEW_")


def test_duplicate_conflicts_are_recommendations_not_automatic_overwrites():
    existing = load_fixture(config.FIXTURE_PATH)
    current = next(r for r in existing if r.state_code == "IL" and r.policy_identifier == "SB 3735")
    row = TrackerRow(
        state_name="Illinois",
        identifier_raw="SB3735",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="In Committee",
        key_requirement="Protects student data from AI collection or processing",
        research_record_url="https://www.ailawsbystate.com/law/IL/sb-3735",
    )
    detail = DetailInfo(
        title="IL SB 3735: Student data privacy and artificial intelligence.",
        official_source_url=current.source_url,
        source_status="In Committee",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    _, duplicates, conflicts = classify_against_existing([candidate], [current])
    assert duplicates[0]["merge_recommendation"]
    assert conflicts
    assert all(item["resolution"] in {"REVIEW_INCOMING_VALUE", "SAFE_ENRICHMENT_CANDIDATE"} for item in conflicts)


def test_truncated_artificial_intel_title_is_still_recognized_as_ai_signal():
    row = TrackerRow(
        state_name="Maine",
        identifier_raw="LD109",
        year=2025,
        source_category="Student Data Privacy & AI",
        source_status="Enacted",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url="https://www.ailawsbystate.com/law/ME/ld109",
    )
    detail = DetailInfo(
        title="ME LD109 : Resolve, Directing the Maine Arts Commission to Study Copyright Infringement by Artificial Intel…",
        official_source_url="https://legislature.maine.gov/legis/bills/display_ps.asp?LD=109&snum=132",
        source_status="Enacted",
        last_verified_date="2026-05-04",
        data_updated_date="2026-05-08",
    )
    candidate = candidate_from_source(row, detail)
    assert not any("no obvious AI" in flag for flag in candidate.review_flags)


def test_year_mismatch_is_non_blocking_when_identity_and_title_are_otherwise_valid():
    row = TrackerRow(
        state_name="California",
        identifier_raw="AB2876",
        year=2025,
        source_category="AI Literacy & Curriculum",
        source_status="Enacted",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/CA/ab2876",
    )
    detail = DetailInfo(
        title="CA AB2876 : Pupil instruction: media literacy: artificial intelligence literacy: curriculum frameworks",
        official_source_url="https://leginfo.legislature.ca.gov/faces/billStatusClient.xhtml?bill_id=202320240AB2876",
        source_status="Enacted",
        last_verified_date="2026-05-01",
        data_updated_date="2026-05-01",
    )
    candidate = candidate_from_source(row, detail)
    assert any(flag.startswith("WARNING: Tracker year 2025") for flag in candidate.review_flags)
    assert not candidate.has_blocking_review_flags()
    candidates, _, _ = classify_against_existing([candidate], [])
    assert candidates[0].classification == "NEW"
    assert candidates[0].is_valid_for_db()


def test_chatbot_title_counts_as_ai_signal_for_source_consistency_guard():
    row = TrackerRow(
        state_name="Virginia",
        identifier_raw="HB669",
        year=2026,
        source_category="Generative AI in Classrooms",
        source_status="Introduced",
        key_requirement="Regulates generative AI use in educational settings",
        research_record_url="https://www.ailawsbystate.com/law/VA/hb669",
    )
    detail = DetailInfo(
        title="VA HB669 : Impersonation of certain licensed professionals by chatbot; definitions, notice, civil liability.",
        official_source_url="https://lis.virginia.gov/bill-details/20261/HB669",
        source_status="Introduced",
        last_verified_date="2026-05-04",
        data_updated_date="2026-05-04",
    )
    candidate = candidate_from_source(row, detail)
    assert not any("no obvious AI" in flag for flag in candidate.review_flags)



def test_bill_identity_gate_flags_school_choice_vs_political_advertising_collision():
    row = TrackerRow(
        state_name="Hawaii",
        identifier_raw="SB278",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="Introduced",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url="https://www.ailawsbystate.com/law/HI/sb278-relating-to-political-advertising",
    )
    detail = DetailInfo(
        title="HI SB278 : School Choice Scholarship Program",
        official_source_url="https://www.capitol.hawaii.gov/measure_indiv.aspx?billtype=SB&billnumber=278&year=2013",
        source_status="Introduced",
        last_verified_date="2026-05-03",
        data_updated_date="2026-05-03",
    )
    candidate = candidate_from_source(row, detail)
    assert any(flag.startswith("Bill identity mismatch:") for flag in candidate.review_flags)
    candidates, _, _ = classify_against_existing([candidate], [])
    assert candidates[0].classification == "POSSIBLE_MATCH_REVIEW"
    assert not candidates[0].is_valid_for_db()


def test_bill_identity_gate_flags_autonomous_vehicle_vs_school_record_collision():
    row = TrackerRow(
        state_name="Illinois",
        identifier_raw="HB2575",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="In Committee",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url="https://www.ailawsbystate.com/law/IL/hb2575-autonomous-vehicle-act",
    )
    detail = DetailInfo(
        title="IL HB2575 : SCH CD-APPOINTED STATE WORK",
        official_source_url="https://ilga.gov/Legislation/BillStatus?DocNum=2575",
        source_status="In Committee",
        last_verified_date="2026-06-05",
        data_updated_date="2026-06-05",
    )
    candidate = candidate_from_source(row, detail)
    assert any(flag.startswith("Bill identity mismatch:") for flag in candidate.review_flags)


def test_bill_identity_gate_flags_school_title_vs_facial_recognition_detail_identity():
    row = TrackerRow(
        state_name="Maryland",
        identifier_raw="HB1046",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="Unknown",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url=(
            "https://www.ailawsbystate.com/law/MD/"
            "hb1046-criminal-procedure-facial-recognition-technology-requirements-procedures"
        ),
    )
    detail = DetailInfo(
        title="MD HB1046 : School and School-Sponsored Activities - Report of Suspected Abuse or Neglect - Parental Notification",
        official_source_url="https://mgaleg.maryland.gov/mgawebsite/Legislation/Details/hb1046?ys=2026RS",
        source_status="Unknown",
        last_verified_date="2026-08-25",
        data_updated_date="2026-09-05",
    )
    candidate = candidate_from_source(row, detail)
    assert any(flag.startswith("Bill identity mismatch:") for flag in candidate.review_flags)


def test_bill_identity_gate_does_not_flag_ai_title_vs_education_slug_without_conflicting_domain():
    row = TrackerRow(
        state_name="Tennessee",
        identifier_raw="SB514",
        year=2026,
        source_category="AI Literacy & Curriculum",
        source_status="Introduced",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url=(
            "https://www.ailawsbystate.com/law/TN/"
            "sb-514-education-curriculum-as-introduced-requires-local-education-agencies-and"
        ),
    )
    detail = DetailInfo(
        title="TN SB514 : AN ACT to amend Tennessee Code Annotated, Title 49, relative to artificial intelligence.",
        official_source_url="https://wapp.capitol.tn.gov/apps/BillInfo/Default.aspx?BillNumber=SB0514",
        source_status="Introduced",
        last_verified_date="2026-05-04",
        data_updated_date="2026-05-04",
    )
    candidate = candidate_from_source(row, detail)
    assert not any(flag.startswith("Bill identity mismatch:") for flag in candidate.review_flags)
    assert not any(flag.startswith("Education-scope mismatch:") for flag in candidate.review_flags)


def test_education_scope_gate_flags_non_education_health_ai_title():
    row = TrackerRow(
        state_name="California",
        identifier_raw="SB903",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="Passed Both Chambers",
        key_requirement="Protects student data from AI collection or processing",
        research_record_url=(
            "https://www.ailawsbystate.com/law/CA/"
            "sb-903-mental-health-professionals-artificial-intelligence"
        ),
    )
    detail = DetailInfo(
        title="CA SB 903 : Mental health professionals: artificial intelligence.",
        official_source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260SB903",
        source_status="Passed Both Chambers",
        last_verified_date="2026-06-03",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    assert any(flag.startswith("Education-scope mismatch:") for flag in candidate.review_flags)
    candidates, _, _ = classify_against_existing([candidate], [])
    assert candidates[0].classification == "POSSIBLE_MATCH_REVIEW"
    assert not candidates[0].is_valid_for_db()

def test_review_resolution_verified_teacher_rows_become_db_eligible_categories():
    from app.ai_laws_review_resolutions import apply_review_resolutions

    cases = [
        ("Illinois", "SB1677", ("AI Use",)),
        ("Indiana", "HB1296", ("AI Use",)),
        ("Louisiana", "HR320", ("AI Literacy",)),
        ("Mississippi", "SB2429", ("AI Use", "AI Literacy")),
        ("Mississippi", "SB2062", ("AI Use", "AI Literacy")),
        ("New York", "A06972", ("AI Use", "AI Literacy", "Student Privacy", "School Procurement")),
        ("New York", "A06720", ("Student Privacy",)),
        ("New York", "S03827", ("Student Privacy",)),
    ]

    for state_name, identifier, expected_categories in cases:
        row = TrackerRow(
            state_name=state_name,
            identifier_raw=identifier,
            year=2026,
            source_category="Teacher Use of AI / PD",
            source_status="Introduced",
            key_requirement="Governs teacher use of AI or professional development requirements",
            research_record_url=f"https://www.ailawsbystate.com/law/test/{identifier.lower()}",
        )
        detail = DetailInfo(
            title=f"{state_name} {identifier}: reviewed official title",
            official_source_url=f"https://example.gov/{identifier}",
            source_status="Introduced",
            last_verified_date="2026-09-20",
            data_updated_date="2026-09-24",
        )
        candidate = candidate_from_source(row, detail)
        assert any("Teacher Use of AI / PD" in issue for issue in candidate.issues)

        apply_review_resolutions([candidate])
        assert candidate.review_disposition == "INCLUDE_VERIFIED"
        assert candidate.category == expected_categories[0]
        assert candidate.categories == expected_categories
        assert not any("Teacher Use of AI / PD" in issue for issue in candidate.issues)

        candidates, _, _ = classify_against_existing([candidate], [])
        assert candidates[0].classification == "NEW"
        assert candidates[0].is_valid_for_db()


def test_review_resolution_known_bad_teacher_rows_stay_visible_but_are_excluded():
    from app.ai_laws_review_resolutions import apply_review_resolutions

    cases = [
        ("Connecticut", "SB325"),
        ("Hawaii", "HR202"),
        ("Illinois", "HB5321"),
        ("Maryland", "HB807"),
        ("New York", "A07838"),
        ("New York", "A10217"),
        ("Oklahoma", "HB3544"),
    ]

    for state_name, identifier in cases:
        row = TrackerRow(
            state_name=state_name,
            identifier_raw=identifier,
            year=2026,
            source_category="Teacher Use of AI / PD",
            source_status="Introduced",
            key_requirement="Governs teacher use of AI or professional development requirements",
            research_record_url=f"https://www.ailawsbystate.com/law/test/{identifier.lower()}",
        )
        detail = DetailInfo(
            title=f"{state_name} {identifier}: unrelated reviewed title",
            official_source_url=f"https://example.gov/{identifier}",
            source_status="Introduced",
            last_verified_date="2026-09-20",
            data_updated_date="2026-09-24",
        )
        candidate = candidate_from_source(row, detail)
        apply_review_resolutions([candidate])
        assert candidate.review_disposition == "EXCLUDE_REVIEWED"
        assert candidate.is_reviewed_exclusion()
        assert not candidate.is_valid_for_db()


def test_source_title_mismatch_is_explicitly_excluded_from_sql_after_review_gate():
    from app.ai_laws_review_resolutions import apply_review_resolutions

    row = TrackerRow(
        state_name="Alabama",
        identifier_raw="HB197",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="In Committee",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url="https://www.ailawsbystate.com/law/AL/hb197",
    )
    detail = DetailInfo(
        title="AL HB197 : Community development district; annexation provisions",
        official_source_url="https://alison.legislature.state.al.us/bill-search",
        source_status="In Committee",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    apply_review_resolutions([candidate])
    assert candidate.review_disposition == "EXCLUDE_SOURCE_MISMATCH"
    candidates, _, _ = classify_against_existing([candidate], [])
    assert candidates[0].classification == "POSSIBLE_MATCH_REVIEW"
    assert candidates[0].is_reviewed_exclusion()
    assert not candidates[0].is_valid_for_db()


def test_federal_tracker_rows_are_explicit_out_of_scope_exclusions():
    from app.ai_laws_review_resolutions import apply_review_resolutions

    row = TrackerRow(
        state_name="United States (Federal)",
        identifier_raw="HB6077",
        year=2025,
        source_category="AI Literacy & Curriculum",
        source_status="Introduced",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/US/hb6077",
    )
    detail = DetailInfo(
        title="Federal HB6077 : Artificial intelligence education",
        official_source_url="https://www.congress.gov/bill/119th-congress/house-bill/6077",
        source_status="Introduced",
        last_verified_date="2026-09-20",
        data_updated_date="2026-09-24",
    )
    candidate = candidate_from_source(row, detail)
    apply_review_resolutions([candidate])
    assert candidate.review_disposition == "EXCLUDE_OUT_OF_SCOPE"
    assert candidate.is_reviewed_exclusion()
    assert not candidate.is_valid_for_db()


def test_pipeline_generates_sql_for_clean_subset_when_all_review_rows_have_resolved_exclusions(tmp_path, monkeypatch):
    from app import config
    from app.ai_laws_pipeline import run_ai_laws_ingestion

    fixture = {
        "tracker_html": "<!-- post-review integration fixture -->",
        "records": [
            {
                "row": {
                    "state_name": "Illinois",
                    "identifier_raw": "SB1677",
                    "year": 2025,
                    "source_category": "Teacher Use of AI / PD",
                    "source_status": "Introduced",
                    "key_requirement": "Governs teacher use of AI or professional development requirements",
                    "research_record_url": "https://www.ailawsbystate.com/law/IL/sb1677",
                },
                "detail": {
                    "title": "IL SB1677 : SCH CD-TEACHER EVALUATION PLAN",
                    "official_source_url": "https://www.ilga.gov/Legislation/BillStatus?DocNum=1677",
                    "source_status": "Introduced",
                    "last_verified_date": "2026-09-20",
                    "data_updated_date": "2026-09-24",
                },
            },
            {
                "row": {
                    "state_name": "Connecticut",
                    "identifier_raw": "SB325",
                    "year": 2026,
                    "source_category": "Teacher Use of AI / PD",
                    "source_status": "Passed One Chamber",
                    "key_requirement": "Governs teacher use of AI or professional development requirements",
                    "research_record_url": "https://www.ailawsbystate.com/law/CT/sb325",
                },
                "detail": {
                    "title": "CT SB325 : Public school employee address disclosure",
                    "official_source_url": "https://www.cga.ct.gov/bill/SB00325",
                    "source_status": "Passed One Chamber",
                    "last_verified_date": "2026-09-20",
                    "data_updated_date": "2026-09-24",
                },
            },
            {
                "row": {
                    "state_name": "Alabama",
                    "identifier_raw": "HB197",
                    "year": 2026,
                    "source_category": "Student Data Privacy & AI",
                    "source_status": "In Committee",
                    "key_requirement": "Regulates AI use in K-12 education",
                    "research_record_url": "https://www.ailawsbystate.com/law/AL/hb197",
                },
                "detail": {
                    "title": "AL HB197 : Community development district; annexation provisions",
                    "official_source_url": "https://alison.legislature.state.al.us/bill-search",
                    "source_status": "In Committee",
                    "last_verified_date": "2026-09-20",
                    "data_updated_date": "2026-09-24",
                },
            },
            {
                "row": {
                    "state_name": "United States (Federal)",
                    "identifier_raw": "HB6077",
                    "year": 2025,
                    "source_category": "AI Literacy & Curriculum",
                    "source_status": "Introduced",
                    "key_requirement": "Requires AI literacy education or curriculum standards",
                    "research_record_url": "https://www.ailawsbystate.com/law/US/hb6077",
                },
                "detail": {
                    "title": "Federal HB6077 : Artificial intelligence education",
                    "official_source_url": "https://www.congress.gov/bill/119th-congress/house-bill/6077",
                    "source_status": "Introduced",
                    "last_verified_date": "2026-09-20",
                    "data_updated_date": "2026-09-24",
                },
            },
        ],
    }
    fixture_path = tmp_path / "reviewed.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")

    exit_code, summary = run_ai_laws_ingestion(fixture_path=fixture_path)
    assert exit_code == 0
    assert summary["verified_include_count"] == 1
    assert summary["reviewed_exclude_count"] == 3
    assert summary["unresolved_review_count"] == 0
    assert summary["source_consistency_blocking_review_count"] == 0

    sql = (config.OUTPUT_DIR / "ai_laws_education.sql").read_text(encoding="utf-8")
    assert "SB 1677" in sql
    assert "SB 325" not in sql
    assert "HB 197" not in sql
    assert "HB 6077" not in sql


def test_pipeline_blocks_sql_for_unresolved_bill_identity_collision(tmp_path, monkeypatch):
    from app import config
    from app.ai_laws_pipeline import run_ai_laws_ingestion

    fixture = {
        "tracker_html": "<!-- identity collision fixture -->",
        "records": [
            {
                "row": {
                    "state_name": "Hawaii",
                    "identifier_raw": "SB999",
                    "year": 2026,
                    "source_category": "Student Data Privacy & AI",
                    "source_status": "Introduced",
                    "key_requirement": "Regulates AI use in K-12 education",
                    "research_record_url": "https://www.ailawsbystate.com/law/HI/sb999-relating-to-political-advertising",
                },
                "detail": {
                    "title": "HI SB999 : School Choice Scholarship Program",
                    "official_source_url": "https://www.capitol.hawaii.gov/measure_indiv.aspx?billtype=SB&billnumber=999&year=2013",
                    "source_status": "Introduced",
                    "last_verified_date": "2026-05-03",
                    "data_updated_date": "2026-05-03",
                },
            }
        ],
    }
    fixture_path = tmp_path / "identity_collision.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")

    exit_code, summary = run_ai_laws_ingestion(fixture_path=fixture_path)
    assert exit_code == 1
    assert summary["bill_identity_review_count"] == 1
    assert summary["unresolved_review_count"] == 1
    assert not (config.OUTPUT_DIR / "ai_laws_education.sql").exists()


def test_reviewed_bill_identity_collision_is_explicitly_excluded():
    from app.ai_laws_review_resolutions import apply_review_resolutions
    from app.sources.ai_laws_education import Candidate, DetailInfo, TrackerRow

    row = TrackerRow(
        state_name="Hawaii",
        identifier_raw="SB278",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="Introduced",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url="https://www.ailawsbystate.com/law/HI/sb278-relating-to-political-advertising",
    )
    candidate = Candidate(
        raw=row,
        detail=DetailInfo(
            title="HI SB278 : School Choice Scholarship Program",
            official_source_url="https://www.capitol.hawaii.gov/measure_indiv.aspx?billtype=SB&billnumber=278&year=2013",
        ),
        state_code="HI",
        policy_identifier="SB 278",
        identifier_key="SB:278",
        category="Student Privacy",
        categories=("Student Privacy", "AI Use"),
        status="Pending",
        status_detail="Introduced",
        policy_type="Bill",
        summary="Regulates AI use in K-12 education",
        title="HI SB278 : School Choice Scholarship Program",
        source_url="https://www.capitol.hawaii.gov/measure_indiv.aspx?billtype=SB&billnumber=278&year=2013",
        research_source_url=row.research_record_url,
    )
    candidate.review_flags.append(
        "Bill identity mismatch: AI Laws detail URL title and official government title have no meaningful topic overlap and point to different subject matter"
    )
    candidate.classification = "POSSIBLE_MATCH_REVIEW"

    apply_review_resolutions([candidate])

    assert candidate.review_disposition == "EXCLUDE_BILL_IDENTITY_MISMATCH"
    assert candidate.is_reviewed_exclusion()
    assert not candidate.is_valid_for_db()


def test_reviewed_non_education_scope_record_is_explicitly_excluded():
    from app.ai_laws_review_resolutions import apply_review_resolutions
    from app.sources.ai_laws_education import Candidate, DetailInfo, TrackerRow

    row = TrackerRow(
        state_name="California",
        identifier_raw="SB 903",
        year=2026,
        source_category="Student Data Privacy & AI",
        source_status="Passed Both Chambers",
        key_requirement="Protects student data from AI collection or processing",
        research_record_url="https://www.ailawsbystate.com/law/CA/sb-903-mental-health-professionals-artificial-intelligence",
    )
    candidate = Candidate(
        raw=row,
        detail=DetailInfo(
            title="CA SB 903 : Mental health professionals: artificial intelligence.",
            official_source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260SB903",
        ),
        state_code="CA",
        policy_identifier="SB 903",
        identifier_key="SB:903",
        category="Student Privacy",
        categories=("Student Privacy", "AI Use"),
        status="Pending",
        status_detail="Passed Both Chambers",
        policy_type="Bill",
        summary="Protects student data from AI collection or processing",
        title="CA SB 903 : Mental health professionals: artificial intelligence.",
        source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260SB903",
        research_source_url=row.research_record_url,
    )
    candidate.review_flags.append(
        "Education-scope mismatch: official government title is centered on a non-education domain (HEALTH)"
    )
    candidate.classification = "POSSIBLE_MATCH_REVIEW"

    apply_review_resolutions([candidate])

    assert candidate.review_disposition == "EXCLUDE_NON_EDUCATION_SCOPE"
    assert candidate.is_reviewed_exclusion()
    assert not candidate.is_valid_for_db()


def test_tracker_year_vs_last_action_gap_is_not_session_identity_evidence():
    row = TrackerRow(
        state_name="Illinois",
        identifier_raw="HB2503",
        year=2023,
        source_category="Student Data Privacy & AI",
        source_status="Introduced",
        key_requirement="Protects student data from AI collection or processing",
        research_record_url="https://www.ailawsbystate.com/law/IL/hb2503-sch-cd-artificial-intelligence",
    )
    detail = DetailInfo(
        title="IL HB2503 : SCH CD-ARTIFICIAL INTELLIGENCE",
        official_source_url="https://www.ilga.gov/Legislation/BillStatus?DocNum=2503&GAID=18&DocTypeID=HB&SessionID=114&GA=104",
        source_status="Introduced",
        last_action_date="2025-04-23",
        last_verified_date="2026-05-17",
        data_updated_date="2026-07-13",
    )
    candidate = candidate_from_source(row, detail)
    assert not any("Last Action year" in flag for flag in candidate.review_flags)
    candidates, _, _ = classify_against_existing([candidate], [])
    assert candidates[0].classification == "NEW"
    assert candidates[0].is_valid_for_db()


def test_adjacent_tracker_year_and_last_action_year_are_allowed():
    row = TrackerRow(
        state_name="California",
        identifier_raw="AB2876",
        year=2025,
        source_category="AI Literacy & Curriculum",
        source_status="Enacted",
        key_requirement="Requires AI literacy education or curriculum standards",
        research_record_url="https://www.ailawsbystate.com/law/CA/ab2876-pupil-instruction-media-literacy-artificial-intelligence-literacy",
    )
    detail = DetailInfo(
        title="CA AB2876 : Pupil instruction: media literacy: artificial intelligence literacy: curriculum frameworks: instructional materials.",
        official_source_url="https://leginfo.legislature.ca.gov/faces/billStatusClient.xhtml?bill_id=202320240AB2876",
        source_status="Enacted",
        last_action_date="2024-09-28",
        last_verified_date="2026-05-01",
        data_updated_date="2026-05-01",
    )
    candidate = candidate_from_source(row, detail)
    assert not any("Last Action year" in flag for flag in candidate.review_flags)


def test_postsecondary_only_title_is_k12_scope_review():
    row = TrackerRow(
        state_name="California",
        identifier_raw="AB2392",
        year=2026,
        source_category="Generative AI in Classrooms",
        source_status="Introduced",
        key_requirement="Regulates generative AI use in educational settings",
        research_record_url="https://www.ailawsbystate.com/law/CA/ab-2392-public-postsecondary-education-artificial-intelligence-products-training",
    )
    detail = DetailInfo(
        title="CA AB 2392 : Public postsecondary education: generative artificial intelligence systems: procurement standards: training.",
        official_source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260AB2392",
        source_status="Introduced",
    )
    candidate = candidate_from_source(row, detail)
    assert any("postsecondary-only" in flag for flag in candidate.review_flags)


def test_mixed_k12_and_higher_ed_title_is_not_postsecondary_only():
    row = TrackerRow(
        state_name="Iowa",
        identifier_raw="HF2153",
        year=2028,
        source_category="Student Data Privacy & AI",
        source_status="Introduced",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url="https://www.ailawsbystate.com/law/IA/hf2153-a-bill-for-an-act-requiring-community-colleges-school-districts-and",
    )
    detail = DetailInfo(
        title="IA HF2153 : A bill for an act requiring community colleges, school districts, and institutions under the control of the state board of regents to adopt policies related to artificial intelligence.",
        official_source_url="https://www.legis.iowa.gov/legislation/BillBook?ga=91&ba=HF2153",
        source_status="Introduced",
        last_action_date="2026-01-26",
    )
    candidate = candidate_from_source(row, detail)
    assert not any("postsecondary-only" in flag for flag in candidate.review_flags)


def test_state_bar_ai_bill_is_outside_k12_scope():
    row = TrackerRow(
        state_name="California",
        identifier_raw="AB 1651",
        year=2028,
        source_category="Student Data Privacy & AI",
        source_status="Enacted",
        key_requirement="Regulates AI use in K-12 education",
        research_record_url="https://www.ailawsbystate.com/law/CA/ab-1651-state-bar-of-california-artificial-intelligence",
    )
    detail = DetailInfo(
        title="CA AB 1651 : State Bar of California: artificial intelligence.",
        official_source_url="https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260AB1651",
        source_status="Enacted",
        effective_date="2028-01-01",
        last_action_date="2026-08-22",
    )
    candidate = candidate_from_source(row, detail)
    assert any("Education-scope mismatch" in flag for flag in candidate.review_flags)


def test_verified_reused_session_collisions_are_reviewed_exclusions():
    from app.ai_laws_review_resolutions import apply_review_resolutions

    cases = [
        ("Tennessee", "HB1455", 2021, "TN", "HB:1455"),
        ("Texas", "HB2400", 2009, "TX", "HB:2400"),
        ("Washington", "SB6254", 2024, "WA", "SB:6254"),
    ]
    candidates = []
    for state_name, identifier, year, state_code, identifier_key in cases:
        row = TrackerRow(
            state_name=state_name,
            identifier_raw=identifier,
            year=year,
            source_category="Student Data Privacy & AI",
            source_status="Introduced",
            key_requirement="Regulates AI use in K-12 education",
            research_record_url=f"https://www.ailawsbystate.com/law/{state_code}/{identifier.lower()}",
        )
        candidate = candidate_from_source(
            row,
            DetailInfo(
                title=f"{state_code} {identifier} : Artificial intelligence education policy",
                official_source_url=f"https://example.gov/{identifier}",
                source_status="Introduced",
                last_action_date="2026-04-14",
                last_verified_date="2026-04-22",
                data_updated_date="2026-04-22",
            ),
        )
        assert candidate.identifier_key == identifier_key
        candidate.classification = "POSSIBLE_MATCH_REVIEW"
        candidates.append(candidate)

    apply_review_resolutions(candidates)
    assert all(c.review_disposition == "EXCLUDE_BILL_IDENTITY_MISMATCH" for c in candidates)
    assert all(c.is_reviewed_exclusion() for c in candidates)
