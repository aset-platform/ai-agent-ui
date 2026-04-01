"""Tests for :mod:`ollama_manager`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError as ReqConnErr


# ── Fixtures ────────────────────────────────────────────


class _FakeSettings:
    ollama_enabled = True
    ollama_base_url = "http://localhost:11434"
    ollama_model = "gpt-oss:20b"
    ollama_num_ctx = 16384
    ollama_timeout = 120
    ollama_health_cache_ttl = 0  # disable cache in tests


@pytest.fixture()
def mgr():
    """Fresh OllamaManager with mocked config."""
    with patch(
        "ollama_manager.get_settings",
        return_value=_FakeSettings(),
    ):
        from ollama_manager import OllamaManager

        yield OllamaManager()


# ── is_available ────────────────────────────────────────


def test_is_available_when_server_up(mgr):
    resp = MagicMock()
    resp.ok = True
    resp_ps = MagicMock()
    resp_ps.json.return_value = {"models": []}

    with patch(
        "ollama_manager.requests.get",
        side_effect=[resp, resp_ps],
    ):
        assert mgr.is_available() is True


def test_is_available_when_server_down(mgr):
    with patch(
        "ollama_manager.requests.get",
        side_effect=ReqConnErr,
    ):
        assert mgr.is_available() is False


# ── is_model_loaded ─────────────────────────────────────


def test_is_model_loaded_found(mgr):
    resp_ok = MagicMock(ok=True)
    resp_ps = MagicMock()
    resp_ps.json.return_value = {
        "models": [
            {
                "name": "gpt-oss:20b",
                "size_vram": 13_000_000_000,
            },
        ],
    }

    with patch(
        "ollama_manager.requests.get",
        side_effect=[resp_ok, resp_ps],
    ):
        assert mgr.is_model_loaded("gpt-oss:20b")


def test_is_model_loaded_not_found(mgr):
    resp_ok = MagicMock(ok=True)
    resp_ps = MagicMock()
    resp_ps.json.return_value = {"models": []}

    with patch(
        "ollama_manager.requests.get",
        side_effect=[resp_ok, resp_ps],
    ):
        assert not mgr.is_model_loaded("gpt-oss:20b")


# ── get_status ──────────────────────────────────────────


def test_get_status_available(mgr):
    resp_ok = MagicMock(ok=True)
    resp_ps = MagicMock()
    resp_ps.json.return_value = {
        "models": [
            {
                "name": "gpt-oss:20b",
                "size_vram": 13_421_772_800,
            },
        ],
    }

    with patch(
        "ollama_manager.requests.get",
        return_value=resp_ok,
    ):
        # First call: is_available probe
        # get_status also calls GET /api/ps directly
        with patch(
            "ollama_manager.requests.get",
            side_effect=[resp_ok, resp_ps, resp_ps],
        ):
            status = mgr.get_status()

    assert status["available"] is True
    assert len(status["models"]) == 1
    assert status["models"][0]["name"] == "gpt-oss:20b"
    assert status["models"][0]["ram_gb"] == 12.5


def test_get_status_unavailable(mgr):
    with patch(
        "ollama_manager.requests.get",
        side_effect=ReqConnErr,
    ):
        status = mgr.get_status()

    assert status == {"available": False, "models": []}


# ── load_profile ────────────────────────────────────────


def test_load_profile_reasoning(mgr):
    # unload_all: GET /api/ps → empty
    resp_ps_empty = MagicMock()
    resp_ps_empty.json.return_value = {"models": []}

    # load: POST /api/generate → ok
    resp_post = MagicMock()
    resp_post.raise_for_status = MagicMock()

    with patch(
        "ollama_manager.requests.get",
        return_value=resp_ps_empty,
    ), patch(
        "ollama_manager.requests.post",
        return_value=resp_post,
    ) as mock_post:
        result = mgr.load_profile("reasoning")

    assert result["loaded"] == "gpt-oss:20b"
    assert result["keep_alive"] == "1h"

    call_json = mock_post.call_args.kwargs["json"]
    assert call_json["model"] == "gpt-oss:20b"
    assert call_json["keep_alive"] == "1h"
    assert call_json["options"]["num_ctx"] == 8192


def test_load_profile_coding(mgr):
    resp_ps_empty = MagicMock()
    resp_ps_empty.json.return_value = {"models": []}
    resp_post = MagicMock()
    resp_post.raise_for_status = MagicMock()

    with patch(
        "ollama_manager.requests.get",
        return_value=resp_ps_empty,
    ), patch(
        "ollama_manager.requests.post",
        return_value=resp_post,
    ) as mock_post:
        result = mgr.load_profile("coding")

    assert result["loaded"] == "qwen2.5-coder:14b"
    call_json = mock_post.call_args.kwargs["json"]
    assert call_json["model"] == "qwen2.5-coder:14b"


def test_load_profile_invalid(mgr):
    with pytest.raises(ValueError, match="Unknown profile"):
        mgr.load_profile("unknown")


# ── unload_all ──────────────────────────────────────────


def test_unload_all_with_loaded_model(mgr):
    resp_ps = MagicMock()
    resp_ps.json.return_value = {
        "models": [
            {"name": "gpt-oss:20b"},
        ],
    }
    resp_post = MagicMock()

    with patch(
        "ollama_manager.requests.get",
        return_value=resp_ps,
    ), patch(
        "ollama_manager.requests.post",
        return_value=resp_post,
    ) as mock_post, patch(
        "ollama_manager.time.sleep",
    ):
        result = mgr.unload_all()

    assert result == {"unloaded": ["gpt-oss:20b"]}
    call_json = mock_post.call_args.kwargs["json"]
    assert call_json["model"] == "gpt-oss:20b"
    assert call_json["keep_alive"] == 0


def test_unload_all_nothing_loaded(mgr):
    resp_ps = MagicMock()
    resp_ps.json.return_value = {"models": []}

    with patch(
        "ollama_manager.requests.get",
        return_value=resp_ps,
    ):
        result = mgr.unload_all()

    assert result == {"unloaded": []}


# ── Graceful degradation ───────────────────────────────


def test_all_methods_safe_when_unreachable(mgr):
    with patch(
        "ollama_manager.requests.get",
        side_effect=ReqConnErr,
    ):
        assert mgr.is_available() is False
        assert mgr.is_model_loaded("gpt-oss:20b") is False
        assert mgr.get_status() == {
            "available": False,
            "models": [],
        }
