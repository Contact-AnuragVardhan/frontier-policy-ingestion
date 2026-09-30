from app.normalize import (
    canonical_bill_title,
    extract_policy_identifier,
    policy_type_for,
    status_group_for,
)


def test_status_mapping():
    assert status_group_for("Enacted") == "Enacted"
    assert status_group_for("Introduced") == "Pending"
    assert status_group_for("Passed 1st Chamber") == "Pending"
    assert status_group_for("Passed 2nd Chamber") == "Pending"


def test_policy_type_mapping():
    assert policy_type_for("Enacted") == "Law"
    assert policy_type_for("Introduced") == "Bill"


def test_identifier_and_title_normalization():
    assert extract_policy_identifier("CA AB 1159") == "AB 1159"
    assert extract_policy_identifier("NY A 9190") == "A 9190"
    assert canonical_bill_title("California", "AB 1159") == "California AB 1159"
    assert canonical_bill_title("New York", "A 9190") == "New York A 9190"
