"""Pipeline-step wrapper idempotency + force-override tests.

Covers all 4 daily India-regime steps:
  * regime_classifier_daily
  * regime_change_notifier
  * compute_daily_factors
  * attribution_daily_brinson

Each wrapper must:
  - Return ``{"skipped": True, "reason": "scope"}`` when scope is
    not india/all/empty.
  - Return ``{"skipped": True, "reason": "already_ran_today"}``
    (or ``"event_already_emitted"`` for the notifier) when the
    target table already carries today's row and force=False.
  - Pre-delete today's row + run the underlying job when
    force=True.
  - Run the underlying job when no row exists and force=False.
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.regime import pipeline_steps as ps


# --- regime_classifier_daily -----------------------------------

def test_classifier_skips_unsupported_scope() -> None:
    out = ps.run_regime_classifier_step(
        scope="us", run_id="x", repo=None,
    )
    assert out == {"skipped": True, "reason": "scope"}


def test_classifier_skips_when_today_present(monkeypatch) -> None:
    monkeypatch.setattr(
        ps, "_today_ist", lambda: date(2026, 5, 11),
    )
    monkeypatch.setattr(
        ps, "_regime_history_has_today", lambda d: True,
    )
    called: list = []
    monkeypatch.setattr(
        "backend.algo.regime.classifier_job.run_classifier_job",
        lambda payload: called.append(payload) or {"x": 1},
    )
    out = ps.run_regime_classifier_step(
        scope="india", run_id="x", repo=None, force=False,
    )
    assert out["skipped"] is True
    assert out["reason"] == "already_ran_today"
    assert called == []


def test_classifier_force_pre_deletes_and_runs(monkeypatch) -> None:
    monkeypatch.setattr(
        ps, "_today_ist", lambda: date(2026, 5, 11),
    )
    monkeypatch.setattr(
        ps, "_regime_history_has_today", lambda d: True,
    )
    deletes: list = []
    monkeypatch.setattr(
        ps, "_delete_iceberg_rows",
        lambda table, pred: deletes.append(table),
    )
    called: list = []
    monkeypatch.setattr(
        "backend.algo.regime.classifier_job.run_classifier_job",
        lambda payload: called.append("yes") or {"row": "x"},
    )
    out = ps.run_regime_classifier_step(
        scope="india", run_id="r", repo=None, force=True,
    )
    assert deletes == ["stocks.regime_history"]
    assert called == ["yes"]
    assert out["forced"] is True
    assert out["row"] == "x"


def test_classifier_runs_when_today_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        ps, "_today_ist", lambda: date(2026, 5, 11),
    )
    monkeypatch.setattr(
        ps, "_regime_history_has_today", lambda d: False,
    )
    called: list = []
    monkeypatch.setattr(
        "backend.algo.regime.classifier_job.run_classifier_job",
        lambda payload: called.append("yes") or {"row": "x"},
    )
    out = ps.run_regime_classifier_step(
        scope="india", run_id="r", repo=None, force=False,
    )
    assert called == ["yes"]
    assert out["forced"] is False


# --- regime_change_notifier ------------------------------------

def test_notifier_skips_when_event_already_emitted(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ps, "_today_ist", lambda: date(2026, 5, 11),
    )
    monkeypatch.setattr(
        ps, "_regime_changed_event_today", lambda d: True,
    )
    called: list = []
    monkeypatch.setattr(
        "backend.algo.jobs.regime_change_notifier.run_notifier",
        lambda as_of=None: called.append(as_of) or {"k": "v"},
    )
    out = ps.run_regime_notifier_step(
        scope="india", run_id="r", repo=None, force=False,
    )
    assert out["skipped"] is True
    assert out["reason"] == "event_already_emitted"
    assert called == []


def test_notifier_force_re_emits(monkeypatch) -> None:
    monkeypatch.setattr(
        ps, "_today_ist", lambda: date(2026, 5, 11),
    )
    monkeypatch.setattr(
        ps, "_regime_changed_event_today", lambda d: True,
    )
    called: list = []
    monkeypatch.setattr(
        "backend.algo.jobs.regime_change_notifier.run_notifier",
        lambda as_of=None: called.append(as_of) or {"flip": True},
    )
    out = ps.run_regime_notifier_step(
        scope="india", run_id="r", repo=None, force=True,
    )
    assert called == [date(2026, 5, 11)]
    assert out["forced"] is True
    assert out["emitted"] is True


# --- compute_daily_factors -------------------------------------

def test_factors_skips_when_today_present(monkeypatch) -> None:
    monkeypatch.setattr(
        ps, "_today_ist", lambda: date(2026, 5, 11),
    )
    monkeypatch.setattr(
        ps, "_daily_factors_has_today", lambda d: True,
    )
    called: list = []
    monkeypatch.setattr(
        "backend.algo.factors.compute_job.run_compute_job",
        lambda **kw: called.append(kw) or 0,
    )
    out = ps.run_factors_compute_step(
        scope="india", run_id="r", repo=None, force=False,
    )
    assert out["skipped"] is True
    assert called == []


def test_factors_force_pre_deletes_and_runs(monkeypatch) -> None:
    monkeypatch.setattr(
        ps, "_today_ist", lambda: date(2026, 5, 11),
    )
    monkeypatch.setattr(
        ps, "_daily_factors_has_today", lambda d: True,
    )
    deletes: list = []
    monkeypatch.setattr(
        ps, "_delete_iceberg_rows",
        lambda table, pred: deletes.append(table),
    )
    captured: dict = {}
    monkeypatch.setattr(
        "backend.algo.factors.compute_job.run_compute_job",
        lambda **kw: captured.update(kw) or 42,
    )
    out = ps.run_factors_compute_step(
        scope="india", run_id="r", repo=None, force=True,
    )
    assert deletes == ["stocks.daily_factors"]
    assert captured["as_of"] == date(2026, 5, 11)
    assert captured["days"] == 1
    assert out["rows_written"] == 42


# --- attribution_daily_brinson ---------------------------------

def test_attribution_skips_unsupported_scope() -> None:
    out = ps.run_attribution_brinson_step(
        scope="us", run_id="r", repo=None,
    )
    assert out == {"skipped": True, "reason": "scope"}


def test_attribution_force_pre_deletes_and_runs(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ps, "_today_ist", lambda: date(2026, 5, 11),
    )
    monkeypatch.setattr(
        ps, "_attribution_daily_has_today", lambda d: True,
    )
    deletes: list = []
    monkeypatch.setattr(
        ps, "_delete_attribution_today",
        lambda d: deletes.append(d),
    )
    called: list = []
    monkeypatch.setattr(
        "backend.algo.attribution.job.daily_brinson_job",
        lambda payload: called.append(payload) or {"rows": 3},
    )
    out = ps.run_attribution_brinson_step(
        scope="india", run_id="r", repo=None, force=True,
    )
    assert deletes == [date(2026, 5, 11)]
    assert called[0]["as_of"] == "2026-05-11"
    assert out["forced"] is True
    assert out["rows"] == 3
