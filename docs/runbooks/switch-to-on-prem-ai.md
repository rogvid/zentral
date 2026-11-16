# Switch to on-prem AI

**Goal:** ticket classification by a model running on infrastructure you
control, so no ticket text leaves your network.
**Use when** moving from `rules` (no model) or from a cloud test provider
(`anthropic`, `claude_cli`) to production.

Zentral talks to any server that implements the OpenAI-compatible chat
completions API — Ollama, vLLM, llama.cpp server, LocalAI, TGI.

## 1. Run a model server

Pick what fits your operations. Two common choices:

=== "Ollama (simplest)"

    ```bash
    # Compose: included as an optional profile
    docker compose --profile ai up -d
    docker compose exec ollama ollama pull llama3.1:8b
    ```

    Endpoint inside the stack: `http://ollama:11434/v1`.

=== "vLLM (GPU, higher throughput)"

    ```bash
    docker run --gpus all -p 8001:8000 vllm/vllm-openai:latest \
      --model meta-llama/Llama-3.1-8B-Instruct --api-key "$MODEL_API_KEY"
    ```

    Endpoint: `http://<host>:8001/v1`.

Requirements for the server:

- reachable **only** from the Zentral worker (internal network / network
  policy) — it has no authentication of its own unless you set an API key;
- no outbound internet access after the model is downloaded;
- request logging disabled or treated as personal data.

## 2. Check the model can do the job

Before touching production, try a few representative (non-personal) examples:

```bash
curl -s http://<model-host>/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "llama3.1:8b", "temperature": 0,
  "messages": [{"role": "user", "content": "Reply with JSON {\"ok\": true}"}],
  "response_format": {"type": "json_object"}
}' | jq -r '.choices[0].message.content'
```

For a proper comparison, run the live test in the repository against your
endpoint with examples from your own domain:

```bash
ZENTRAL_LIVE_AI=1 ZENTRAL_LIVE_AI_PROVIDER=openai_compatible \
ZENTRAL_AI_BASE_URL=http://<model-host>/v1 ZENTRAL_LIVE_AI_MODEL=llama3.1:8b \
  uv run pytest tests/test_ai.py -m live_ai
```

## 3. Point Zentral at it

```bash
ZENTRAL_AI_PROVIDER=openai_compatible
ZENTRAL_AI_BASE_URL=http://ollama:11434/v1
ZENTRAL_AI_MODEL=llama3.1:8b
# ZENTRAL_AI_API_KEY=…            if your server requires one
# ZENTRAL_AI_TIMEOUT_SECONDS=60   CPU inference can be slow
```

Keep `ZENTRAL_AI_REDACT_PII=true`: masking costs nothing and limits what ends
up in the model server's memory and logs.

Restart the **worker** (the API server does not call the model).

## 4. Verify

```bash
zentral doctor
# ✓ AI provider: openai_compatible:llama3.1:8b (on-prem) → hardware
```

Then send a test ticket and check, as auditor, the newest `ticket.classified`
event:

```json
{"provider": "openai_compatible", "model": "openai_compatible:llama3.1:8b",
 "on_prem": true, "input_redacted": true, "fallback": false, "confidence": 0.86, …}
```

`on_prem: true` and `fallback: false` are what you are looking for.

## 5. Watch it for a week

- `ai_provider_failed` in the worker log → the model server is unreachable or
  too slow; tickets are being classified by keyword rules instead.
- Share of events with `fallback: true`:

    ```sql
    SELECT (details->>'fallback')::boolean AS fallback, count(*)
    FROM audit_events WHERE action = 'ticket.classified'
      AND occurred_at > now() - interval '7 days' GROUP BY 1;
    ```

- Ask agents how often they correct category or system. If suggestions are
  often wrong, add **descriptions and keywords** to your systems, raise
  `ZENTRAL_AI_MIN_CONFIDENCE` so fewer are auto-applied, or try a larger
  model.

## 6. Clean up after a cloud test phase

- Remove the cloud provider's API key from configuration and revoke it.
- Confirm in the audit trail that no `ticket.classified` event after the switch
  has `on_prem: false`.
- Check the provider's data-retention terms for the test period and record it
  in your processing documentation.

## Back out

Set `ZENTRAL_AI_PROVIDER=rules` and restart the worker. Nothing else depends
on the model: intake, routing rules and everything else keep working.
