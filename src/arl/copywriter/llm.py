from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from arl.config import LlmSettings
from arl.copywriter.models import LlmCopywritingResult


class LlmProvider(Protocol):
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> "LlmProviderResponse":
        ...


@dataclass(frozen=True)
class LlmProviderResponse:
    content: str
    token_usage: dict[str, int]


class LlmProviderError(RuntimeError):
    pass


class OpenAICompatibleProvider:
    def __init__(
        self,
        settings: LlmSettings,
        *,
        post: Callable[..., httpx.Response] | None = None,
    ) -> None:
        self.settings = settings
        self._post = post

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> LlmProviderResponse:
        if not self.settings.base_url:
            raise LlmProviderError("missing_base_url")
        if not self.settings.api_key:
            raise LlmProviderError("missing_api_key")

        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        try:
            if self._post is not None:
                response = self._post(
                    self._chat_completions_url(),
                    headers=headers,
                    json=payload,
                    timeout=self.settings.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.settings.timeout_seconds) as client:
                    response = client.post(
                        self._chat_completions_url(),
                        headers=headers,
                        json=payload,
                    )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LlmProviderError(exc.__class__.__name__) from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmProviderError("invalid_response_shape") from exc
        if not isinstance(content, str) or not content.strip():
            raise LlmProviderError("empty_response")

        usage_raw = data.get("usage") if isinstance(data, dict) else None
        token_usage = _token_usage(usage_raw)
        return LlmProviderResponse(content=content, token_usage=token_usage)

    def _chat_completions_url(self) -> str:
        base = self.settings.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"


def parse_llm_copywriting_result(content: str) -> LlmCopywritingResult:
    try:
        payload = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError as exc:
        raise LlmProviderError("invalid_json") from exc
    try:
        return LlmCopywritingResult.model_validate(payload)
    except ValidationError as exc:
        raise LlmProviderError("invalid_schema") from exc


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _token_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        raw = value.get(key)
        if isinstance(raw, int):
            result[key] = raw
    return result
