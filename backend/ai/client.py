"""NVIDIA NIM client for AI job matching.

Uses the OpenAI-compatible Python SDK pointed at NVIDIA's endpoint:
    https://integrate.api.nvidia.com/v1

Authentication is via the NVIDIA_API_KEY environment variable. The model is
configurable through NVIDIA_MODEL (default verified from the NVIDIA API Catalog).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from openai import OpenAI
from openai import APIConnectionError, APIStatusError, RateLimitError

log = logging.getLogger("ai.client")

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Verified from the NVIDIA API Catalog (integrate.api.nvidia.com/v1/models).
# NVIDIA_MODEL is required; no model ID is substituted without configuration.
DEFAULT_MODEL = ""


class NVIDIAClient:
    """Thin wrapper around the OpenAI SDK configured for NVIDIA NIM."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
    ):
        self.api_key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        self.base_url = base_url or os.environ.get("NVIDIA_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.environ.get("NVIDIA_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self._client: Optional[OpenAI] = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        return self._client

    def chat_completion(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
        response_format: Optional[dict] = None,
    ) -> str:
        """Send a streaming chat completion and return the raw text content."""
        if not self.configured:
            raise RuntimeError("NVIDIA_API_KEY is not set")
        client = self._get_client()
        kwargs: dict = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        try:
            stream = client.chat.completions.create(**kwargs)
        except RateLimitError as exc:
            raise RuntimeError(f"NVIDIA rate limit exceeded: {exc}") from exc
        except APIConnectionError as exc:
            raise RuntimeError(f"NVIDIA connection error: {exc}") from exc
        except APIStatusError as exc:
            raise RuntimeError(f"NVIDIA API error (HTTP {exc.status_code}): {exc}") from exc

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
            if delta.content is not None:
                content_parts.append(delta.content)

        content = "".join(content_parts)
        if reasoning_parts and not content:
            content = "".join(reasoning_parts)
        return content or ""

    def test_connection(self) -> tuple[bool, str]:
        """Return (ok, message) after a minimal completion call."""
        if not self.configured:
            return False, "NVIDIA_API_KEY is not set"
        try:
            content = self.chat_completion(
                [{"role": "user", "content": "Reply with exactly: OK"}],
                temperature=0,
                max_tokens=10,
            )
            if content and "OK" in content:
                return True, f"NVIDIA reachable (model={self.model})"
            return True, f"NVIDIA reachable (model={self.model}); unexpected reply: {content!r}"
        except RuntimeError as exc:
            return False, str(exc)
