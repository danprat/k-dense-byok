"""Project-wide cost aggregation + budget gate tests.

Covers the two new helpers on ``cost_ledger``:

* ``read_project_costs`` walks every session dir under a project's runs
  directory and sums their costs.jsonl rows.
* ``check_project_budget`` maps ``(totalUsd, limitUsd)`` onto the three
  UI states: ``ok`` / ``warn`` / ``exceeded``.
"""

from __future__ import annotations

from kady_agent import cost_ledger


def test_read_project_costs_empty_project(active_project):
    summary = cost_ledger.read_project_costs(active_project.id)
    assert summary["projectId"] == active_project.id
    assert summary["totalUsd"] == 0.0
    assert summary["sessionCount"] == 0
    assert summary["bySession"] == {}


def test_read_project_costs_single_session(
    active_project, write_ledger, make_cost_entry
):
    write_ledger(
        "s1",
        [
            make_cost_entry(turnId="t1", role="orchestrator", costUsd=0.01, totalTokens=100),
            make_cost_entry(turnId="t1", role="expert", costUsd=0.02, totalTokens=200),
        ],
    )

    summary = cost_ledger.read_project_costs(active_project.id)
    assert summary["sessionCount"] == 1
    assert summary["totalUsd"] == 0.03
    assert summary["orchestratorUsd"] == 0.01
    assert summary["expertUsd"] == 0.02
    assert summary["totalTokens"] == 300
    assert summary["bySession"]["s1"]["totalUsd"] == 0.03
    assert summary["bySession"]["s1"]["hasPending"] is False


def test_read_project_costs_multi_session_sums_across(
    active_project, write_ledger, make_cost_entry
):
    write_ledger("s1", [make_cost_entry(turnId="t1", costUsd=0.50, totalTokens=100)])
    write_ledger("s2", [make_cost_entry(turnId="t2", costUsd=1.25, totalTokens=250)])

    summary = cost_ledger.read_project_costs(active_project.id)
    assert summary["sessionCount"] == 2
    assert summary["totalUsd"] == 1.75
    assert summary["totalTokens"] == 350
    assert set(summary["bySession"].keys()) == {"s1", "s2"}


def test_read_project_costs_propagates_pending(
    active_project, write_ledger, make_cost_entry
):
    write_ledger(
        "s1",
        [make_cost_entry(turnId="t1", costUsd=0.0, costPending=True, totalTokens=50)],
    )

    summary = cost_ledger.read_project_costs(active_project.id)
    assert summary["hasPending"] is True
    assert summary["bySession"]["s1"]["hasPending"] is True


def test_check_project_budget_no_limit(active_project, write_ledger, make_cost_entry):
    write_ledger("s1", [make_cost_entry(costUsd=5.0)])
    status = cost_ledger.check_project_budget(active_project.id, None)
    assert status["state"] == "ok"
    assert status["limitUsd"] is None
    assert status["ratio"] is None
    assert status["totalUsd"] == 5.0


def test_check_project_budget_under_80(active_project, write_ledger, make_cost_entry):
    write_ledger("s1", [make_cost_entry(costUsd=0.50)])
    status = cost_ledger.check_project_budget(active_project.id, 10.0)
    assert status["state"] == "ok"
    assert 0.0 < status["ratio"] < 0.8


def test_check_project_budget_warn_band(
    active_project, write_ledger, make_cost_entry
):
    write_ledger("s1", [make_cost_entry(costUsd=8.5)])
    status = cost_ledger.check_project_budget(active_project.id, 10.0)
    assert status["state"] == "warn"
    assert 0.8 <= status["ratio"] < 1.0


def test_check_project_budget_exceeded(
    active_project, write_ledger, make_cost_entry
):
    write_ledger("s1", [make_cost_entry(costUsd=10.5)])
    status = cost_ledger.check_project_budget(active_project.id, 10.0)
    assert status["state"] == "exceeded"
    assert status["ratio"] >= 1.0


def test_check_project_budget_zero_limit_is_unlimited(
    active_project, write_ledger, make_cost_entry
):
    """A limit of 0 (or negative) is treated as "no cap" so existing projects
    without an explicit value never accidentally block delegations."""
    write_ledger("s1", [make_cost_entry(costUsd=5.0)])
    status = cost_ledger.check_project_budget(active_project.id, 0)
    assert status["state"] == "ok"
    assert status["limitUsd"] is None
