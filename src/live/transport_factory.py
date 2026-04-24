from __future__ import annotations

from typing import TYPE_CHECKING

from model_providers import get_live_provider_config
from config import Config

if TYPE_CHECKING:
    from .transports import BaseLiveTransport


def resolve_transport_cls(provider_id: str, mode_kind: str) -> type[BaseLiveTransport]:
    from .transports import (
        BackendGeminiLiveTransport,
        DirectGeminiLiveTransport,
        LiteLLMRequestLiveTransport,
        OllamaLocalLiveTransport,
        OpenAIRealtimeTransport,
    )

    _REGISTRY: dict[tuple[str, str], type[BaseLiveTransport]] = {
        ("ollama", "realtime"): OllamaLocalLiveTransport,
        ("openai", "realtime"): OpenAIRealtimeTransport,
    }

    if (provider_id, mode_kind) == ("gemini", "realtime"):
        return DirectGeminiLiveTransport if Config.USE_DIRECT_API else BackendGeminiLiveTransport

    return _REGISTRY.get((provider_id, mode_kind), LiteLLMRequestLiveTransport)
