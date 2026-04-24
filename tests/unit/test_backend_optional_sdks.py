from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from maxwell_daemon.backends.base import BackendUnavailableError, Message, MessageRole
from maxwell_daemon.backends.gemini import GeminiBackend
from maxwell_daemon.backends.groq import GroqBackend
from maxwell_daemon.backends.mistral import MistralBackend


def _messages() -> list[Message]:
    return [
        Message(role=MessageRole.SYSTEM, content="system instruction"),
        Message(role=MessageRole.USER, content="hello"),
    ]


def test_gemini_backend_reports_missing_sdk() -> None:
    with (
        patch(
            "maxwell_daemon.backends.gemini.import_module",
            side_effect=ModuleNotFoundError("google.generativeai"),
        ),
        pytest.raises(BackendUnavailableError, match="google-generativeai SDK not installed"),
    ):
        GeminiBackend(api_key="test-key")


def test_gemini_backend_success_paths() -> None:
    class FakeGenerativeModel:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def generate_content_async(
            self, contents: object, stream: bool = False, **kwargs: object
        ) -> object:
            if stream:

                async def _generator() -> object:
                    yield SimpleNamespace(text="alpha")
                    yield SimpleNamespace(text="")
                    yield SimpleNamespace(text="beta")

                return _generator()
            return SimpleNamespace(
                text="done",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=11,
                    candidates_token_count=4,
                ),
            )

    class FakeGeminiSdk:
        def __init__(self) -> None:
            self.configured_key: str | None = None
            self.types = SimpleNamespace(
                GenerationConfig=lambda **kwargs: SimpleNamespace(**kwargs)
            )
            self.GenerativeModel = FakeGenerativeModel

        def configure(self, *, api_key: str) -> None:
            self.configured_key = api_key

        def list_models(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="models/gemini-2.5-pro")]

    sdk = FakeGeminiSdk()

    async def _exercise() -> None:
        backend = GeminiBackend(api_key="test-key")
        response = await backend.complete(_messages(), model="gemini-2.5-pro", max_tokens=32)
        streamed = [chunk async for chunk in backend.stream(_messages(), model="gemini-2.5-pro")]
        assert response.content == "done"
        assert response.usage.prompt_tokens == 11
        assert streamed == ["alpha", "beta"]
        assert await backend.health_check() is True
        assert await backend.list_models() == ["models/gemini-2.5-pro"]
        assert sdk.configured_key == "test-key"

    with patch("maxwell_daemon.backends.gemini.import_module", return_value=sdk):
        asyncio.run(_exercise())


def test_groq_backend_reports_missing_sdk() -> None:
    with (
        patch(
            "maxwell_daemon.backends.groq.import_module",
            side_effect=ModuleNotFoundError("groq"),
        ),
        pytest.raises(BackendUnavailableError, match="groq SDK not installed"),
    ):
        GroqBackend(api_key="test-key")


def test_groq_backend_retries_retryable_errors() -> None:
    class FakeApiConnectionError(Exception):
        pass

    class FakeRateLimitError(Exception):
        pass

    class FakeResponse:
        model = "llama-3.3-70b-versatile"

        def __init__(self) -> None:
            self.choices = [
                SimpleNamespace(
                    message=SimpleNamespace(content="retried"),
                    finish_reason="stop",
                )
            ]
            self.usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10)

        def model_dump(self) -> dict[str, str]:
            return {"ok": "true"}

    class FakeCompletions:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs: object) -> FakeResponse:
            self.calls += 1
            if self.calls == 1:
                raise FakeApiConnectionError("retry me")
            return FakeResponse()

    class FakeAsyncGroq:
        def __init__(self, *, api_key: str, timeout: float) -> None:
            self.api_key = api_key
            self.timeout = timeout
            self.completions = FakeCompletions()
            self.chat = SimpleNamespace(completions=self.completions)

    groq_sdk = SimpleNamespace(
        AsyncGroq=FakeAsyncGroq,
        APIConnectionError=FakeApiConnectionError,
        RateLimitError=FakeRateLimitError,
    )

    async def _exercise() -> None:
        backend = GroqBackend(api_key="test-key")
        response = await backend.complete(
            [Message(role=MessageRole.USER, content="hello")],
            model="llama-3.3-70b-versatile",
            tools=[{"name": "tool"}],
        )
        assert response.content == "retried"
        assert response.usage.total_tokens == 10
        assert backend._client.completions.calls == 2

    with (
        patch("maxwell_daemon.backends.groq.import_module", return_value=groq_sdk),
        patch("maxwell_daemon.backends.groq.asyncio.sleep", new=AsyncMock()) as sleep_mock,
    ):
        asyncio.run(_exercise())
        sleep_mock.assert_awaited_once_with(1.0)


def test_mistral_backend_reports_missing_sdk() -> None:
    with (
        patch(
            "maxwell_daemon.backends.mistral.import_module",
            side_effect=ModuleNotFoundError("mistralai"),
        ),
        pytest.raises(BackendUnavailableError, match="mistralai SDK not installed"),
    ):
        MistralBackend(api_key="test-key")


def test_mistral_backend_success_paths() -> None:
    class FakeChat:
        async def complete_async(self, **kwargs: object) -> object:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="done"),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
                model="mistral-small-latest",
            )

        async def stream_async(self, **kwargs: object) -> object:
            async def _generator() -> object:
                yield SimpleNamespace(
                    data=SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="one"))]
                    )
                )
                yield SimpleNamespace(
                    data=SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="two"))]
                    )
                )

            return _generator()

    class FakeModels:
        async def list_async(self) -> object:
            return SimpleNamespace(data=[SimpleNamespace(id="mistral-small-latest")])

    class FakeMistralClient:
        def __init__(self, *, api_key: str, timeout_ms: int) -> None:
            self.api_key = api_key
            self.timeout_ms = timeout_ms
            self.chat = FakeChat()
            self.models = FakeModels()

    mistral_sdk = SimpleNamespace(Mistral=FakeMistralClient)

    async def _exercise() -> None:
        backend = MistralBackend(api_key="test-key")
        response = await backend.complete(
            [Message(role=MessageRole.USER, content="hello")],
            model="mistral-small-latest",
        )
        streamed = [
            chunk
            async for chunk in backend.stream(
                [Message(role=MessageRole.USER, content="hello")],
                model="mistral-small-latest",
            )
        ]
        assert response.content == "done"
        assert streamed == ["one", "two"]
        assert await backend.health_check() is True
        assert await backend.list_models() == ["mistral-small-latest"]

    with patch("maxwell_daemon.backends.mistral.import_module", return_value=mistral_sdk):
        asyncio.run(_exercise())
