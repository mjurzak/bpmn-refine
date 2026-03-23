# llm integration

## client layer

All Anthropic SDK usage is confined to `backend/app/llm/client.py`. No other module imports `anthropic` directly. This makes it straightforward to swap providers or add caching/retry logic in one place.

```python
from app.llm.client import complete, complete_with_history
```

`complete(prompt, system, model, max_tokens)` — single-turn
`complete_with_history(messages, system, model, max_tokens)` — multi-turn (full history passed each call)

## model routing

`backend/app/llm/router.py` defines `TaskType` and `resolve_model()`.

| TaskType | Tier | Default model |
|---|---|---|
| `SEMANTIC_VALIDATION` | strong | `claude-opus-4-6` |
| `REPAIR` | strong | `claude-opus-4-6` |
| `REFINEMENT` | strong | `claude-opus-4-6` |
| `IR_CONVERSION` | fast | `claude-haiku-4-5-20251001` |
| `SUMMARY` | fast | `claude-haiku-4-5-20251001` |
| `SIMPLE_QUERY` | fast | `claude-haiku-4-5-20251001` |

Models are overridable via `LLM_STRONG_MODEL` and `LLM_FAST_MODEL` in `.env`.

## prompts

Prompt text lives in `backend/app/llm/prompts/` as plain `.txt` files — never inline in source code.

| File | Used by | Purpose |
|---|---|---|
| `validate.txt` | `/validate` endpoint | system prompt for semantic issue detection |
| `repair.txt` | repair flow (future) | system prompt for producing a repaired IR |
| `chat_system.txt` | `/chat` endpoint | system prompt for conversational refinement |

Prompts are read at request time (`Path.read_text()`), so they can be edited without restarting the server.

### prompt versioning

Prompts are versioned by the git history of their `.txt` files. If you need multiple active versions simultaneously (e.g. for A/B evaluation), name them `validate_v2.txt` and select via config rather than overwriting the existing file.

## diagram updates from chat

The `/chat` endpoint scans the LLM reply for a ` ```diagram ... ``` ` code fence. If found, the content is parsed as `BpmnDiagram` JSON and returned in `updated_diagram`. The frontend only applies it when the user accepts the suggestion — no silent mutations.
