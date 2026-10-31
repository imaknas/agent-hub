"""Provider for the Anthropic Messages API (completion interface; docs/design/llm-providers.md).

Structured output uses one tool (input_schema = the wanted schema, tool_choice names it); the model's tool input is
the answer.
max_tokens must always be given (this provider requires it); stop_reason=max_tokens counts as truncated, no retry.
"""
from __future__ import annotations

import json

from provider_http import RATE_LIMITED, call, retry_after
from runtime_ports import CompletionResult

DEFAULT_BASE = "https://api.anthropic.com"
VERSION = "2023-06-01"


def _headers(conf: dict) -> dict:
    return {"x-api-key": conf.get("_key") or "", "anthropic-version": VERSION}


def complete(req, conf) -> CompletionResult:
    body = {"model": req.model, "max_tokens": req.max_tokens, "messages": [{"role": "user", "content": req.prompt}]}
    if req.system:
        body["system"] = req.system
    if req.schema is not None:
        body["tools"] = [{"name": "output", "description": "回答(照這個格式)", "input_schema": req.schema}]
        body["tool_choice"] = {"type": "tool", "name": "output"}
    status, data, headers, err = call("POST", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/v1/messages",
                                      body, _headers(conf), req.timeout_s)
    if status in RATE_LIMITED:
        return CompletionResult(False, stop="rate_limited", error=err, retry_after_s=retry_after(headers))
    if status != 200 or not isinstance(data, dict):
        return CompletionResult(False, stop="error", error=err or f"HTTP {status}")
    usage = data.get("usage") or {}
    reason = data.get("stop_reason")
    stop = {"max_tokens": "length", "refusal": "refused"}.get(reason, "end")
    blocks = data.get("content") or []
    if req.schema is not None:
        tool = next((b for b in blocks if b.get("type") == "tool_use"), None)
        text = json.dumps(tool["input"], ensure_ascii=False) if tool else ""
    else:
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return CompletionResult(stop == "end", text=text, stop=stop, error="" if stop == "end" else f"stop_reason={reason}",
                            input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))


def health(conf) -> tuple:
    status, _, _, err = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/v1/models", None, _headers(conf), 20)
    return (status == 200), ("" if status == 200 else err or f"HTTP {status}")


def list_models(conf) -> list[str]:
    """The models this key can see (newest first; agenthub llm setup lets the user choose, the platform doesn't guess
    model names). Can't get them → empty."""
    status, data, _, _ = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/v1/models?limit=100", None,
                              _headers(conf), 20)
    return [m.get("id") for m in (data or {}).get("data") or [] if m.get("id")] if status == 200 else []
