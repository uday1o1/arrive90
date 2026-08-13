from __future__ import annotations

from arrive90_routing.equivalence import EquivalenceClassInput, enumerate_equivalence_classes


def test_equivalence_inventory_deduplicates_and_sorts_by_complete_signature() -> None:
    first = EquivalenceClassInput("calendar", "table", "walk", "06:00", 0)
    duplicate = EquivalenceClassInput("calendar", "table", "walk", "06:00", 0)
    different = EquivalenceClassInput("calendar", "table", "walk", "06:00", 5)
    result = enumerate_equivalence_classes((different, duplicate, first))
    assert len(result) == 2
    assert {item.class_id for item in result} == {first.class_id, different.class_id}
