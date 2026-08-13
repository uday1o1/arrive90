from __future__ import annotations

import pytest
from arrive90_models.support import SupportManifest, deadline_support_cell


def test_support_cells_apply_all_prefit_count_and_service_day_rules() -> None:
    passing = deadline_support_cell(
        "slack-5-30", candidate_outcome_count=1_000, base_query_count=500, service_day_count=30
    )
    failing = deadline_support_cell(
        "slack-30-60", candidate_outcome_count=999, base_query_count=499, service_day_count=29
    )
    manifest = SupportManifest((passing, failing))
    assert manifest.is_eligible("slack-5-30")
    assert not manifest.is_eligible("slack-30-60")
    assert not manifest.is_eligible("unknown")
    assert len(failing.reasons) == 3
    with pytest.raises(ValueError, match="duplicate"):
        SupportManifest((passing, passing))
