from agent.worker import _extract_outbound_lead_context


def test_extracts_nested_outbound_metadata_and_attributes():
    context = _extract_outbound_lead_context(
        '{"lead":{"name":"Asha","phone":"+919999999999"},'
        '"source_channel":"Meta Ads","campaign":"Pune 2BHK",'
        '"project":"Little Earth","enquiry_id":"ENQ-42"}',
        {"lead.consent_reference": "CONSENT-7"},
    )

    assert context == {
        "caller_name": "Asha",
        "caller_phone": "+919999999999",
        "source_channel": "Meta Ads",
        "source_campaign": "Pune 2BHK",
        "source_project": "Little Earth",
        "source_enquiry_id": "ENQ-42",
        "consent_reference": "CONSENT-7",
    }


def test_invalid_outbound_metadata_is_ignored():
    context = _extract_outbound_lead_context("not-json")
    assert all(value is None for value in context.values())
