# Add an AI provider

Most on-prem model servers already work with the built-in `openai_compatible`
provider. Write your own only if your model is reached through a different
protocol.

## The contract

```python
from typing import ClassVar
from zentral.ai.base import (
    AIError, AIProvider, Classification, ClassificationInput, system_prompt, user_prompt,
)


class MyProvider(AIProvider):
    name: ClassVar[str] = "myprovider"
    on_prem: ClassVar[bool] = True      # True only if data stays on infrastructure you control

    async def classify(self, inp: ClassificationInput) -> Classification:
        try:
            raw = await self._call_model(
                system=system_prompt(inp.categories, inp.systems),
                user=user_prompt(inp),
            )
            return Classification.model_validate(raw)
        except Exception as exc:
            raise AIError("model call failed") from exc
```

```toml
[project.entry-points."zentral.ai_providers"]
myprovider = "my_package:MyProvider"
```

```bash
ZENTRAL_AI_PROVIDER=myprovider
```

## What you get for free

You do not need to implement any of the following — the classification service
does it around every provider:

- masking of personal data **before** `classify` is called,
- a timeout (`ZENTRAL_AI_TIMEOUT_SECONDS`),
- validation of the result against the category list and systems catalogue,
- the confidence threshold,
- fallback to the rules provider when you raise `AIError`,
- the audit event.

## Rules

- Raise **`AIError`** for every failure. Do not return a made-up result.
- Use `system_prompt()` and `user_prompt()` unchanged where possible; they
  carry the untrusted-content framing and the allow-lists.
- Request structured output (`CLASSIFICATION_SCHEMA`) if your server supports
  it.
- Do not give the model tools.
- Set `on_prem` honestly. It is recorded in the audit trail and shown by
  `zentral doctor`. If your provider sends data to a third party, also add its
  name to `CLOUD_AI_PROVIDERS` handling in your deployment policy — production
  refuses to disable masking for the built-in cloud providers.
- Use `self.http` (shared client with timeouts).

## Verify

`zentral doctor` performs a live classification with the configured provider.
`tests/test_ai.py` shows how to test request shape and failure handling against
a stubbed endpoint.
