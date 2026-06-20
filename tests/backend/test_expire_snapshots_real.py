"""TDD tests for expire_snapshots real-expiry delegation.

Ensures expire_snapshots() delegates to cleanup_orphans_v2
with the correct arguments and maps the return dict correctly.
"""
import backend.maintenance.iceberg_maintenance as im


def test_expire_snapshots_delegates_to_cleanup(monkeypatch):
    calls = {}

    def fake_cleanup(table_name, **kw):
        calls["table"] = table_name
        calls["kw"] = kw
        return {"expired_snapshots": 7, "verified": True}

    monkeypatch.setattr(im, "cleanup_orphans_v2", fake_cleanup)
    res = im.expire_snapshots("algo.events", keep=5)
    assert calls["table"] == "algo.events"
    assert calls["kw"]["retain_snapshots"] == 5
    assert calls["kw"]["skip_backup"] is True
    assert res["expired"] == 7


def test_expire_snapshots_returns_required_keys(monkeypatch):
    """Result dict must include table, expired, verified."""

    def fake_cleanup(table_name, **kw):
        return {"expired_snapshots": 3, "verified": True}

    monkeypatch.setattr(im, "cleanup_orphans_v2", fake_cleanup)
    res = im.expire_snapshots("stocks.ohlcv")
    assert res["table"] == "stocks.ohlcv"
    assert res["expired"] == 3
    assert res["verified"] is True


def test_expire_snapshots_zero_expired(monkeypatch):
    """expired maps to 0 when cleanup returns 0 expired_snapshots."""

    def fake_cleanup(table_name, **kw):
        return {"expired_snapshots": 0, "verified": True}

    monkeypatch.setattr(im, "cleanup_orphans_v2", fake_cleanup)
    res = im.expire_snapshots("stocks.ohlcv", keep=10)
    assert res["expired"] == 0


def test_expire_snapshots_uses_default_keep(monkeypatch):
    """Default keep must equal SNAPSHOT_KEEP constant."""
    calls = {}

    def fake_cleanup(table_name, **kw):
        calls["kw"] = kw
        return {"expired_snapshots": 0, "verified": True}

    monkeypatch.setattr(im, "cleanup_orphans_v2", fake_cleanup)
    im.expire_snapshots("stocks.analysis_summary")
    assert calls["kw"]["retain_snapshots"] == im.SNAPSHOT_KEEP


def test_expire_snapshots_missing_verified_defaults_true(monkeypatch):
    """verified defaults to True when absent from cleanup result."""

    def fake_cleanup(table_name, **kw):
        return {"expired_snapshots": 2}

    monkeypatch.setattr(im, "cleanup_orphans_v2", fake_cleanup)
    res = im.expire_snapshots("stocks.ohlcv")
    assert res["verified"] is True
