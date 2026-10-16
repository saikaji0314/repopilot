from concurrent.futures import ThreadPoolExecutor

import pytest

from repopilot.runtime.budget import Budget, RunLimit


def test_unknown_and_abandoned_calls_remain_accounted_after_restart(tmp_path):
    path = tmp_path / "budget.sqlite3"
    budget = Budget(path, 1)
    unknown = budget.reserve("run-1", 0.4)
    budget.settle(unknown, None)
    budget.reserve("abandoned-run", 0.4)
    restarted = Budget(path, 100)
    assert restarted.snapshot()["cap_cny"] == 1
    assert restarted.snapshot()["accounted_cny"] == 0.8
    assert restarted.snapshot()["uncertain_calls"] == 2
    with pytest.raises(RunLimit):
        restarted.reserve("run-2", 0.3)
    assert restarted.snapshot()["calls"] == 2


def test_known_usage_releases_only_unused_reservation(tmp_path):
    budget = Budget(tmp_path / "budget.sqlite3", 1)
    call = budget.reserve("run-1", 0.8)
    budget.settle(call, 0.2)
    assert budget.snapshot()["accounted_cny"] == 0.2
    assert budget.snapshot()["uncertain_calls"] == 0


def test_concurrent_reservations_cannot_both_spend_same_budget(tmp_path):
    budget = Budget(tmp_path / "budget.sqlite3", 1)

    def attempt(_):
        try:
            budget.reserve("concurrent", 0.6)
            return True
        except RunLimit:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
