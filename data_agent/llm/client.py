"""OpenAI-compatible JSON client with bounded repair and no external dependency."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Type

from .schemas import LLMGenerationResult


Transport = Callable[[str, Dict[str, Any], Dict[str, str], float], Dict[str, Any]]


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_seconds: float = 60.0
    allow_json_repair: bool = True

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        **overrides: Any,
    ) -> "LLMConfig":
        values = os.environ if environ is None else environ
        model = values.get("LLM_MODEL", "").strip()
        if not model:
            raise ValueError("LLM_MODEL is required")
        return cls(
            base_url=values.get("LLM_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/"),
            api_key=values.get("LLM_API_KEY", "EMPTY"),
            model=model,
            **overrides,
        )


def _http_transport(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    hostname = urllib.parse.urlsplit(url).hostname
    if hostname in {"127.0.0.1", "localhost", "::1"}:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        response_context = opener.open(request, timeout=timeout_seconds)
    else:
        response_context = urllib.request.urlopen(request, timeout=timeout_seconds)
    with response_context as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("LLM API response must be an object")
    return decoded


def _strip_json_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


class LLMClient:
    def __init__(
        self,
        config: LLMConfig,
        transport: Optional[Transport] = None,
    ) -> None:
        self.config = config
        self.model = config.model
        self._transport = transport or _http_transport

    @classmethod
    def from_env(
        cls,
        *,
        environ: Optional[Mapping[str, str]] = None,
        transport: Optional[Transport] = None,
        **config_overrides: Any,
    ) -> "LLMClient":
        return cls(
            LLMConfig.from_env(environ=environ, **config_overrides),
            transport=transport,
        )

    def _request(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return self._transport(
            self.config.base_url + "/chat/completions",
            payload,
            headers,
            self.config.timeout_seconds,
        )

    def generate_json(
        self,
        messages: list[dict[str, str]],
        response_type: Optional[Type[Any]] = None,
    ) -> LLMGenerationResult:
        started = time.perf_counter()
        active_messages = list(messages)
        max_attempts = 2 if self.config.allow_json_repair else 1
        last_content: Optional[str] = None
        prompt_tokens = 0
        completion_tokens = 0

        for attempt in range(1, max_attempts + 1):
            try:
                response = self._request(active_messages)
                choices = response.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise ValueError("LLM API response has no choices")
                message = choices[0].get("message", {})
                content = message.get("content")
                if not isinstance(content, str):
                    raise ValueError("LLM API response has no text content")
                last_content = content
                usage = response.get("usage") or {}
                prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as error:
                return LLMGenerationResult(
                    ok=False,
                    error_type="LLM_API_ERROR",
                    error=str(error),
                    retryable=True,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attempts=attempt,
                )

            try:
                parsed = json.loads(_strip_json_fence(last_content))
                if not isinstance(parsed, dict):
                    raise ValueError("model JSON response must be an object")
            except (json.JSONDecodeError, ValueError) as error:
                if attempt < max_attempts:
                    active_messages = active_messages + [
                        {"role": "assistant", "content": last_content},
                        {
                            "role": "user",
                            "content": "Return the same answer as one valid JSON object only.",
                        },
                    ]
                    continue
                return LLMGenerationResult(
                    ok=False,
                    raw_text=last_content,
                    error_type="JSON_PARSE_ERROR",
                    error=str(error),
                    retryable=False,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attempts=attempt,
                )

            if response_type is not None:
                try:
                    parsed = response_type.from_dict(parsed).to_dict()
                except (AttributeError, TypeError, ValueError) as error:
                    return LLMGenerationResult(
                        ok=False,
                        raw_text=last_content,
                        error_type="JSON_SCHEMA_ERROR",
                        error=str(error),
                        retryable=False,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        latency_ms=(time.perf_counter() - started) * 1000,
                        attempts=attempt,
                    )
            return LLMGenerationResult(
                ok=True,
                data=parsed,
                raw_text=last_content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=(time.perf_counter() - started) * 1000,
                attempts=attempt,
            )

        raise AssertionError("unreachable")
