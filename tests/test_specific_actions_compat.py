from monitoring.adapter.outbound.persistence.specific_actions_compat import decode_specific_actions, encode_specific_actions


def test_r6_encoding_preserves_unicode_and_newlines() -> None:
    value = "Tutoría semanal — acción 1\nAcción 2: no dividir este texto"
    assert encode_specific_actions(value) == {"version": 1, "actions": [{"text": value}]}
    assert decode_specific_actions(encode_specific_actions(value)) == value


def test_compatibility_accepts_legacy_text() -> None:
    assert decode_specific_actions("Schedule follow-up") == "Schedule follow-up"


def test_malformed_document_does_not_leak_as_api_contract() -> None:
    assert decode_specific_actions({"version": 1, "actions": []}) == "{'version': 1, 'actions': []}"
