"""判斷點的 completion(只回答案):用哪一家、哪個模型、key 在哪,跟每家都要過的同一套處理。docs/design/llm-providers.md。

hub.json 的 `llm` 區塊(沒有這個區塊 = 現在的行為:判斷點照舊交給 runtime.default 的 agent 介面,這支不參與):

  "llm": {
    "providers": {
      "<名稱>": {"impl": "anthropic | openai | ollama | <有 completion 的 runtime,例 claude>",
                 "base_url": "…", "key": {"secret": "<secrets_dir 裡的檔名>"} | {"env": "<環境變數>"},
                 "location": "cloud | local", "models": {"fast": "<名稱>" | {"name", "context", "price": {"in", "out"}}, …},
                 "strict": false, "max_tokens_field": "max_tokens"}
    },
    "use": {"fast": "<provider>", "standard": "<provider>", "strong": "<provider>"},   判斷點的 model_group → provider
    "points": {"<判斷點>": {"provider": "<名稱>", "model": "<名稱>"}},                  個別判斷點蓋過
    "budget": {"daily_usd": 2.0}
  }

每家都一樣的處理(實作只負責「送出去、拿回原文」):
  1. 送出前遮掉像憑證的字串(全平台一個遮蔽點)
  2. 估長度:超過那個模型的 context 就不送(不讓 provider 默默截斷)
  3. 今天的花費超過 budget.daily_usd 就不送
  4. 429/503/529 退避重試(最多 3 次);長度截斷不重試(會原樣再截一次)
  5. 剝掉推理模型的 <think>…</think>,解析 JSON(容忍 ``` 圍起來),用 minischema 驗證;不合格重試一次並附上錯誤
  6. 每次呼叫記在 .state/llm-usage.jsonl(token、成本)
key 只在這裡讀(secrets_dir 或環境變數),放進 conf["_key"] 交給實作,不進任何子程序的環境變數。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import minischema  # noqa: E402

USAGE = hubconf.STATE / "llm-usage.jsonl"
GROUPS = ("fast", "standard", "strong")
DEFAULT_GROUP = "standard"
MAX_RATE_RETRIES = 3

# 送給模型前遮掉像憑證的字串(原本在 reflect_llm.py:第一次試跑時,模型把對話裡貼過的 Finnhub API key 前幾碼引進了報告)。
# 已知前綴的 token,跟夠長、字母數字混雜的長字串一律換成 [已遮蔽]。
SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_\-]{10,}|sk-ant-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[abpr]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-.]+"
    r"|(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,})")


def redact(text: str) -> str:
    return SECRET.sub("[已遮蔽]", text)


class Refused(Exception):
    """這次不送(設定不對、沒有 key、超過長度、預算用完):原因給人看。"""


def settings() -> dict | None:
    s = hubconf.get("llm")
    return s if isinstance(s, dict) and s.get("providers") else None


def key_envs() -> set[str]:
    """設定裡用環境變數給的 key:交給任何子程序(agent job、信箱 session)之前要拿掉,不然有 Bash 的 agent 都讀得到。"""
    s = settings() or {}
    return {p["key"]["env"] for p in (s.get("providers") or {}).values()
            if isinstance(p, dict) and isinstance(p.get("key"), dict) and p["key"].get("env")}


def read_key(conf: dict) -> str | None:
    """None = 這家不需要 key(例:本機的 Ollama、Claude Code 自己登入)。要卻拿不到 → Refused。"""
    k = conf.get("key")
    if not k:
        return None
    if k.get("secret"):
        f = hubconf.secrets_dir() / k["secret"]
        try:
            v = f.read_text().strip()
        except OSError:
            raise Refused(f"沒有 key:{f} 不在(bin/hub llm set-key 寫進去)")
        if not v:
            raise Refused(f"沒有 key:{f} 是空的")
        return v
    if k.get("env"):
        v = os.environ.get(k["env"])
        if not v:
            raise Refused(f"沒有 key:環境變數 {k['env']} 沒有設(排程跑在 launchd 底下,讀不到 shell 的設定——用 secret 比較可靠)")
        return v
    raise Refused("key 要寫成 {\"secret\": 檔名} 或 {\"env\": 變數名}")


def resolve(point: str, spec: dict) -> dict:
    """這個判斷點交給哪一家、哪個模型:llm.points 的指定 > llm.use[model_group] 的那一家的 models[model_group]。"""
    s = settings()
    if not s:
        raise Refused("hub.json 沒有 llm 區塊")
    providers = s["providers"]
    group = spec.get("model_group") or DEFAULT_GROUP
    pin = (s.get("points") or {}).get(point) or {}
    name = pin.get("provider") or (s.get("use") or {}).get(group)
    if not name:
        raise Refused(f"llm.use 沒有寫 {group} 要交給哪一家")
    conf = providers.get(name)
    if not isinstance(conf, dict):
        raise Refused(f"llm.providers 沒有 {name}")
    entry = pin.get("model") or (conf.get("models") or {}).get(group)
    if not entry:
        raise Refused(f"llm.providers.{name}.models 沒有 {group}")
    m = entry if isinstance(entry, dict) else {"name": entry}
    return {"provider": name, "impl": conf.get("impl"), "conf": conf, "group": group, "model": m.get("name"),
            "context": m.get("context"), "price": m.get("price"), "location": conf.get("location", "cloud")}


def estimate_tokens(text: str) -> int:
    """寧可高估:UTF-8 位元組 / 3(中文一個字約一個 token、英文約 4 個字元一個 token,這樣兩邊都偏高)。"""
    return len(text.encode()) // 3 + 1


def today_cost() -> float:
    today = datetime.date.today().isoformat()
    total = 0.0
    try:
        for line in USAGE.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if str(r.get("ts", "")).startswith(today) and r.get("cost_usd"):
                total += float(r["cost_usd"])
    except OSError:
        pass
    return total


def record(point: str, target: dict, res) -> float | None:
    cost = res.cost_usd
    price = target.get("price") or {}
    if cost is None and price and res.input_tokens is not None:
        cost = ((res.input_tokens or 0) * float(price.get("in", 0)) + (res.output_tokens or 0) * float(price.get("out", 0))) / 1e6
    row = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "point": point,
           "provider": target["provider"], "model": target["model"], "input_tokens": res.input_tokens,
           "output_tokens": res.output_tokens, "cost_usd": cost, "stop": res.stop}
    try:
        USAGE.parent.mkdir(parents=True, exist_ok=True)
        with USAGE.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return cost


THINK = re.compile(r"<think>.*?</think>", re.S)


def parse_json(text: str):
    t = THINK.sub("", text)
    if "</think>" in t:            # 沒有開頭標籤的(有些模型只印結尾)
        t = t.split("</think>", 1)[1]
    t = t.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if fence:
        t = fence.group(1)
    try:
        return json.loads(t)
    except ValueError:
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            return json.loads(t[i:j + 1])   # 再不行就丟 ValueError
        raise


def runtime_target(name: str, model: str | None, auth: str) -> dict:
    """沒有 llm 區塊時:交給執行環境(runtime.default)的 completion 介面,模型照舊的設定(models.env / 宣告的預設)。"""
    return {"provider": name, "impl": name, "conf": {"auth": auth}, "group": None, "model": model,
            "context": None, "price": None, "location": "cloud"}


def run(point: str, spec: dict, prompt: str, schema: dict | None, sleep=time.sleep, target: dict | None = None) -> dict:
    """跑一次 completion。回傳跟 llm.py 給呼叫端的同一種形狀;失敗丟 Refused(原因)。
    target 沒給 = 照 hub.json llm 決定;給了(runtime_target)= 交給那個執行環境。"""
    import runtime_ports
    target = target or resolve(point, spec)
    conf = dict(target["conf"])
    conf["_key"] = read_key(conf)
    conf["_context"] = target["context"]
    budget = ((settings() or {}).get("budget") or {}).get("daily_usd")
    if budget is not None and today_cost() >= float(budget):
        raise Refused(f"今天的模型花費已經到預算(llm.budget.daily_usd ${budget}),這次不送")
    prompt = redact(prompt)
    max_tokens = int(spec.get("max_tokens") or 8192)
    if target["context"] and estimate_tokens(prompt) + max_tokens > int(target["context"]):
        raise Refused(f"prompt 太長:估計 {estimate_tokens(prompt)} token + 輸出 {max_tokens} 超過 "
                      f"{target['provider']}/{target['model']} 的 context {target['context']}")
    impl = runtime_ports.load_completion(target["impl"])
    req = runtime_ports.CompletionRequest(point=point, prompt=prompt, model=target["model"], schema=schema,
                                          max_tokens=max_tokens, timeout_s=int(spec.get("timeout_s") or 600))
    cost_total, last_errs = 0.0, None
    for attempt in range(2):   # 第二次 = 輸出不合格時附上錯誤再問一次
        rate_tries = 0
        while True:
            res = impl.complete(req, conf)
            cost_total += record(point, target, res) or 0
            if res.stop != "rate_limited" or rate_tries >= MAX_RATE_RETRIES:
                break
            rate_tries += 1
            sleep(min(res.retry_after_s or 5 * 2 ** rate_tries, 60))
        if not res.ok:
            if res.stop == "length":
                raise Refused(f"輸出被長度截斷({target['provider']}/{target['model']},max_tokens {max_tokens});不重試")
            raise Refused(f"{target['provider']}/{target['model']}:{res.stop}:{res.error}")
        if schema is None:
            return _out(point, target, res.text, None, cost_total)
        try:
            obj = parse_json(res.text)
            last_errs = minischema.errors(schema, obj)
        except ValueError:
            obj, last_errs = None, ["不是 JSON"]
        if not last_errs:
            return _out(point, target, res.text, obj, cost_total)
        req = req._replace(prompt=prompt + "\n\n---\n上一次的回答不合格(" + "; ".join(last_errs[:5])
                           + ")。這次只回一個合格的 JSON,不要其他文字。")
    raise Refused("輸出不符合 schema(重試一次後):" + "; ".join(last_errs[:3]))


def _out(point, target, text, obj, cost) -> dict:
    # 欄位名稱跟 llm.py 原本給呼叫端的一樣(librarian、consolidate…讀這些)
    return {"type": "result", "is_error": False, "result": text, "structured_output": obj, "total_cost_usd": cost or None,
            "llm_point": point, "provider": target["provider"], "model": target["model"]}


def health_all() -> list[tuple[str, bool, str]]:
    """(provider, 好不好, 原因)——doctor 用。key 拿不到也算不好。"""
    import runtime_ports
    out = []
    for name, conf in ((settings() or {}).get("providers") or {}).items():
        try:
            c = dict(conf)
            c["_key"] = read_key(c)
            ok, why = runtime_ports.load_completion(c.get("impl")).health(c)
        except (Refused, KeyError, runtime_ports.Unsupported, runtime_ports.MissingFunctions) as e:
            ok, why = False, str(e)
        out.append((name, ok, why))
    return out


# ---------- 設定的檢查(config-check 叫)----------
# key 的值永遠不准寫在 hub.json:看到像 key 的字串就擋。模型名稱常有數字跟連字號(例:<家族>-<型號>-4-5-20251001),
# 所以不用 SECRET 那條「夠長、字母數字混雜」的規則,只認已知前綴跟 32 個以上沒有分隔符號的字母數字。
KEYLIKE = re.compile(r"^(?:sk-|sk-ant-|AIza|gh[pousr]_|xox[abpr]-|eyJ)|^[A-Za-z0-9]{32,}$")
PRIVATE = re.compile(r"^(?:localhost|127\.\d+\.\d+\.\d+|\[?::1\]?|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
                     r"|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+|[\w.-]+\.ts\.net)$")


def _strings(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _strings(v, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(o, str):
        yield path, o


def config_errors(llm: dict) -> list[str]:
    import urllib.parse
    import runtime_ports
    errs = []
    for where, v in _strings(llm):
        if KEYLIKE.search(v):
            errs.append(f"{where} 看起來是 key 的值——值放 secrets 目錄({{\"secret\": 檔名}})或環境變數({{\"env\": 名稱}})")
    providers = llm.get("providers") or {}
    if not isinstance(providers, dict) or not providers:
        return errs + ["providers 至少要有一家"]
    for name, p in providers.items():
        if not isinstance(p, dict):
            errs.append(f"providers.{name} 要是物件")
            continue
        impl = p.get("impl")
        if impl in runtime_ports.PROVIDERS:
            pass
        elif impl in runtime_ports.RUNTIMES:
            if "completion" not in runtime_ports.capabilities(impl):
                errs.append(f"providers.{name}:執行環境 {impl} 沒有 completion 介面")
        else:
            errs.append(f"providers.{name}.impl `{impl}` 不認得(有:{', '.join([*runtime_ports.PROVIDERS, *runtime_ports.RUNTIMES])})")
        k = p.get("key")
        if k is not None and (not isinstance(k, dict) or len([x for x in ("secret", "env") if k.get(x)]) != 1):
            errs.append(f"providers.{name}.key 要寫成 {{\"secret\": 檔名}} 或 {{\"env\": 變數名}}(只能一種)")
        elif k and k.get("secret") and ("/" in k["secret"] or k["secret"].startswith(".")):
            errs.append(f"providers.{name}.key.secret 只寫檔名(放在平台的 secrets 目錄裡)")
        if p.get("location", "cloud") not in ("cloud", "local"):
            errs.append(f"providers.{name}.location 只能是 cloud / local")
        if p.get("location") == "local":
            host = urllib.parse.urlparse(p.get("base_url") or "").hostname or ""
            if not (p.get("base_url") and PRIVATE.match(host)):
                errs.append(f"providers.{name}:location local 只准本機、私有網段、tailnet 的 base_url(現在是 {p.get('base_url')!r})")
        for g, m in (p.get("models") or {}).items():
            if g not in GROUPS:
                errs.append(f"providers.{name}.models.{g}:組名只能是 {' / '.join(GROUPS)}")
            if not (isinstance(m, str) and m) and not (isinstance(m, dict) and m.get("name")):
                errs.append(f"providers.{name}.models.{g} 要是模型名稱,或 {{name, context?, price?}}")
    for g, name in (llm.get("use") or {}).items():
        if g not in GROUPS:
            errs.append(f"use.{g}:組名只能是 {' / '.join(GROUPS)}")
        elif name not in providers:
            errs.append(f"use.{g}:沒有 provider {name}")
        elif g not in ((providers[name] or {}).get("models") or {}):
            errs.append(f"use.{g} → {name},但 providers.{name}.models 沒有 {g}")
    try:
        import yaml
        known = set(yaml.safe_load((hubconf.ROOT / "contracts" / "llm-points.yaml").read_text())["points"])
    except Exception:
        known = None
    for pt, pin in (llm.get("points") or {}).items():
        if known is not None and pt not in known:
            errs.append(f"points.{pt}:不是宣告過的判斷點")
        if not isinstance(pin, dict) or pin.get("provider") not in providers:
            errs.append(f"points.{pt}.provider 要是 providers 裡的一家")
    return errs
