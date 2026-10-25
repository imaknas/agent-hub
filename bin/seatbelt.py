#!/usr/bin/env python3
"""平台代跑(cap-runner)用的 macOS Seatbelt profile:中立政策(policy.py)+ 能力(capabilities/<名稱>.sb)→ SBPL 文字。

不經過任何 agent:執行環境的 sandbox 裡套不了第二層(10-25 實測 `sandbox_apply: Operation not permitted`),
所以這份 profile 只在 sandbox 外的 cap-runner 用 `sandbox-exec -f` 套上。設計:docs/design/capabilities.md。

base 是「先全部放行,再擋危險的部分」(deny-list;試行期,Metal 才跑得起來):
  寫入   只准專案資料夾 + 暫存區(刻意不照 policy.write:代跑的工作不該直接寫匯流排或共用的套件 cache)
  讀取   擋 workspace_root、services_root 整個(自己的資料夾除外)、別的專案、policy.deny_read、憑證、平台狀態跟匯流排、Keychain 檔案
  網路   完全不通(含 127.0.0.1 跟 unix socket);Seatbelt 沒辦法照網域過濾
  其他   擋 Keychain 的系統服務(執行環境的 sandbox 擋不住的那個洞)、開 app、Apple Events、
         launchctl/crontab/at、看別的程序/對別的程序送信號
能力片段接在 base 後面(後面的規則優先);gpu 的片段是空的,能力在這裡的作用是授權。

  profile(pol, cap) → str        路徑直接寫進 profile(每次執行時現算),不用 -D 參數
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

TMP = ["/private/tmp", "/private/var/folders"]
DEVICES = ["/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/dtracehelper"]
KEYCHAIN_SERVICES = ["com.apple.SecurityServer", "com.apple.securityd.xpc", "com.apple.security.agent",
                     "com.apple.security.keychain-circle-notification", "com.apple.secd", "com.apple.trustd.agent"]
NO_EXEC = ["/usr/bin/open", "/usr/bin/osascript", "/bin/launchctl", "/usr/bin/crontab", "/usr/bin/at", "/usr/bin/batch",
           "/usr/bin/security"]


def q(p) -> str:
    """SBPL 的字串常值。"""
    return '"' + str(p).replace("\\", "\\\\").replace('"', '\\"') + '"'


def glob_regex(p) -> str:
    """policy.deny_read 裡的 glob(只用到 *)→ SBPL regex,含底下全部。"""
    import re
    parts = [re.escape(x) for x in str(p).split("*")]
    return '#"^' + "[^/]+".join(parts).replace('"', '\\"') + '(/|$)"'


def fragment(cap: str) -> str:
    return (hubconf.ROOT / "capabilities" / f"{cap}.sb").read_text()


def read_denies(pol: dict) -> list[str]:
    home = pathlib.Path.home()
    out = []
    for p in [hubconf.p("workspace_root"), hubconf.p("services_root"), *pol["other_projects"], *pol["credentials"],
              hubconf.STATE, hubconf.BUS_DIR, home / "Library" / "Keychains"]:
        if p:
            out.append(f"(subpath {q(p)})")
    for p in pol["deny_read"]:
        out.append(f"(regex {glob_regex(p)})" if "*" in str(p) else f"(subpath {q(p)})")
    out += [f"(literal {q(p)})" for p in pol["credential_files"]]
    return out


def profile(pol: dict, cap: str) -> str:
    ws = pathlib.Path(pol["workspace"]).resolve()
    lines = [
        "(version 1)",
        f"; agent-hub 平台代跑:能力 {cap}(bin/seatbelt.py 產生,每次執行現算;不要手改)",
        "(allow default)",
        "; 網路:完全不通(含本機跟 unix socket)",
        "(deny network-outbound (remote ip))",
        "(deny network-outbound (remote unix-socket))",
        "(deny network-inbound (local ip))",
        "(deny network-bind)",
        "; 寫入:只准專案資料夾跟暫存區(後面的 allow 優先)",
        '(deny file-write* (subpath "/"))',
        "(allow file-write* " + " ".join([f"(subpath {q(ws)})", *[f"(subpath {q(t)})" for t in TMP],
                                          *[f"(literal {q(d)})" for d in DEVICES]]) + ")",
        "; 讀取:別的專案、服務、平台狀態、憑證、Keychain 檔案;自己的資料夾放行",
        "(deny file-read* " + " ".join(read_denies(pol)) + ")",
        f"(allow file-read* (subpath {q(ws)}))",
        "; Keychain 的系統服務",
        "(deny mach-lookup " + " ".join(f"(global-name {q(s)})" for s in KEYCHAIN_SERVICES) + ")",
        "; 不准請別的系統服務在 sandbox 外替它啟動程式、不准碰別的程序",
        "(deny lsopen)",
        "(deny appleevent-send)",
        "(deny process-exec " + " ".join(f"(literal {q(x)})" for x in NO_EXEC) + ")",
        "(deny process-info*)",  # 10-25 實測:(deny signal (target others)) 擋不住,要先全擋再放行同一個 sandbox
        "(allow process-info* (target same-sandbox))",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        f"; ---------- 能力 {cap}(capabilities/{cap}.sb)----------",
        fragment(cap).rstrip(),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":  # seatbelt.py <專案> <能力>:印出會用的 profile(除錯用)
    import policy
    print(profile(policy.for_project(sys.argv[1]), sys.argv[2]))
