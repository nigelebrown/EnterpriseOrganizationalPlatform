"""Protect the intended synthetic Section/Unit catalogue and mapping semantics."""
from scripts.seed_test_data import SECTIONS, ATTACHMENTS, code


def test_requested_section_definitions_are_present_exactly_once():
    entries = dict(SECTIONS)
    requested = {
        "CIB": "CIB",
        "PIU": "PIU",
        "GENOFF": "General Office",
        "ARMOURY": "Armoury",
        "OPSOFF": "Operations Office",
        "DIU": "DIU",
    }
    assert len(SECTIONS) == len(entries)  # unique codes
    assert set(entries).issuperset(requested)
    for k, display_name in requested.items():
        assert entries[k] == display_name
        assert code(k).startswith("DEMO-")


def test_existing_demo_section_mappings_remain_unchanged():
    attachments = set(ATTACHMENTS)
    assert ("SAC", "CIB") in attachments
    assert ("SAC", "DIU") in attachments
    assert ("SAS", "CIB") in attachments
    assert ("SAS", "DIU") in attachments
    # A definition does not imply that it exists within every Location.
    for key in ("PIU", "GENOFF", "ARMOURY", "OPSOFF"):
        assert not any(section_key == key for _, section_key in ATTACHMENTS)
