"""LLM provider 共用的 HTTP(標準函式庫)。key 只放在這次請求的 header,不進環境變數、不寫 log。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def call(method: str, url: str, body: dict | None, headers: dict, timeout: float) -> tuple[int | None, object, dict, str]:
    """(HTTP 狀態, 解析過的 JSON 或 None, 回應的 header, 錯誤說明)。狀態 None = 連不上。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), dict(r.headers), ""
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        return e.code, parsed, dict(e.headers or {}), f"HTTP {e.code}:{raw[:300].decode(errors='replace')}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, None, {}, str(e)[:300]


RATE_LIMITED = {429, 503, 529}   # 太多請求、暫時過載:退避後重試


def retry_after(headers: dict) -> float | None:
    v = {k.lower(): x for k, x in headers.items()}.get("retry-after")
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def schema_in_prompt(prompt: str, schema: dict) -> str:
    """provider 不支援結構化輸出(例:Ollama 的 MLX 後端回 501、有些相容端點不認 response_format):把 schema 寫進 prompt。
    回來的仍然由 completion.py 解析、用 minischema 驗證、不合格重試一次——不靠 provider 保證格式。"""
    return (prompt + "\n\n---\n只回一個 JSON 物件,不要其他文字、不要 ``` 圍起來。它必須符合這個 JSON schema:\n"
            + json.dumps(schema, ensure_ascii=False))
