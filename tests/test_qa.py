import json

from empirical_contracts import QAResult

from fcv_empirical.common.qa import accumulate_qa, presence_counts, serialize_qa


def test_qa_accumulation_and_serialization_use_contracts() -> None:
    first = QAResult(check_id="schema", state="GREEN", message="ok")
    second = QAResult(check_id="coverage", state="YELLOW", message="inspect")
    results = accumulate_qa((first,), (second,))
    assert results == (first, second)
    check_ids = [item["check_id"] for item in json.loads(serialize_qa(results))]
    assert check_ids == ["schema", "coverage"]


def test_missing_and_zero_remain_distinct() -> None:
    counts = presence_counts([1, None, 0, None])
    assert counts == {"rows": 4, "observed": 2, "missing": 2}
