"""Remote memory manager contract tests."""

from __future__ import annotations

from maxwell_daemon.fleet.memory import RemoteMemoryManager
from maxwell_daemon.memory import MemoryManager


def test_remote_memory_manager_implements_memory_manager_contract() -> None:
    manager = RemoteMemoryManager("https://coordinator.example")

    assert isinstance(manager, MemoryManager)
