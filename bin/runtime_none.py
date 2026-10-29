"""沒有 coding agent 的執行環境:runtime_ports 的每個介面都沒有(docs/design/tool-agnostic.md)。

只有 API key、沒裝任何 coding agent 的人用這個(hub.json `runtime.default: "none"`)。`runtime_ports.load()` 對這裡的每個
介面都丟 `Unsupported`,呼叫端照「不支援」處理:信箱交辦留在信箱等人、memory-gaps 不看對話、週報沒有作息那段、
hook 不裝——明確關掉、說出原因,不拿空資料照跑。判斷點之後走 completion 介面(llm-providers.md),不需要這裡有東西。

刻意是空的:任何函式加進來都會讓它「實作了某個介面的一部分」,載入時就被當成 bug 拒絕。
"""
