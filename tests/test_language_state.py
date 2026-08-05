from agent.persona import instructions_for_language
from agent.state import (
    explicit_language_request,
    is_substantive_language_sample,
    select_language,
)


def test_initial_language_requires_substantive_supported_utterance():
    assert select_language(None, "haan", "hi-IN") is None
    assert select_language(None, "ok yes", "en-IN") is None
    assert select_language(None, "मुझे 2 BHK चाहिए", "hi-IN") == "hi-IN"
    assert select_language(None, "मला पुण्यात घर पाहिजे", "mr-IN") == "mr-IN"
    assert select_language(None, "I need a two bedroom flat", "en-IN") == "en-IN"
    assert select_language(None, "I need a flat", "en-US") is None


def test_selected_language_is_sticky_against_auto_detection():
    assert select_language("hi-IN", "This project looks good", "en-IN") == "hi-IN"
    assert select_language("mr-IN", "मुझे यह पसंद है", "hi-IN") == "mr-IN"


def test_explicit_request_switches_language_immediately():
    assert explicit_language_request("Can we switch to English please?") == "en-IN"
    assert explicit_language_request("हिंदी में बात कीजिए") == "hi-IN"
    assert explicit_language_request("मराठीत बोला") == "mr-IN"
    assert explicit_language_request("मराठी मध्ये बोला") == "mr-IN"
    assert select_language("hi-IN", "Marathi madhe bola", "hi-IN") == "mr-IN"


def test_language_name_without_request_does_not_switch():
    assert explicit_language_request("The English project brochure looks good") is None
    assert select_language("hi-IN", "The English project brochure looks good", "en-IN") == "hi-IN"


def test_substantive_filter_rejects_short_and_backchannel_turns():
    assert not is_substantive_language_sample("2 BHK")
    assert not is_substantive_language_sample("yes please")
    assert not is_substantive_language_sample("theek hai")
    assert is_substantive_language_sample("want residential property")


def test_language_instructions_reflect_current_selection():
    assert "selected language is Marathi (mr-IN)" in instructions_for_language("mr-IN")
    assert "No language is selected yet" in instructions_for_language(None)
