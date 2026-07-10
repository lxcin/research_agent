"""LLM abstraction layer with mock support for testing."""
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, messages: list[dict], **kwargs) -> str: ...


class LiteLLMProvider(LLMProvider):
    def __init__(self, model: str = "claude-3-haiku-20240307"):
        self.model = model

    def complete(self, messages: list[dict], **kwargs) -> str:
        import litellm
        resp = litellm.completion(model=self.model, messages=messages, **kwargs)
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