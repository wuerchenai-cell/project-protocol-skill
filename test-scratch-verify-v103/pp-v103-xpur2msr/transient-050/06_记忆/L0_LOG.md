# L0 原始流水（append-only，只增不改）

> 格式：- [YYYY-MM-DD HH:mm] STATUS | TID | 结论 | 产物相对路径 | NEXT 下一步
> 一行一条，行尾必须有换行；用 progress.py add 追加会自动补齐末尾换行。
> 当前月固定叫本文件名。月末封存为 L0_LOG_YYYY-MM.md 移入 99_归档/ 后新建空文件；
> 封存是手工操作，执行前须按 00_定义/COLLAB.md 暂停所有并发写者。

- [2026-11-10 14:14] DONE | T01 | transient-record | - | NEXT -
