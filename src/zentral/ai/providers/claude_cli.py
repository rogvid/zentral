"""Claude via the local ``claude`` CLI — **local testing only**.

Lets a developer exercise the AI pipeline with their Claude subscription
instead of an API key. The CLI is run headless, with every tool disabled, a
fixed system prompt and a JSON schema; ticket text is passed on stdin and never
appears on a command line. Refused in production by ``Settings.validate_for_runtime``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import ClassVar

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

DEFAULT_MODEL = "claude-haiku-5-5"


class ClaudeCliProvider(AIProvider):
    name: ClassVar[str] = "claude_cli"
    on_prem: ClassVar[bool] = False

    @property
    def model_label(self) -> str:
        return f"claude_cli:{self.settings.ai_model or DEFAULT_MODEL}"

    async def classify(self, inp: ClassificationInput) -> Classification:
        executable = shutil.which("claude")
        if executable is None:
            raise AIError("the 'claude' CLI is not installed")
        try:
            proc = await asyncio.create_subprocess_exec(
                executable,
                "-p",
                "--model",
                self.settings.ai_model or DEFAULT_MODEL,
                "--output-format",
                "json",
                "--tools",
                "",
                "--no-session-persistence",
                "--system-prompt",
                system_prompt(inp.categories),
                "--json-schema",
                json.dumps(CLASSIFICATION_SCHEMA),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(user_prompt(inp).encode()),
                timeout=self.settings.ai_timeout_seconds,
            )
        except TimeoutError as exc:
            proc.kill()
            raise AIError("claude CLI timed out") from exc
        except OSError as exc:
            raise AIError("claude CLI could not be started") from exc
        try:
            result = json.loads(stdout)
            if result.get("is_error") or "structured_output" not in result:
                raise AIError("claude CLI returned no structured output")
            return Classification.model_validate(result["structured_output"])
        except (ValueError, ValidationError) as exc:
            raise AIError("claude CLI returned an invalid classification") from exc
