"""Tests for EmbeddingService."""

from unittest.mock import MagicMock, patch

from embedding_service import EmbeddingService


class TestEmbeddingService:
    """Verify embedding calls and fallback."""

    def _svc(self) -> EmbeddingService:
        return EmbeddingService(
            base_url="http://localhost:11434",
            model="nomic-embed-text",
            dim=768,
            ttl=0,
        )

    def test_embed_returns_vector(self):
        """Successful embed returns 768-dim list."""
        svc = self._svc()
        fake_vec = [0.1] * 768
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "embeddings": [fake_vec],
        }

        with patch(
            "embedding_service.requests.post",
            return_value=mock_resp,
        ):
            result = svc.embed("hello world")

        assert result is not None
        assert len(result) == 768

    def test_embed_returns_none_on_timeout(self):
        """Timeout returns None, no crash."""
        svc = self._svc()
        with patch(
            "embedding_service.requests.post",
            side_effect=Exception("timeout"),
        ):
            result = svc.embed("hello")

        assert result is None

    def test_embed_returns_none_on_empty(self):
        """Empty string returns None."""
        svc = self._svc()
        assert svc.embed("") is None
        assert svc.embed("   ") is None

    def test_embed_dim_mismatch(self):
        """Wrong dimension returns None."""
        svc = self._svc()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "embeddings": [[0.1] * 512],
        }

        with patch(
            "embedding_service.requests.post",
            return_value=mock_resp,
        ):
            result = svc.embed("test")

        assert result is None

    def test_is_available_caches(self):
        """Availability is cached for TTL."""
        svc = EmbeddingService(
            base_url="http://localhost:11434",
            model="nomic-embed-text",
            dim=768,
            ttl=60,
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "nomic-embed-text:latest"},
            ],
        }

        with patch(
            "embedding_service.requests.get",
            return_value=mock_resp,
        ) as mock_get:
            assert svc.is_available() is True
            assert svc.is_available() is True
            # Only one HTTP call due to caching
            assert mock_get.call_count == 1

    def test_embed_batch(self):
        """Batch embedding returns aligned list."""
        svc = self._svc()
        fake_vec = [0.1] * 768
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "embeddings": [fake_vec],
        }

        with patch(
            "embedding_service.requests.post",
            return_value=mock_resp,
        ):
            results = svc.embed_batch(
                ["a", "b", ""],
            )

        assert len(results) == 3
        assert results[0] is not None
        assert results[1] is not None
        assert results[2] is None  # empty string
