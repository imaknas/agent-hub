"""OpenAI Chat Completions 相容的 provider(completion 介面;docs/design/llm-providers.md)。

同一個實作涵蓋 OpenAI、OpenRouter、Gemini 的相容端點、DeepSeek、LM Studio、vLLM…:hub.json 的 provider 寫 `base_url`
(預設 https://api.openai.com/v1)。設定:
  max_tokens_field   預設 max_tokens;OpenAI 的推理模型要 max_completion_tokens
  structured         false = 不送 response_format,直接把 schema 寫進 prompt(端點不支援時;沒寫也會在 400 時自動改)
  strict             true = json_schema 的 strict 模式(那家要每個欄位都 required、additionalProperties: false,平台的 schema
                     多半不符合);預設 false:照樣給 schema 引導,驗證由 completion.py 用 minischema 做
"""
from __future__ import annotations

from provider_http import RATE_LIMITED, call, retry_after, schema_in_prompt
from runtime_ports import CompletionResult

DEFAULT_BASE = "https://api.openai.com/v1"


def _headers(conf: dict) -> dict:
    return {"Authorization": f"Bearer {conf['_key']}"} if conf.get("_key") else {}


def complete(req, conf) -> CompletionResult:
    msgs = ([{"role": "system", "content": req.system}] if req.system else []) + [{"role": "user", "content": req.prompt}]
    body = {"model": req.model, "messages": msgs, conf.get("max_tokens_field") or "max_tokens": req.max_tokens}
    if req.schema is not None:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "output", "schema": req.schema, "strict": bool(conf.get("strict"))}}
    url = (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/chat/completions"
    if conf.get("structured") is False:
        body.pop("response_format", None)
    status, data, headers, err = call("POST", url, body, _headers(conf), req.timeout_s)
    if req.schema is not None and status in (400, 422, 501) and "response_format" in body and "response_format" in err:
        # 相容端點不認 response_format / json_schema:改成 schema 寫進 prompt(驗證照樣在 completion.py)
        body.pop("response_format")
        body["messages"] = msgs[:-1] + [{"role": "user", "content": schema_in_prompt(req.prompt, req.schema)}]
        status, data, headers, err = call("POST", url, body, _headers(conf), req.timeout_s)
    if status in RATE_LIMITED:
        return CompletionResult(False, stop="rate_limited", error=err, retry_after_s=retry_after(headers))
    if status != 200 or not isinstance(data, dict) or not data.get("choices"):
        return CompletionResult(False, stop="error", error=err or f"回應沒有 choices(HTTP {status})")
    choice = data["choices"][0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    finish = choice.get("finish_reason")
    stop = {"length": "length", "content_filter": "refused"}.get(finish, "end")
    if msg.get("refusal"):
        stop = "refused"
    return CompletionResult(stop == "end", text=msg.get("content") or "", stop=stop,
                            error="" if stop == "end" else f"finish_reason={finish}",
                            input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))


def health(conf) -> tuple:
    status, _, _, err = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/models", None, _headers(conf), 20)
    return (status == 200), ("" if status == 200 else err or f"HTTP {status}")
