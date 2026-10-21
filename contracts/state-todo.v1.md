<!-- contract: state-todo v1 -->
# 契約:STATE.md 的 `## 待辦` 段落 v1

產生者:librarian(模型改寫,`bin/check-state-todo.py` 檢查格式,不對就請模型只修格式)、`bin/knas-tasks.py`
(機械:蓋任務 key、照 key 刪/加回 owner 的行)。讀的人:`bin/gen-todo.py`、owner 的個人助理。
**檔案裡不放版本標記**(模型改寫時會拿掉不認得的行);版本由這份契約跟 check-state-todo 決定。

## 格式

- 段落標題固定是一行 `## 待辦`,到下一個 `## ` 為止。
- 一行一項,負責人標籤三選一;沒有待辦就是一行 `- (無)`。
- 也接受分組寫法:`### 〔負責人〕` 小標題,底下 `- [ ] <文字>`(沒有標籤)。
- owner(Knas)的項目登錄成任務之後,行尾加 `(等 Knas:<key>)`(全形括號,冒號半形全形都可以)。
  key = `task-<專案>-<10 位 hex>`;Knas 在助理那邊結案時,這一行會被照 key 刪掉。

```regex item
^- \[ \] 〔(Knas|agent|待定)〕\s*(.+)$
```

```regex group
^#{3,4}\s*〔(Knas|agent|待定)〕\s*$
```

```regex bare
^- \[ \] (?!〔)(.+)$
```

```regex key
[((]等 ?Knas[::]\s*([^()()]+?)\s*[))]\s*$
```

## 版本

加說明不升版;改標籤、改 key 格式、改段落標題要升版(讀的人兩種都收,直到產生者換完)。
