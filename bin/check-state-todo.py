#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""檢查一份 STATE.md 的 `## 待辦` 段落格式,跟 gen-todo.py 用同一套解析規則。

librarian 每次改完一個專案的 STATE.md 就呼叫:有問題就印出來(結束碼 1),讓
librarian 請模型只修格式。沒問題什麼都不印、結束碼 0。佔位用的小檔案不檢查。

用法:check-state-todo.py <STATE.md>
"""
import importlib.util
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("gen_todo", here / "gen-todo.py")
gen_todo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_todo)


def main() -> None:
    path = pathlib.Path(sys.argv[1])
    if not path.exists() or path.stat().st_size <= gen_todo.PLACEHOLDER_MAX_BYTES:
        return
    items = {o: [] for o in gen_todo.OWNERS}
    problems: list[str] = []
    gen_todo.parse("STATE.md", path.read_text(), items, problems)
    if problems:
        print("\n".join(problems))
        sys.exit(1)


if __name__ == "__main__":
    main()
