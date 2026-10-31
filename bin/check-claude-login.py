#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""claude 的登入還能不能用(平台 job agent-hub/claude-login,每小時)。壞了或快到期就發 Discord。

mini 上 claude 的憑證有三份,各自會壞(2026-10-24 Knas:登入失效時 Muninn 默默改用本機模型是嚴重的 silent fail):
  1. 訂閱登入,launchd 底下(要 claude.ai connector 的 job、Muninn):keychain。實際打一次最小的請求(llm.py 的 login-check)
  2. headless token(~/.claude/headless-oauth-token,`claude setup-token`,一年):llm.py 預設的憑證(排程、租借、
     lease-resume)。LLM_AUTH=token 打一次請求;檔案超過 335 天就提醒(一年到期前 30 天)
  3. ~/.claude/.credentials.json(SSH 底下讀不到 keychain 時 claude 用這份):只看 refreshTokenExpiresAt,
     剩不到 7 天就提醒——不讀、不印任何 token
請求失敗而且是登入問題(Login expired、/login、401、OAuth…)才算登入壞了;逾時、額度之類的只記在輸出,不發警報
(連續 3 次才發:可能是 claude 本身壞了)。

用法:check-claude-login.py
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402
import runtime_ports  # noqa: E402  有哪些登入、放哪、錯誤長怎樣是 runtime 的事(auth 介面,第 6 層)

STREAK = hubconf.STATE / "claude-login-streak.json"
SOFT_LIMIT = 3
_ = i18n.domain(__file__)


# 平台知道的:每種登入是誰在用(壞了的時候,警報要說影響到誰)
def used_by(mode: str) -> str | None:
    return {"subscription": _("used by the connector jobs and the assistant"),
            "token": _("used by all scheduled jobs and leases")}.get(mode)


def missing_means(mode: str) -> str:
    return _("all scheduled jobs and leases will fail") if mode == "token" else _("every call with this login will fail")


def ask(env: dict) -> str | None:
    """None = 成功;否則是失敗原因。"""
    try:
        r = subprocess.run([sys.executable, str(hubconf.ROOT / "bin" / "llm.py"), "run", "login-check"],
                           input="只回覆 ok 兩個字母。", capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        return _("No answer for over 3 minutes")
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return _("llm.py printed no JSON (exit {code}): {err}", code=r.returncode, err=r.stderr.strip()[-200:])
    return out.get("llm_error") or (out.get("result") if out.get("is_error") else None)


def main() -> int:
    now = datetime.datetime.now()
    hard: list[str] = []
    soft: dict[str, str] = {}
    rt = runtime_ports.load(ports=("auth",))
    base = rt.clean_env({k: v for k, v in os.environ.items() if k != "LLM_AUTH"})

    for c in rt.credentials():
        if c.mode:  # 能打請求檢查的登入:在就打一次,不在就是問題
            if not c.present:
                hard.append(_("{path} is missing ({means})", path=c.path, means=missing_means(c.mode)))
                continue
            if (err := ask(base if c.mode == "subscription" else {**base, "LLM_AUTH": c.mode})) is None:
                print(_("{name}: ok", name=c.name))
            elif rt.is_login_error(err):
                hard.append(_("{label} ({used_by}) does not work: {err} — {renew}", label=c.label,
                              used_by=used_by(c.mode) or c.used_by or "", err=err[:200], renew=c.renew))
            else:
                soft[c.mode] = err
        if c.present and c.since and c.max_age:
            age = now - c.since
            if age > c.max_age:
                hard.append(_("{label} is {days} days old (expires after {lifetime}); soon: {renew}", label=c.label,
                              days=age.days, lifetime=c.lifetime, renew=c.renew))
        if c.present and c.expires:
            left = c.expires - now
            print(_("{name}: refresh valid for {days} more days", name=c.name, days=left.days))
            if c.warn_before and left < c.warn_before:
                hard.append(_("{label} ({used_by}): the login expires within {days} days — {renew}", label=c.label,
                              used_by=c.used_by, days=max(left.days, 0), renew=c.renew))
        elif c.present and c.note:
            print(c.note)

    try:
        streak = json.loads(STREAK.read_text())
    except (OSError, ValueError):
        streak = {}
    streak = {k: streak.get(k, 0) + 1 for k in soft}
    STREAK.write_text(json.dumps(streak))
    for k, err in soft.items():
        print(_("{mode}: request failed (does not look like a login problem; {n} in a row): {err}", mode=k, n=streak[k],
                err=err[:200]))
        if streak[k] >= SOFT_LIMIT:
            hard.append(_("claude requests failed {n} times in a row ({mode}): {err}", n=streak[k], mode=k, err=err[:200]))

    alert.once("claude-login", hard, _("The claude login check is back to normal"))
    for x in hard:
        print(f"⚠️ {x}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
