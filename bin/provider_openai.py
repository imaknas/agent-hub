"""Provider compatible with OpenAI Chat Completions (completion interface; docs/design/llm-providers.md).

One implementation covers OpenAI, OpenRouter, Gemini's compatible endpoint, DeepSeek, LM Studio, vLLM…: the provider
in hub.json sets `base_url` (default https://api.openai.com/v1). Settings:
  max_tokens_field   default max_tokens; OpenAI's reasoning models need max_completion_tokens
  structured         false = don't send response_format, write the schema into the prompt instead (when the endpoint
                     doesn't support it; if unset, it also switches automatically on a 400)
  strict             true = json_schema's strict mode (that provider wants every field required and
                     additionalProperties: false, which the platform's schemas mostly don't meet); default false: the
                     schema is still given as guidance, validation is done by completion.py with minischema
"""
from __future__ import annotations

import i18n
from provider_http import RATE_LIMITED, call, retry_after, schema_in_prompt
from runtime_ports import CompletionResult

_ = i18n.domain(__file__)

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
        # the compatible endpoint doesn't know response_format / json_schema: write the schema into the prompt instead
        # (validation still happens in completion.py)
        body.pop("response_format")
        body["messages"] = msgs[:-1] + [{"role": "user", "content": schema_in_prompt(req.prompt, req.schema)}]
        status, data, headers, err = call("POST", url, body, _headers(conf), req.timeout_s)
    if status in RATE_LIMITED:
        return CompletionResult(False, stop="rate_limited", error=err, retry_after_s=retry_after(headers))
    if status != 200 or not isinstance(data, dict) or not data.get("choices"):
        return CompletionResult(False, stop="error", error=err or _("no choices in the response (HTTP {status})", status=status))
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
    status, _d, _h, err = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/models", None, _headers(conf), 20)
    return (status == 200), ("" if status == 200 else err or f"HTTP {status}")


def list_models(conf) -> list[str]:
    """The models this key can see (agenthub llm setup lets the user choose; the platform doesn't guess model names).
    Can't get them → empty."""
    status, data, _h, _e = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/models", None, _headers(conf), 20)
    return sorted(m.get("id") for m in (data or {}).get("data") or [] if m.get("id")) if status == 200 else []
