"""On-premises models via the OpenAI-compatible chat completions protocol.

Ollama, vLLM, llama.cpp server, LM Studio, LocalAI and TGI all expose
``POST {base_url}/chat/completions``, so one provider covers every common way
of hosting an open-weights model on your own hardware:

    ZENTRAL_AI_PROVIDER=openai_compatible
    ZENTRAL_AI_BASE_URL=http://ollama.internal:11434/v1
    ZENTRAL_AI_MODEL=llama3.1:8b

This provider is treated as on-prem: point it only at infrastructure you operate.
"""

from __future__ import annotations

import json
from typing import ClassVar

import httpx
from pydantic import ValidationError

from zentral.ai.base import (
    CLASSIFICATION_SCHEMA,
    AIError,
    AIProvider,
    Classification,
    ClassificationInput,
    system_prompt,
    user_prompt,
)


class OpenAICompatibleProvider(AIProvider):
    name: ClassVar[str] = "openai_compatible"
    on_prem: ClassVar[bool] = True

    async def classify(self, inp: ClassificationInput) -> Classification:
        base = self.settings.ai_base_url.rstrip("/")
        if not base or not self.settings.ai_model:
            raise AIError("ZENTRAL_AI_BASE_URL and ZENTRAL_AI_MODEL are required")
        headers = {}
        if key := self.settings.ai_api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": self.settings.ai_model,
            "temperature": 0,
            "max_tokens": 512,
            "messages": [
                {"role": "system", "content": system_prompt(inp.categories)},
                {"role": "user", "content": user_prompt(inp)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "classification",
                    "strict": True,
                    "schema": CLASSIFICATION_SCHEMA,
                },
            },
        }
        try:
            resp = await self.http.post(
                f"{base}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.settings.ai_timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise AIError(f"model endpoint unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise AIError(f"model endpoint returned {resp.status_code}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            return Classification.model_validate(json.loads(_strip_fence(content)))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise AIError("model returned an invalid classification") from exc


def _strip_fence(content: str) -> str:
    """Small local models sometimes wrap JSON in a Markdown fence despite the schema."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return text
