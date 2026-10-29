"""Anthropic Messages API 的 provider(completion 介面;docs/design/llm-providers.md)。

要結構化輸出時用一個 tool(input_schema = 要的 schema、tool_choice 指定它),模型的 tool 輸入就是答案。
max_tokens 一定要給(這家必填);stop_reason=max_tokens 當成截斷,不重試。
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
