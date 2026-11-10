# Otari issues found while building Otari Switch (drafts to file on mozilla-ai/otari)

Found on Nov 10, 2026 against a self-hosted gateway (mozilla-ai/otari `main` @ 6c54933, reports `0.0.0-dev`, Python
3.14, SQLite, llama.cpp b12246 `llama-server` providers) and hosted otari.ai. Each has steps to reproduce. File them
from your own GitHub account; attach the request id where one is given.

---

## 1. `/v1/responses` returns 502 for llama.cpp providers

**Steps**
1. Add a llama.cpp provider: `POST /api/v1/hosted-providers {"provider":"llamacpp","name":"llamacpp","api_base":"http://127.0.0.1:8081/v1"}` and price + enable its model.
2. `POST /api/v1/responses {"model":"llamacpp:qwen2.5-1.5b-instruct","input":"Hello"}` with an API key.

**Expected** an ordinary Responses object (the docs say Responses is translated for providers without one, and llama-server has one).

**Actual** `502 {"detail":"LLM provider error"}` with no `code`. Streaming sends text deltas, then `event: error` `{"error":{"message":"An error occurred during streaming","type":"server_error"}}`. The gateway log shows the native llama-server Responses usage block fails to parse (`cache_write_tokens` missing). Chat Completions and Messages work on the same model.

---

## 2. Catalog says local llama.cpp models cannot call tools or return structured output

**Steps** `GET /api/v1/catalog/models/alibaba/qwen2.5-1.5b-instruct` on a gateway serving that model through llama.cpp.

**Actual** `capabilities.tool_call: false`, `structured_output: false`, and `GET /api/v1/models` has no `supported_parameters` for it. Both work: `tools` + `tool_choice` returns `finish_reason: "tool_calls"`, and strict `json_schema` returns schema-valid JSON.

**Why it matters** tools that read the catalog (Otari Switch picks candidates by `supported_parameters`) wrongly rule these models out.

---

## 3. `/routing/policies/explain` names the provider type, not the instance

**Steps** two llama.cpp instances (`llamacpp`, `llamacpp-llama`); policy `{"select":[{"default":"llamacpp:qwen2.5-1.5b-instruct"}],"on_failure":["llamacpp-llama:llama-3.2-1b-instruct"]}`; `POST /api/v1/routing/policies/explain {"name":"switch-test"}`.

**Actual** the second candidate's `dispatch_model` is `llamacpp:llama-3.2-1b-instruct` (provider type) while `instance` is `llamacpp-llama`. Sending that `dispatch_model` as a model would reach the wrong instance.

---

## 4. 401 responses carry `Otari-Request-ID` although the docs say they don't

The API reference says a request rejected before the gateway handles it (a failed authentication 401) carries no `Otari-Request-ID`. Inference routes answer 401 with the header. Either the docs or the behaviour should change; clients that treat "has a request id" as "reached the gateway" are misled.

---

## 5. Request id formats differ between routes

`/mcp/servers` and `/mcp/execute` errors use `req_<hex>`; completions use a UUID. Harmless, but tools that validate or link request ids need two patterns. Worth one format or a note in the docs.

---

## 6. First start prints a usable `bootstrap` API key to stdout

A fresh self-hosted gateway creates a `bootstrap` API key with no budget and prints it in the log. Logs are often shipped to shared systems. Suggest printing only its id/prefix, or requiring an explicit flag.

---

## 7. Reasoning models return empty `content` when `max_tokens` is small (hosted otari.ai)

**Steps** `POST /api/v1/chat/completions` on otari.ai with `model: "alibaba/qwen3.6-27b"`, `max_tokens: 200`, `temperature: 0`, a short judging prompt (request id `162f80f1-48d2-4f8f-b689-b9f714ae7227`).

**Actual** `finish_reason: "length"`, `content: ""`, all 200 tokens in `reasoning_content`; billed as normal. Not a bug as such, but `GET /models` lists `reasoning` in `supported_parameters` for 77 of 165 models without saying which reason by default, so a caller cannot tell when it must raise `max_tokens` or send `reasoning_effort`. A `reasons_by_default` flag (models.dev has it) would help.

---

## 8. A slow model plus a client timeout leaves an `Idempotency-Key` "in progress" (hosted)

**Steps** call `moonshotai/kimi-k2.6` (20-35 s per answer) with an `Idempotency-Key` and a 60 s client timeout; let one call time out and retry with the same key.

**Actual** `409` "A request with this Idempotency-Key is still in progress" (documented, request ids `1fa67295-afbe-4468-95e5-383b9968d0a6`, `9a2e9499-824a-4f0b-890b-290c4e9134f5`). Behaviour is correct; the suggestion is a docs note that clients should keep waiting on this 409 for as long as their model can take (Otari Switch now waits up to about two minutes), since the usual "retry a 409 three times" client default gives up while the original is still running and already billed.
