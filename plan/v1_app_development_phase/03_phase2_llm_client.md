# Phase 2 — LLM Client (detailed plan)

**Status:** DRAFT, waiting for review and permission.
**Part of:** [00_master_plan.md](00_master_plan.md)
**Depends on:** Phase 1 (`Settings` / `load_settings()` from `app/config.py`) ✅ done.
**Unblocks:** Phase 3 (Planner) and Phase 4 (Coder), which are the two LLM steps of the chain.

## Goal

One small module, `app/llm.py`, that is the **only** place the app talks to the LLM. It should:
- send a system prompt + user prompt and return the answer as plain text,
- give repeatable output (low temperature),
- turn SDK and network failures into one clear error type the UI can show,
- be easy to replace with a fake in tests, so **no test ever calls the real API**.

## Background (checked in the installed `openai==3.49.0`)

- `openai.OpenAI(api_key=..., base_url=..., timeout=..., max_retries=...)` creates the client.
- We'll use the **Chat Completions** API (`client.chat.completions.create`), because OpenAI-compatible endpoints (your `OPENAI_BASE_URL`) support it most widely.
- The SDK raises `AuthenticationError`, `RateLimitError`, `APITimeoutError`, `APIConnectionError`, `APIStatusError` (all subclasses of `OpenAIError`).

---

## Steps

### Step 2.1 — `LLMError`
- **Why:** Planner, Coder, chain and UI only need to catch one error type, not the SDK's many.
- **What:** `class LLMError(RuntimeError)` with a short, user-friendly message. The original SDK error is kept as `__cause__` for debugging.
- **Depends on:** nothing.

### Step 2.2 — Client factory `get_client(settings)`
- **Why:** build the HTTP client once and reuse it, instead of creating a new connection on every call.
- **What:**
  ```python
  @functools.lru_cache(maxsize=1)
  def get_client(settings: Settings) -> openai.OpenAI
  ```
  - Uses `settings.openai_api_key`, `settings.openai_base_url`.
  - `timeout=30` seconds per request.
  - `max_retries=2`: the **SDK's built-in network-level retries** for connection errors, 429 rate limits and 5xx server errors (see Decision 1 below).
  - `Settings` is a frozen dataclass, so it can be the cache key.
- **Depends on:** Phase 1.

### Step 2.3 — `complete(system, user)`
- **Why:** the single function the Planner and Coder call.
- **What:**
  ```python
  def complete(system: str, user: str, *, settings: Settings | None = None,
               client: openai.OpenAI | None = None) -> str
  ```
  - `settings` defaults to `load_settings()`; `client` defaults to `get_client(settings)`. Tests pass a fake `client`.
  - Calls `chat.completions.create(model=settings.model, temperature=0, messages=[{"role": "system", ...}, {"role": "user", ...}])`.
  - Returns `choices[0].message.content` with surrounding whitespace stripped.
  - Empty or missing content raises `LLMError("The model returned an empty response")`.
- **Depends on:** 2.1, 2.2.

### Step 2.4 — Error mapping
- **Why:** the user should see a message that tells them what to do, never a raw stack trace or the API key.
- **What:** inside `complete`, catch SDK errors and raise `LLMError`:
  | SDK error | Message shown |
  |---|---|
  | `AuthenticationError` | "LLM authentication failed: check OPENAI_API_KEY" |
  | `RateLimitError` | "LLM rate limit reached: try again shortly" |
  | `APITimeoutError` | "LLM request timed out" |
  | `APIConnectionError` | "Could not reach the LLM endpoint: check OPENAI_BASE_URL" |
  | `NotFoundError` | "Model not found: check MODEL" |
  | any other `OpenAIError` | "LLM request failed" |
  Note: `APITimeoutError` is a subclass of `APIConnectionError`, so the timeout case is checked first.
- **Depends on:** 2.1, 2.3.

### Step 2.5 — `LLMFn` type for dependency injection
- **Why:** Phases 3–6 will accept "any function that takes (system, user) and returns text". Production passes `complete`; tests pass a simple fake such as `lambda s, u: "SELECT 1"`. This keeps the Planner/Coder tests free of SDK details.
- **What:** `LLMFn = Callable[[str, str], str]` exported from `app/llm.py`.
- **Depends on:** 2.3.

### Step 2.6 — Manual smoke check (`python -m app.llm`)
- **Why:** proves your real `.env` endpoint, key and model work together before we build on them.
- **What:** a small `if __name__ == "__main__":` block that sends "Reply with the single word: OK" and prints the reply (never the key).
- **Cost:** one tiny real API call. **I'll ask before running it.**
- **Depends on:** 2.3, 2.4.

### Step 2.7 — Tests (`tests/test_llm.py`)
- **Why:** lock in the behaviour without network access or an API key.
- **How:** a hand-written fake client object that records the request and returns a canned response, or raises a chosen SDK error.
- **Cases:**
  1. returns the content, whitespace stripped;
  2. sends the configured `model`, `temperature=0`, and system + user messages in the right order;
  3. empty content → `LLMError`;
  4. each SDK error in the table → `LLMError` with the expected message;
  5. error messages never contain the API key.
- **Depends on:** 2.1–2.5.

### Step 2.8 — Verify and commit
- `ruff check .` and `pytest` pass.
- Run the smoke check (2.6) only with your OK.
- Commit `"Phase 2: LLM client"`.
- **Depends on:** 2.1–2.7.

---

## Step dependencies

```
2.1 ─┐
2.2 ─┴─► 2.3 ─► 2.4 ─┬─► 2.6 (manual, with your OK) ─┐
              2.5 ───┴─► 2.7 (tests) ───────────────┴─► 2.8
```

## Files changed in this phase

- `app/llm.py` (placeholder replaced with real code)
- `tests/test_llm.py` (new)

## Decisions needed

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Keep the SDK's **network-level** retries (`max_retries=2`) for dropped connections, rate limits and server errors? This is separate from your "no retries" rule, which means the Coder is **not** asked to fix SQL that fails review. | **Yes**: it only repeats the same request after a transient network or server failure; it never changes the prompt or the result logic. Set to `0` if you want zero automatic repeats of any kind. |
| 2 | `temperature=0`? Some reasoning models reject the `temperature` parameter. | **Yes**. If your `MODEL` rejects it, the smoke check (2.6) will show it and I'll make temperature optional. |
| 3 | Run the smoke check (one tiny real API call) during this phase? | **Yes** |
