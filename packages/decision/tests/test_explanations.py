import pytest
from arrive90_decision.explanations import TEMPLATES, explanation_text


def test_every_explanation_code_has_a_noncausal_sentence_template() -> None:
    assert explanation_text("ACTIVE_SERVICE_ALERT") == TEMPLATES["ACTIVE_SERVICE_ALERT"]
    assert all(text.endswith(".") for text in TEMPLATES.values())
    with pytest.raises(ValueError, match="unknown"):
        explanation_text("MODEL_CAUSED_DELAY")
