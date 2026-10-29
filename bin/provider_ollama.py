"""本機 Ollama 的 provider(completion 介面;docs/design/llm-providers.md)。

用**原生 API**(/api/chat),不用它的 OpenAI 相容端點:相容端點不能設 num_ctx,超過預設 context 的 prompt 會被
默默從前面截掉。模型的 context 寫在 hub.json(provider.models 的 {name, context}),這裡照它設 num_ctx;
送出前的長度檢查在 completion.py。思考型模型(qwen3…)關掉思考(think: false),剩下的 <think> 由 completion.py 剝掉。
結構化輸出:先用 `format: <schema>`;後端不支援(MLX 回 501)就改成把 schema 寫進 prompt(provider 設 `structured: false`
可以直接跳過第一次)。
"""
from __future__ import annotations

from provider_http import RATE_LIMITED, call, schema_in_prompt
from runtime_ports import CompletionResult

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
    status, data, _, err = call("POST", url, body, {}, req.timeout_s)
    if req.schema is not None and status == 501 and "format" in body:
        # 這個模型的後端不支援結構化輸出(MLX 後端:「structured output is unavailable」):改成 schema 寫進 prompt
        body.pop("format")
        body["messages"] = msgs[:-1] + [{"role": "user", "content": schema_in_prompt(req.prompt, req.schema)}]
        status, data, _, err = call("POST", url, body, {}, req.timeout_s)
    if status in RATE_LIMITED:
        return CompletionResult(False, stop="rate_limited", error=err)
    if status != 200 or not isinstance(data, dict):
        return CompletionResult(False, stop="error", error=err or f"HTTP {status}")
    stop = "length" if data.get("done_reason") == "length" else "end"
    return CompletionResult(stop == "end", text=(data.get("message") or {}).get("content") or "", stop=stop,
                            error="" if stop == "end" else "done_reason=length",
                            input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"))


def health(conf) -> tuple:
    status, data, _, err = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/api/tags", None, {}, 10)
    if status != 200:
        return False, err or f"HTTP {status}"
    have = {m.get("name") for m in (data or {}).get("models") or []}
    want = {m if isinstance(m, str) else m.get("name") for m in (conf.get("models") or {}).values()}
    missing = sorted(w for w in want if w and w not in have and f"{w}:latest" not in have)
    return (not missing), (f"本機沒有模型:{', '.join(missing)}(ollama pull <名稱>)" if missing else "")


def list_models(conf) -> list[str]:
    """本機已經 pull 的模型。Ollama 沒開 → 空的。"""
    status, data, _, _ = call("GET", (conf.get("base_url") or DEFAULT_BASE).rstrip("/") + "/api/tags", None, {}, 10)
    return sorted(m.get("name") for m in (data or {}).get("models") or [] if m.get("name")) if status == 200 else []
