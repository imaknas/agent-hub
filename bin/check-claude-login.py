#!/usr/bin/env python3
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
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import hubconf  # noqa: E402

HOME = pathlib.Path.home()
TOKEN = HOME / ".claude" / "headless-oauth-token"
CREDS = HOME / ".claude" / ".credentials.json"
STREAK = hubconf.STATE / "claude-login-streak.json"
AUTH = re.compile(r"login|401|unauthori[sz]ed|oauth|invalid api key|authentication|credential|expired", re.I)
REFRESH_WARN = datetime.timedelta(days=7)
TOKEN_WARN_AGE = datetime.timedelta(days=335)
SOFT_LIMIT = 3


def ask(env: dict) -> str | None:
    """None = 成功;否則是失敗原因。"""
    try:
        r = subprocess.run(["/opt/homebrew/bin/python3", str(hubconf.ROOT / "bin" / "llm.py"), "run", "login-check"],
                           input="只回覆 ok 兩個字母。", capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        return "超過 3 分鐘沒回應"
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return f"llm.py 沒有輸出 JSON(exit {r.returncode}):{r.stderr.strip()[-200:]}"
    return out.get("llm_error") or (out.get("result") if out.get("is_error") else None)


def main() -> int:
    now = datetime.datetime.now()
    hard: list[str] = []
    soft: dict[str, str] = {}
    base = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_OAUTH_TOKEN", "LLM_AUTH")}

    if (err := ask(base)) is None:
        print("訂閱登入(launchd):ok")
    elif AUTH.search(err) or "找不到 claude" in err:
        hard.append(f"claude 的訂閱登入(Upwork job、Muninn 用的)不能用了:{err[:200]}——在 mini 上登入(`claude` → /login)")
    else:
        soft["subscription"] = err

    if TOKEN.exists():
        if (err := ask({**base, "LLM_AUTH": "token"})) is None:
            print("headless token:ok")
        elif AUTH.search(err):
            hard.append(f"headless token(所有排程跟租借用的)不能用了:{err[:200]}——重跑 `claude setup-token` 寫進 {TOKEN}")
        else:
            soft["token"] = err
        age = now - datetime.datetime.fromtimestamp(TOKEN.stat().st_mtime)
        if age > TOKEN_WARN_AGE:
            hard.append(f"headless token 已經 {age.days} 天(一年到期),快要重跑 `claude setup-token` 寫進 {TOKEN}")
    else:
        hard.append(f"沒有 {TOKEN}(所有排程跟租借都會失敗)")

    if CREDS.exists():
        try:
            oauth = json.loads(CREDS.read_text()).get("claudeAiOauth") or {}
            exp = oauth.get("refreshTokenExpiresAt")
        except ValueError:
            oauth, exp = {}, None
        if exp:
            left = datetime.datetime.fromtimestamp(exp / 1000) - now
            print(f".credentials.json(SSH 用):refresh 還有 {left.days} 天")
            if left < REFRESH_WARN:
                hard.append(f"~/.claude/.credentials.json(SSH 底下的 claude 用的)登入 {max(left.days, 0)} 天內到期——"
                            "在 mini 的終端機跑一次 claude 讓它更新,或重新 /login")
        elif oauth:
            print(".credentials.json:沒有 refreshTokenExpiresAt,略過到期檢查")

    try:
        streak = json.loads(STREAK.read_text())
    except (OSError, ValueError):
        streak = {}
    streak = {k: streak.get(k, 0) + 1 for k in soft}
    STREAK.write_text(json.dumps(streak))
    for k, err in soft.items():
        print(f"{k}:請求失敗(不像登入問題,連續第 {streak[k]} 次):{err[:200]}")
        if streak[k] >= SOFT_LIMIT:
            hard.append(f"claude 請求連續 {streak[k]} 次失敗({k}):{err[:200]}")

    alert.once("claude-login", hard, "claude 登入檢查恢復正常")
    for x in hard:
        print(f"⚠️ {x}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
