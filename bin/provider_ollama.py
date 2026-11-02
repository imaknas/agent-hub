"""The provider for a local Ollama (completion interface; docs/design/llm-providers.md).

Uses the **native API** (/api/chat), not its OpenAI-compatible endpoint: the compatible endpoint cannot set num_ctx,
so a prompt longer than the default context is silently cut from the front. The model's context is in {settings}
({name, context} in provider.models) and num_ctx is set from it here; the length check before sending is in
completion.py. Thinking models (qwen3…) have thinking turned off (think: false); completion.py strips any remaining
<think>.
Structured output: first `format: <schema>`; if the backend does not support it (MLX answers 501), the schema is
written into the prompt instead (setting `structured: false` on the provider skips the first attempt).
"""
from __future__ import annotations

import i18n
from provider_http import RATE_LIMITED, call, schema_in_prompt
from runtime_ports import CompletionResult

_ = i18n.domain(__file__)

DEFAULT_BASE = "http://127.0.0.1:11434"


def complete(req, conf) -> CompletionResult:
    msgs = ([{"role": "system", "content": req.system}] if req.system else []) + [{"role": "user", "content": req.prompt}]
    options = {"num_predict": req.max_tokens}
    if conf.get("_context"):
        options["num_ctx"] = int(conf["_context"])
    body = {"model": req.model, "messages": msgs, "stream": False, "options": options, "think": False}
    url = (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/api/chat"
    if req.schema is not None and conf.get("structured", True):
        body["format"] = req.schema
    status, data, _h, err = call("POST", url, body, {}, req.timeout_s)
    if req.schema is not None and status == 501 and "format" in body:
        # this model's backend does not support structured output (MLX backend: "structured output is unavailable"):
        # write the schema into the prompt instead
        body.pop("format")
        body["messages"] = msgs[:-1] + [{"role": "user", "content": schema_in_prompt(req.prompt, req.schema)}]
        status, data, _h, err = call("POST", url, body, {}, req.timeout_s)
    if status in RATE_LIMITED:
        return CompletionResult(False, stop="rate_limited", error=err)
    if status != 200 or not isinstance(data, dict):
        return CompletionResult(False, stop="error", error=err or f"HTTP {status}")
    stop = "length" if data.get("done_reason") == "length" else "end"
    return CompletionResult(stop == "end", text=(data.get("message") or {}).get("content") or "", stop=stop,
                            error="" if stop == "end" else "done_reason=length",
                            input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"))


def health(conf) -> tuple:
    status, data, _h, err = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/api/tags", None, {}, 10)
    if status != 200:
        return False, err or f"HTTP {status}"
    have = {m.get("name") for m in (data or {}).get("models") or []}
    want = {m if isinstance(m, str) else m.get("name") for m in (conf.get("models") or {}).values()}
    missing = sorted(w for w in want if w and w not in have and f"{w}:latest" not in have)
    return (not missing), (_("models not on this machine: {models} (ollama pull <name>)", models=", ".join(missing))
                            if missing else "")


def list_models(conf) -> list[str]:
    """Models already pulled on this machine. Ollama not running -> empty."""
    status, data, _h, _e = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/api/tags", None, {}, 10)
    return sorted(m.get("name") for m in (data or {}).get("models") or [] if m.get("name")) if status == 200 else []
