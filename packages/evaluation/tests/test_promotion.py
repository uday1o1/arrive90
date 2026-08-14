from arrive90_evaluation.promotion import ReleaseMode, choose_release_mode


def test_release_mode_obeys_predeclared_learned_model_free_explorer_order() -> None:
    assert choose_release_mode(learned_passed=True, model_free_passed=True) is (
        ReleaseMode.LEARNED_RECOMMENDATION
    )
    assert choose_release_mode(learned_passed=False, model_free_passed=True) is (
        ReleaseMode.MODEL_FREE_RECOMMENDATION
    )
    assert choose_release_mode(learned_passed=False, model_free_passed=False) is (
        ReleaseMode.HISTORICAL_EXPLORER
    )
