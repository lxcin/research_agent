"""LLM abstraction layer with mock support for testing."""
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, messages: list[dict], **kwargs) -> str: ...


class LiteLLMProvider(LLMProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 api_base: str | None = None):
        from research_agent.config import get_model_config, get_api_key
        cfg = get_model_config()
        self.model = model or cfg.get("name", "openai/deepseek-chat")
        self.api_base = api_base or cfg.get("api_base")
        self.api_key = api_key or get_api_key()
        self._kwargs = {}
        if self.api_base:
            self._kwargs["api_base"] = self.api_base

    def complete(self, messages: list[dict], **kwargs) -> str:
        import litellm
        call_kwargs = {"model": self.model, "messages": messages, **self._kwargs, **kwargs}
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        resp = litellm.completion(**call_kwargs)
        return resp.choices[0].message.content


class MockLLMProvider(LLMProvider):
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.call_count = 0
        self.calls: list[dict] = []

    def complete(self, messages: list[dict], **kwargs) -> str:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        resp = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        return resp