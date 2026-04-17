from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx
from pydantic import BaseModel


@dataclass(slots=True)
class ProviderResult:
    provider: str
    output: dict[str, Any]
    usage: dict[str, int] | None = None
    request_id: str | None = None
    input_token_estimate: int | None = None


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

    @staticmethod
    def _build_strict_json_schema(response_model: type[BaseModel]) -> dict[str, Any]:
        schema = deepcopy(response_model.model_json_schema())

        def normalize(node: Any) -> Any:
            if isinstance(node, dict):
                node.pop("default", None)
                node.pop("examples", None)

                for key in ("properties", "$defs"):
                    child = node.get(key)
                    if isinstance(child, dict):
                        for nested_key, nested_value in list(child.items()):
                            child[nested_key] = normalize(nested_value)

                for key in ("items", "additionalProperties", "contains", "if", "then", "else", "not"):
                    if key in node:
                        node[key] = normalize(node[key])

                for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
                    child = node.get(key)
                    if isinstance(child, list):
                        node[key] = [normalize(item) for item in child]

                properties = node.get("properties")
                if isinstance(properties, dict):
                    node["required"] = list(properties.keys())
                    node["additionalProperties"] = False
                elif node.get("type") == "object" and "additionalProperties" not in node:
                    node["additionalProperties"] = False
            elif isinstance(node, list):
                return [normalize(item) for item in node]
            return node

        return cast(dict[str, Any], normalize(schema))

    def generate(
        self,
        role: str,
        payload: dict[str, Any],
        *,
        response_model: type[BaseModel],
        instructions: str,
    ) -> ProviderResult:
        schema = self._build_strict_json_schema(response_model)
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
        input_token_estimate = max(1, int(round(len(json.dumps(request_body, ensure_ascii=False)) / 4)))

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
            input_token_estimate=input_token_estimate,
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
