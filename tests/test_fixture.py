from app import config
from app.sources.multistate import EXPECTED_KEYS, load_fixture
from app.validate import validate_records


def test_fixture_has_expected_shape():
    records = load_fixture(config.FIXTURE_PATH)
    errors, summary = validate_records(records)

    assert errors == []
    assert summary["record_count"] == 13
    assert summary["state_count"] == 11
    assert summary["primary_categories"] == {
        "AI Literacy": 4,
        "AI Use": 5,
        "Student Privacy": 4,
    }
    assert summary["statuses"] == {"Enacted": 1, "Pending": 12}
    assert {(r.state_code, r.policy_identifier) for r in records} == EXPECTED_KEYS


def test_multi_category_and_timeline_enrichment():
    records = load_fixture(config.FIXTURE_PATH)
    by_key = {(r.state_code, r.policy_identifier): r for r in records}

    assert by_key[("ID", "SB 1227")].categories == (
        "Student Privacy",
        "AI Use",
        "AI Literacy",
    )
    assert by_key[("IL", "SB 3735")].categories == (
        "Student Privacy",
        "Parental Consent",
        "AI Use",
    )
    assert by_key[("MS", "SB 2294")].implementation_timeline == (
        "Starting with the 2029-2030 ninth-grade class."
    )
    assert by_key[("GA", "SB 179")].implementation_timeline == (
        "Beginning in the 2031-2032 school year."
    )
