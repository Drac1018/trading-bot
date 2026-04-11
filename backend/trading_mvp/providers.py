from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel


@dataclass(slots=True)
class ProviderResult:
    provider: str
    output: dict[str, Any]
    usage: dict[str, int] | None = None
    request_id: str | None = None


class StructuredModelProvider(Protocol):
    name: str

    def generate(
        self,
        role: str,
        payload: dict[str, Any],
        *,
        response_model: type[BaseModel],
        instructions: str,
    ) -> ProviderResult:
        ...

    def test_connection(self) -> dict[str, Any]:
        ...


class DeterministicMockProvider:
    name = "deterministic-mock"

    def generate(
        self,
        role: str,
        payload: dict[str, Any],
        *,
        response_model: type[BaseModel],
        instructions: str,
    ) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            output={"role": role, "payload": payload, "instructions": instructions[:120]},
        )

    def test_connection(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name, "message": "Mock provider is active."}


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, temperature: float = 0.1) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.base_url = "https://api.openai.com/v1"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def generate(
        self,
        role: str,
        payload: dict[str, Any],
        *,
        response_model: type[BaseModel],
        instructions: str,
    ) -> ProviderResult:
        schema = response_model.model_json_schema()
        request_body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only valid JSON that strictly matches the provided schema. "
                        "Do not wrap JSON in markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "role": role,
                            "instructions": instructions,
                            "payload": payload,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
        }

        with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
            response = client.post("/chat/completions", headers=self._headers(), json=request_body)
            if not response.is_success:
                detail = response.text.strip()
                snippet = detail[:400] if detail else "no response body"
                raise httpx.HTTPStatusError(
                    f"{response.status_code} from OpenAI chat.completions: {snippet}",
                    request=response.request,
                    response=response,
                )
            payload_json = response.json()

        choice = payload_json["choices"][0]["message"]
        content = choice.get("content", "")
        if isinstance(content, list):
            text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
            content_text = "".join(text_parts)
        else:
            content_text = content

        parsed = json.loads(content_text)
        usage_block = payload_json.get("usage") or {}
        usage: dict[str, int] | None = None
        if usage_block:
            usage = {
                "prompt_tokens": int(usage_block.get("prompt_tokens", 0)),
                "completion_tokens": int(usage_block.get("completion_tokens", 0)),
                "total_tokens": int(usage_block.get("total_tokens", 0)),
            }

        return ProviderResult(
            provider=self.name,
            output=parsed,
            usage=usage,
            request_id=payload_json.get("id"),
        )

    def test_connection(self) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=15.0) as client:
            response = client.get(f"/models/{self.model}", headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        return {
            "ok": True,
            "provider": self.name,
            "message": "OpenAI model is reachable.",
            "model": payload.get("id", self.model),
        }


def build_model_provider(
    *,
    ai_provider: str,
    ai_enabled: bool,
    api_key: str,
    model: str,
    temperature: float,
) -> StructuredModelProvider:
    if not ai_enabled or ai_provider == "mock" or not api_key:
        return DeterministicMockProvider()
    return OpenAIProvider(api_key=api_key, model=model, temperature=temperature)
