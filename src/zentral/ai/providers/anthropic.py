"""Claude via the Anthropic API — for evaluation and testing.

This is a *cloud* provider: redacted ticket text leaves your infrastructure.
Use it to validate the pipeline, then switch to ``openai_compatible`` with an
on-prem model for production data (see docs/runbooks/switch-to-on-prem-ai.md).

Requires the optional dependency: ``pip install 'zentral[anthropic]'``.
Credentials are resolved by the SDK (``ANTHROPIC_API_KEY`` etc.) unless
``ZENTRAL_AI_API_KEY`` is set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import httpx
from pydantic import ValidationError

from zentral.ai.base import (
    AIError,
    AIProvider,
    Classification,
    ClassificationInput,
    system_prompt,
    user_prompt,
)
from zentral.config import Settings

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

DEFAULT_MODEL = "claude-haiku-5-5"


class AnthropicProvider(AIProvider):
    name: ClassVar[str] = "anthropic"
    on_prem: ClassVar[bool] = False

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        client: AsyncAnthropic | None = None,
    ) -> None:
        super().__init__(settings, http)
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - depends on installation
            raise AIError("install 'zentral[anthropic]' to use the anthropic provider") from exc
        self._model = settings.ai_model or DEFAULT_MODEL
        self._client: AsyncAnthropic = client or AsyncAnthropic(
            api_key=settings.ai_api_key.get_secret_value() or None,
            timeout=settings.ai_timeout_seconds,
            max_retries=1,
        )

    @property
    def model_label(self) -> str:
        return f"anthropic:{self._model}"

    async def classify(self, inp: ClassificationInput) -> Classification:
        import anthropic

        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=system_prompt(inp.categories, inp.systems),
                messages=[{"role": "user", "content": user_prompt(inp)}],
                output_format=Classification,
                output_config={"effort": "low"},
            )
        except anthropic.RateLimitError as exc:
            raise AIError("Anthropic API rate limited") from exc
        except anthropic.APIStatusError as exc:
            raise AIError(f"Anthropic API returned {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIError("Anthropic API unreachable") from exc
        except (ValidationError, ValueError) as exc:
            # The SDK parses the answer eagerly; a refusal or truncated output
            # (empty or partial JSON) surfaces here.
            raise AIError("Anthropic API returned an unusable classification") from exc
        if response.stop_reason != "end_turn" or response.parsed_output is None:
            # Covers refusals and truncated output.
            raise AIError(f"no classification produced (stop_reason={response.stop_reason})")
        return response.parsed_output
