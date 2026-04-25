"""Tests for keyring integration and secret store functionality."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from maxwell_daemon.config import load_config
from maxwell_daemon.secrets import KeyringSecretStore


class InMemorySecretStore:
    """Minimal test double for secret storage."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class TestConfigLoadWithSecretStore:
    """Tests for config loading with and without secret stores."""

    def test_load_with_secret_store_none_and_plaintext_key_succeeds(
        self, tmp_path: Path
    ) -> None:
        """Load config with plaintext api_key when secret_store=None should work."""
        path = tmp_path / "c.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "backends": {
                        "claude": {
                            "type": "claude",
                            "model": "claude-sonnet-4-6",
                            "api_key": "sk-plaintext-123",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(path, secret_store=None)

        assert cfg.backends["claude"].api_key_value() == "sk-plaintext-123"

    def test_load_with_secret_store_none_and_secret_ref_key_raises(
        self, tmp_path: Path
    ) -> None:
        """Load config with secret_ref but no secret_store should raise RuntimeError."""
        path = tmp_path / "c.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "backends": {
                        "claude": {
                            "type": "claude",
                            "model": "claude-sonnet-4-6",
                            "api_key_secret_ref": "maxwell-daemon/backends/claude/api_key",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(
            RuntimeError,
            match="backend 'claude' uses api_key_secret_ref but no secret store is available",
        ):
            load_config(path, secret_store=None)

    def test_load_resolves_secret_ref_when_secret_missing_raises(
        self, tmp_path: Path
    ) -> None:
        """Load config with secret_ref that doesn't exist in store should raise."""
        path = tmp_path / "c.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "backends": {
                        "claude": {
                            "type": "claude",
                            "model": "claude-sonnet-4-6",
                            "api_key_secret_ref": "maxwell-daemon/backends/claude/api_key",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        store = InMemorySecretStore()

        with pytest.raises(
            RuntimeError,
            match="backend 'claude' secret_ref 'maxwell-daemon/backends/claude/api_key' was not found",
        ):
            load_config(path, secret_store=store)


class TestKeyringSecretStore:
    """Tests for KeyringSecretStore implementation."""

    def test_keyring_secret_store_with_mock_keyring(self) -> None:
        """KeyringSecretStore should work with mocked keyring module."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "secret-value"
        mock_keyring.set_password.return_value = None
        mock_keyring.delete_password.return_value = None

        store = KeyringSecretStore(keyring_module=mock_keyring)

        store.set("test-key", "test-secret")
        mock_keyring.set_password.assert_called_once_with(
            "maxwell-daemon", "test-key", "test-secret"
        )

        result = store.get("test-key")
        assert result == "secret-value"
        mock_keyring.get_password.assert_called_once_with("maxwell-daemon", "test-key")

    def test_keyring_secret_store_get_returns_none_when_secret_missing(self) -> None:
        """KeyringSecretStore.get() should return None when secret doesn't exist."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None

        store = KeyringSecretStore(keyring_module=mock_keyring)

        result = store.get("nonexistent-key")
        assert result is None

    def test_keyring_secret_store_delete(self) -> None:
        """KeyringSecretStore.delete() should call keyring.delete_password."""
        mock_keyring = MagicMock()
        mock_keyring.delete_password.return_value = None

        store = KeyringSecretStore(keyring_module=mock_keyring)

        store.delete("test-key")
        mock_keyring.delete_password.assert_called_once_with(
            "maxwell-daemon", "test-key"
        )

    def test_keyring_secret_store_raises_when_keyring_unavailable(self) -> None:
        """KeyringSecretStore.__init__() should raise RuntimeError when keyring unavailable."""
        with pytest.raises(
            RuntimeError,
            match="keyring is required for OS-backed secret storage",
        ):
            KeyringSecretStore(keyring_module=None)


class TestKeyringFallback:
    """Tests for fallback behavior when keyring is unavailable."""

    def test_load_config_handles_missing_keyring_gracefully(self, tmp_path: Path) -> None:
        """load_config should handle missing keyring and use None as secret_store."""
        path = tmp_path / "c.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "backends": {
                        "claude": {
                            "type": "claude",
                            "model": "claude-sonnet-4-6",
                            "api_key": "sk-plaintext-123",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(path, secret_store=None)
        assert cfg.backends["claude"].api_key_value() == "sk-plaintext-123"
