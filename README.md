# PDF 图纸翻译

[English](#english) | 中文

把**工程图纸 PDF** 在中文和英文之间对译：图形线条、版式、字号、位置一律不动，
只把文字换掉，并且自动把英制尺寸换算成公制。

> Translate engineering drawings (PDF) between Chinese and English **without
> touching the geometry**. Text is replaced in place; imperial dimensions are
> converted to metric, and every converted value is highlighted in a second copy.

![效果对比](docs/demo.png)

---

## 它解决什么问题

直接用 Google 翻译或 AI 翻图纸，出来是纯文本，版式全丢；用 PDF 编辑软件手工改，
一张多视图图纸要改半天，而且——这是最要命的——**英制尺寸不会自己变成公制**。

标注 `1-1/2"` 换成中文后如果还是 `1-1/2"`，车间拿到的是一张看不懂的数。
如果翻了却没换算，或者算了数却忘了改单位栏，比不翻更危险：
`38.10` 被当成 38.10 英寸下料，就要多做 25 倍的材料。

这个工具把这三件事一次做完：**原位替换文字 + 换算成公制 + 标出哪些数被改过**。

## 主要特性

- **版式零破坏**：按原文字对象的位置、角度、字号重新排字，图形和线条一个像素不动
- **英制自动换算**：`4"`、`1-1/2"`、`4''`、`2'-0"`、`9 ft`、`25 lb`、`8 oz`、`9英尺` 都认
- **换算结果标黄**：另出一份标注版，所有被换算的数值用荧光笔画出来，方便逐个核对
- **换算后复算校验**：程序拿原文按同一套规则独立算一遍，和译文逐一比对，算错自动改；
  判不准的写进校验清单交人工确认
- **单位声明栏改写**：标题栏写着 "inch" 的，改成「毫米」并注明"原图英寸，已换算"，
  避免出现「数值换成毫米、单位还写英寸」的矛盾文件
- **三种翻译通道**：本地术语库（免费离线）→ AI 接口（质量最好）→ 免费机翻（兜底）
- **界面**：无边框毛玻璃窗口、拖拽文件、深浅配色

## 安装

### 方式一：安装包（普通用户）

下载 `PDFTranslator_Setup_x.y.z.exe` 双击安装。装完桌面和开始菜单会有快捷方式，
控制面板里能正常卸载。

装完想确认环境没问题，可以在命令行跑一次自检（不联网、不花钱）：

```
"%LOCALAPPDATA%\Programs\PDF图纸翻译\PDF图纸翻译.exe" --selftest
```

结果会弹窗显示，同时写进 `%APPDATA%\PDF图纸翻译\data\selftest.log`。

### 方式二：源码运行（开发者）

```bash
git clone https://github.com/<your-name>/pdf-drawing-translator.git
cd pdf-drawing-translator
python -m pip install -r requirements.txt
python app.py
```

要求 Python 3.10+（开发用的是 3.14），Windows 上必须是带 tcl/tk 的完整安装版。

## 用起来

1. 把 PDF 拖进窗口（或点方框选文件），支持多选
2. 点「开始转换」
3. 每个 PDF 会生成一个同名文件夹，里面是这一套：

```
BR-01-Fence-Brace/
├── BR-01-Fence-Brace.pdf              源文件（复制一份进来，整个文件夹可以独立外发）
├── 【翻译】BR-01-Fence-Brace.pdf        正常工作用
├── 【标注】BR-01-Fence-Brace.pdf        核对用，被换算的数值标了黄
├── 【未译清单】BR-01-Fence-Brace.txt     没译出来的条目；一条没漏也会写「全部译出」
├── 【预览】BR-01-Fence-Brace.png        译文首页，不打开 PDF 也能先看一眼
└── 【数据校验清单】….txt               换算数据被判不准时才出现
```

重新跑同一个文件不会覆盖上一版，文件夹自动加 `_1`、`_2`。

## 配置翻译引擎

**这个仓库不带任何 API key，装完默认是空的。** 不配也能用：本地术语库 +
免费机翻（MyMemory，每天有配额）可以覆盖相当一部分短标注，但长句质量一般。

想要好效果，在窗口里点「设置」，填自己的 key。支持任何 OpenAI 兼容接口：

| 服务商 | base_url | 说明 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | 便宜，图纸翻译够用 |
| OpenAI | `https://api.openai.com/v1` | |
| 月之暗面 Kimi | `https://api.moonshot.cn/v1` | |
| 本地 Ollama | `http://localhost:11434/v1` | 不花钱，需要显存 |

key 只存在本机 `%APPDATA%\PDF图纸翻译\data\config.json`，不会上传到任何地方，
也不会进 git（`.gitignore` 已经排除了）。

## 英制换算规则

识别的写法（比想象中杂，图纸上这几种会混着出现）：

| 原文 | 结果 | 备注 |
|---|---|---|
| `4"` | `101.60mm` | |
| `1-1/2"` | `38.10mm` | 带整数的混合分数 |
| `1 1/2"` | `38.10mm` | 空格分隔 |
| `4''` | `101.60mm` | 两个单引号也是英寸，**不是** 4 英尺 |
| `2'-6"` | `762.00mm` | 英尺+英寸合写，整体算 |
| `9 ft` / `9英尺` | `2743.20mm` | |
| `25 lb` / `8 oz` | `11.34kg` / `0.227kg` | 质量统一到千克 |
| `R3-1/2"` | `R88.90mm` | 半径、直径、厚度前缀都认 |

格式固定为：尺寸毫米两位小数、质量千克两位小数（<1kg 给三位）。

**故意不换算的东西**（换了反而错件）：

- `11 ga` / `14 ga` —— 线规号，不是长度
- `1/4"-20 UNC`、`NPT 1/2"`、`BSP 3/4"` —— 螺纹标准件代号，国内按代号采购
- `M8×30`、`DN50`、`SCH40`、`Rev E`、`38°` —— 规格、角度、版本号
- 句子里的介词 `in`（`1 in the box` 不会被改成 `25.40mm`）

## 已知限制

- **只能翻译可选中文字**。扫描件、转成图片的图纸，文字是像素，没有 OCR 组件时翻不了
  （装了 `rapidocr-onnxruntime` 可以处理，但识别率不如原始文本）
- 中文图纸里的裸写 `12 in`（带空格、小写）不认 —— 英文句子里 `in` 太容易是介词，
  放开会把整句改烂。紧贴数字的 `4in`、带句点的 `4 in.`、全大写 `4 IN` 才认
- 图形极密处的小字，背景花纹复杂时不做填充，保留原文并记进未译清单
- 只处理 PDF，不碰 DWG / DXF

## 它是怎么做的

```
文字提取      按文字块聚类成段落，跨行合并（图纸上一段说明常被拆成十几个对象）
   ↓
语言判定      中→英 / 英→中 / 跳过
   ↓
翻译          本地术语库 → AI 接口（并发分批）→ 免费机翻 → 保留原文
   ↓
数字保真      译文里的数字必须和原文对得上，对不上就退回重译
   ↓
公制换算      正则识别英制写法，Decimal 精确换算，统一两位小数
   ↓
复算校验      拿原文独立算一遍，和译文比对；算错自动改，判不准的报警
   ↓
原位写回      擦掉原文，按原位置/角度/字号重排，字体子集化
```

设计上一个刻意的取舍：**换算不交给 AI**。模型算 3/8 英寸乘 25.4 会给你 9.5，
偶尔还会把 1-1/2 理解成 1.5 的一半。所有换算都在本地用 `Decimal` 做，
AI 只负责翻文字。

## 构建

```bash
python -m pip install pyinstaller
python -m PyInstaller packaging/PDFTranslator.spec --noconfirm

# 安装包需要 Inno Setup 6：https://jrsoftware.org/isdl.php
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

打包默认**不包含 OCR 引擎**（省一百多兆），也不包含任何用户配置。

## 贡献

Issue 和 PR 都欢迎。改换算规则的话请先跑一遍回归测试，里面固定了 64 条单位写法
和 25 条校验逻辑的用例：

```bash
python tools_test_metrics.py
```

## 许可证

[MIT](LICENSE)

## English

A desktop tool that translates engineering drawings (PDF) between Chinese and
English **in place** — the geometry, layout and font sizes stay untouched, only
the text is replaced.

- Recognises and converts imperial units to metric (`4"`, `1-1/2"`, `2'-0"`,
  `9 ft`, `25 lb`…), and verifies every conversion by recomputing it
  independently from the source text.
- Produces two PDFs per input: a clean translated one, and an annotated one with
  every converted value highlighted, so a workshop can check them one by one.
- Leaves thread codes (`1/4"-20`), gauge numbers (`11 ga`) and angles alone —
  converting those would order the wrong part.
- Bring your own API key (any OpenAI-compatible endpoint); works offline with a
  built-in glossary and a free fallback translator.

Windows only for now. Built with Python, Tkinter and PyMuPDF.
