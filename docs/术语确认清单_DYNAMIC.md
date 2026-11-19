# DYNAMIC 定制版 · 术语确认清单

2026-11-19

## 这份清单干什么用

用同一份 8 页图纸连跑三轮，发现 **14 条标注在三次运行里出现了不同的英文写法**。
不是翻译错，是同一个中文词模型每次自己挑一个近义词 —— 客户如果自己重跑一遍，
拿到的文件和上一份对不上。

要彻底消掉，只有一个办法：**把客户认可的叫法写进专属术语表**
（`glossary_dynamic.csv`），程序在翻译前就把这些词固定住。
下面每一行请客户在两列里选一个，或者自己写第三种。确认后我写进词表，下一版起生效。

## 需要确认的词

| # | 中文 | 出现过的写法 | 我们建议 | 客户确认 |
|---|---|---|---|---|
| 1 | 塑料板 | PLASTIC BOARD / PLASTIC PLATE / PLASTIC PANEL | PLASTIC BOARD | ☐ 建议　☐ 其他： |
| 2 | 模板（黑色建筑模板） | BLACK TEMPLATE / BLACK FORMWORK / BLACK BOARD | FORMWORK PANEL | ☐ 建议　☐ 其他： |
| 3 | 固定销 | FIXING PIN / FIX PIN | FIXING PIN | ☐ 建议　☐ 其他： |
| 4 | 侧面示意图 | SIDE VIEW / SIDE VIEW DETAIL | SIDE VIEW | ☐ 建议　☐ 其他： |
| 5 | 斜缝焊接 | SLANTED SEAM WELD / SLANTED SEAM WELDING / SLANT SEAM WELD | SLANTED SEAM WELD | ☐ 建议　☐ 其他： |
| 6 | 折叠 / 可折叠 | FOLDABLE / FOLDING | FOLDABLE | ☐ 建议　☐ 其他： |
| 7 | 内撑 | INTERNAL BRACE / INNER BRACE | INTERNAL BRACE | ☐ 建议　☐ 其他： |
| 8 | 方块支撑 | BLOCK BRACE / SQUARE BLOCK BRACE / SQUARE BRACE | BLOCK SUPPORT | ☐ 建议　☐ 其他： |
| 9 | 白板 | WHITE BOARD / WHITE BOARDS | WHITE BOARD | ☐ 建议　☐ 其他： |
| 10 | 边框 | FRAME（与「框架」同词） | 是否要和一个词的「框架」区分？ | ☐ 不区分　☐ 区分，叫： |
| 11 | 第一批 | FIRST BATCH: 15 SETS / FIRST BATCH 15 SETS | FIRST BATCH: 15 SETS | ☐ 建议　☐ 其他： |
| 12 | 130公斤/套 | 130 kg/SET / 130kg/SET | 130 kg/SET | ☐ 建议　☐ 其他： |

## 两类格式问题（不用挑词，选个规则就行）

这两条不是术语，是写法。定一条规则，全篇统一执行。

| # | 现在的情况 | 可选规则 | 客户选择 |
|---|---|---|---|
| A | 数量写法有两种：`10mm THK WHITE BOARD ×30 pcs` 和 `30× 10mm WHITE BOARD` | ① 数量放前面：`30 pcs 10mm THK WHITE BOARD`（英文图纸更常见）<br>② 数量放后面：`10mm THK WHITE BOARD, 30 pcs` | ☐ ①　☐ ② |
| B | 冒号时有时无：`SIZE: 1.118×3050mm` 和 `FIRST BATCH 15 SETS` | ① 字段名后一律加冒号<br>② 一律不加 | ☐ ①　☐ ② |

## 已经锁定的（不用再确认）

客户已确认、程序已经强制的：托盘=**rack**、护栏=**barricade**、底座=**feet**、
合页=**hinge**、斜的=**slanted**、部分=**part**。

这几个词在图纸上**单独成标注**时 100% 一致。在整句里出现时，
程序按这句的语法决定，不硬套（硬套出来的句子读不通）。

## 顺带说一句

第 1 条「塑料板」和第 2 条「模板」是最值得锁的：这两个词在同一份图纸里
出现 3 次以上，写法不一致最容易被客户看出来。第 2 条另外还有一层风险 ——
`TEMPLATE` 在英文里指「样板/模板文件」，`BLACK BOARD` 会被读成「黑板」，
两个都不对，必须定下来。
