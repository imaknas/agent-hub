<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/wordmark-dark.svg">
    <img src="assets/logo/wordmark.svg" alt="agent-hub" width="420">
  </picture>
</p>

# agent-hub

**很多專案,交給 AI agent 去跑。每個專案都記得自己的事,由你來決定。**

[English](README.md)

## 安裝

會用終端機的話(macOS 或 Linux),貼這一行,再回答它問的幾個問題(第一題是語言):

```bash
curl -fsSL https://raw.githubusercontent.com/imaknas/agent-hub/main/install.sh | sh
```

## 交給你的 AI App 安裝

不想用終端機,就把下面這段貼進 Claude Desktop 的 Code 分頁或 Codex App:

> 幫我從 https://github.com/imaknas/agent-hub 安裝 agent-hub。照它 README 裡「Set up through your agent」的步驟做,
> 每個問題都在這個對話裡問我,要安裝任何東西之前先告訴我。

安裝過程中,有些事 agent-hub 會直接問你,不經過對話:

- 螢幕上跳出的視窗問你「是/否」:安裝軟體、改你使用者層級的設定、允許 agent 接上哪個資料夾。
- 瀏覽器裡開一個頁面讓你輸入金鑰或 token。

請在那裡回答,不要把金鑰貼進對話。

## 它做什麼

你的 agent 會把做了什麼、學到什麼寫下來。每天晚上 agent-hub 把這些筆記整理進每個專案的目前狀態,修正已經變了的
地方,再把所有專案裡重要的事收在一起。換了 session 或換了工具,東西都不會掉。只有你能決定的事,會回到你手上。

底下是讓這一切在你自己的機器上持續運作的那一層:排程和它的看門狗、各專案的背景服務、sandbox、專案之間的信箱、
憑證檢查和加密備份。

其餘的說明(需求、日常指令、更新、文件)目前只有英文版:[README.md](README.md)。
