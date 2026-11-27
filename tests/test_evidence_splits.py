import pytest

from namma.evidence.splits import time_blocks


def test_timestamp_blocks_are_order_independent_and_disjoint():
    ids = [str(k) for k in range(91, 112)]
    split = time_blocks(reversed(ids), ["1"])
    assert split["train"] + split["dev"] + split["final_test"] == ids
    assert len(split["final_test"]) == 3
    assert split["excluded"] == [{"photo": "1", "reason": "incomplete capture"}]


@pytest.mark.parametrize("ids", [["1", "2"], ["1", "2", "3", "4", "01"]])
def test_invalid_split_fails_closed(ids):
    with pytest.raises(ValueError):
        time_blocks(ids)
