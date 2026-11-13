# -*- coding: utf-8 -*-
"""
英文界面词表：中文原文 → English。

维护方式：代码里所有面向用户的字符串都写成 T("中文原文")，
这个字典按同一个中文原文查英文。缺条目时界面上会露一句中文，
用 tools_check_i18n.py 扫一遍就能找出漏了哪些。

约定：
  - 带占位符的条目用 {} 占位，与调用处的 fmt(...) 参数名对应；
  - 中文里用「」括起来的界面元素名，英文用 "..." 或直接不加引号；
  - 图纸单位、标准号（mm、kg、DN50、ANSI）不翻译。
"""

EN = {
    # ---------------- 输出文件与清单 ----------------
    "【翻译】": "[Translated] ",
    "【标注】": "[Marked] ",
    "【未译清单】": "[Untranslated] ",
    "【预览】": "[Preview] ",
    "【数据校验清单】": "[Data check] ",
    "_数据校验清单.txt": "_data-check.txt",
    "{name} 未译条目（共 {n} 条）": "{name} — untranslated items ({n})",
    "下面这些位置保留原文，没有译出。\n多为纯代号、人名、特殊符号或中英混排，硬翻反而毁图纸。\n\n":
        "The items below were left in the source language.\n"
        "Mostly part codes, personal names, symbols or mixed text — "
        "translating them would damage the drawing.\n\n",
    "第 {p} 页  {t}": "page {p}  {t}",
    "全部译出，没有未译条目。": "Everything was translated.",
    "曲线文字：导出时被转成了矢量轮廓，图上看得见、文字层里却没有，程序读不到，所以这些位置仍是原文。\n"
    "要改只能让制图方重新导出带文字的 PDF；\n"
    "或者用本程序的图像模式整页重做一遍。\n\n":
        "Outlined text: these characters were converted to vector outlines on export. "
        "They are visible on the drawing but absent from the text layer, so the program "
        "cannot reach them and they remain in the original language.\n"
        "To change them, ask the drafter to re-export a PDF with live text,\n"
        "or run this file through image mode, which redoes the whole page.\n\n",
    "   另有 {n} 处文字是曲线（转曲），文字层读不到，图上仍是原文：":
        "   {n} more item(s) are outlined text — not in the text layer, "
        "still in the original language on the drawing:",
    "     第{p}页：{t}": "     page {p}: {t}",
    "转曲文字不误报文字层": "outlined-text scan ignores live text",
    "OCR 组件不可用，跳过": "OCR unavailable, skipped",
    "{name} 计量数据校验清单": "{name} — measurement check report",
    "怎么读这份清单：\n  已纠正 —— 程序按原文重新算过并改正的，不用处理，核对一眼即可；\n  待确认 —— 程序判不准（多半是同句里数量对不上），请对照原图核实。\n\n":
        "How to read this report:\n"
        "  Corrected — recomputed from the source and fixed automatically. "
        "No action needed, just glance at it.\n"
        "  Needs review — the program could not decide (usually a value count mismatch "
        "in the same sentence). Check it against the original drawing.\n\n",
    "已纠正": "Corrected",
    "待确认": "Needs review",
    "第{p}页": "page {p}",
    "原文：{s}": "Source: {s}",
    "原译：{o}": "Output: {o}",
    "；": "; ",

    # ---------------- 校验清单明细 ----------------
    "质量": "mass",
    "长度": "length",
    "小数位不符合规定位数，已统一": "Decimal places were off — normalized",
    "有英制数值没换算，已补算": "Imperial values were not converted — converted now",
    "有公制数值没换算，已补算": "Metric values were not converted — converted now",
    "原文有 {n} 处{kind}数值，译文里找不到对应数值（应为 {v} 等），需人工确认":
        "Source has {n} {kind} value(s); none found in the output "
        "(expected {v} etc.) — please verify",
    "本句{kind}数量对不上（原文 {a} 处 / 译文 {b} 处），可能有数值被漏算或合并，需人工核对":
        "Value count mismatch in this sentence ({kind}: {a} in source / {b} in output); "
        "some values may be missing or merged — please check",
    "译文 {got} 与复算值 {exp} 不符，未自动修改":
        "Output {got} does not match the recomputed {exp}; left unchanged",
    "{got} → {new}（按原文 {src} 复算）": "{got} → {new} (recomputed from {src})",
    "单位声明": "unit declaration",

    # ---------------- 状态条 / 翻译来源 ----------------
    "AI 接口": "AI API",
    "AI 接口：": "AI API: ",
    "已启用": "enabled",
    "未配置": "not configured",
    "AI 接口调用失败：": "AI API call failed: ",
    "AI 有回应，但没有一条通过质检：": "The model answered, but nothing passed quality checks: ",
    " · 余额 ¥{v:.2f}": " · balance ¥{v:.2f}",
    " —— 只能靠本地术语库 + 免费机翻，长句译不了多少，且免费机翻每天有配额、容易失败。":
        # 这条文案只占状态条的一行（控件高度固定两行，第二行留给「怎么解决」）。
        # 英文一旦写长就会挤成两行，把下面那句提示顶掉 —— 所以刻意压短。
        " — only the local glossary and free MT are available; free MT has a daily quota.",
    "点右上角「设置」填自己的 key 就能解决。":
        "Add your own key under \"Settings\" (top right) to fix this.",
    "准备就绪。把 PDF 图纸拖进来，或点击上方区域选择文件。":
        "Ready. Drop a PDF drawing here, or click the area above to pick one.",
    "把 PDF 图纸拖到这里": "Drop PDF drawings here",
    "也可以点这里选择文件": "or click here to pick files",
    "点击选择文件": "Click to pick a file",
    "支持矢量图纸与含位图标注的图纸":
        "Works with vector drawings and drawings with bitmap callouts",
    "还没有添加文件\n把 PDF 图纸拖进上面的方框，或点它选择":
        "No files yet\nDrop PDF drawings into the box above, or click it",
    "待翻译文件（{n}）": "Files to translate ({n})",
    "清空": "Clear",
    "清空列表": "Clear list",
    "要移除列表里的 {n} 个文件吗？\n只是从列表里拿掉，磁盘上的 PDF 不会被删除。":
        "Remove the {n} file(s) from the list?\nThey are only taken off the list — "
        "the PDFs on disk are not deleted.",
    "已加入 {n} 个文件。": "Added {n} file(s).",
    "正在转换，等这一轮跑完再拖文件进来。":
        "A conversion is running — drop files in once this round finishes.",
    "已换成刚拖入的 {n} 个文件，原来列表里的 {d} 个已移出"
    "（磁盘上的 PDF 没动）。要接着翻那几份，"
    "用「选择文件」加回来。":
        "Switched to the {n} file(s) you just dropped; {d} file(s) were taken off "
        "the list (the PDFs on disk are untouched). Use the file picker to add "
        "those back if you still need them.",
    "选择 PDF 图纸": "Choose PDF drawings",
    "PDF 文件": "PDF files",

    # ---------------- 方向 / 单位 / 输出 ----------------
    "翻译方向": "Direction",
    "输出单位": "Output units",
    "输出到": "Output to",
    "源文件旁（每份一个文件夹）": "next to the source (one folder each)",
    "选择目录": "Choose folder",
    "选择输出目录": "Choose output folder",
    "开始转换": "Start",
    "转换中，请稍候…": "Working, please wait…",
    "还没有文件。先拖一份 PDF 进来。": "No files yet. Drop a PDF in first.",
    "开始处理 {n} 个文件": "Processing {n} file(s)",
    "提示：没有配置 AI 接口，长句只能交给免费机翻；它每天有配额，用完后该句保留原文。":
        "Note: no AI API configured. Long sentences fall back to the free machine "
        "translator, which has a daily quota — once used up, those lines stay in the source language.",
    "公制（毫米·千克）": "Metric (mm · kg)",
    "英制（英寸·磅）": "Imperial (in · lb)",
    "保持原样，不换算": "No conversion",
    "自动（{lang}）": "Auto ({lang})",
    "自动识别": "Auto-detect",

    # ---------------- 处理日志 ----------------
    "   共 {p} 页 · 可提取文字 {c} 个字符 · 位图 {i} 张":
        "   {p} page(s) · {c} extractable characters · {i} image(s)",
    "   注意：这份文件几乎没有可选中的文字（疑似扫描件），将只依赖位图 OCR，效果可能有限。":
        "   Warning: almost no selectable text (probably a scan). Only bitmap OCR applies — "
        "results may be limited.",
    "   输出文件夹：{p}": "   Output folder: {p}",
    "   无法创建输出文件夹：{p}\n     {e}: {m}\n     请换一个输出目录（点「选择目录」）后重试。":
        "   Cannot create the output folder: {p}\n     {e}: {m}\n"
        "     Pick another output folder (\"Choose folder\") and retry.",
    "   完成：{a}/{b} 条已译 · 段落合并 {c} 处 · 位图内文字 {d} 条":
        "   Done: {a}/{b} items translated · {c} paragraphs merged · "
        "{d} text(s) inside images",
    "   标注版没能生成（{e}）。翻译版是好的，可以正常用。":
        "   The marked copy could not be created ({e}). The translated PDF is fine.",
    "   标注版：{n} 处换算过的数值已标黄":
        "   Marked copy: {n} converted value(s) highlighted",
    "   输出单位选了「保持原样」，未生成标注版。":
        "   Output units set to \"keep as-is\" — no marked copy generated.",
    "   这份文件没有可换算的数值，未生成标注版（两版内容会完全一样）":
        "   Nothing to convert in this file — no marked copy (both copies would be identical).",
    "   预览图没能生成（{e}），不影响其它文件。":
        "   Preview image could not be rendered ({e}); other files are unaffected.",
    "   单位声明栏已改写 {n} 处：{a}，并注明{a2}":
        "   Unit declaration rewritten in {n} place(s): {a}, with a note that {a2}",
    "改成毫米": "millimetres",
    "改成英寸": "inches",
    "原图英寸已换算": "the original inches were converted",
    "原图毫米已换算": "the original millimetres were converted",
    "     未译 第{p}页：{t}": "     untranslated, page {p}: {t}",
    "     …另有 {n} 处，清单见文件夹内「{f}」":
        "     …{n} more; see \"{f}\" in the output folder",
    "      判不准的条目已写入 {f}，请对照原图核实后再下料。":
        "      Items that need a human eye were written to {f}. "
        "Check them against the original before cutting material.",
    "翻译来源：本地术语库 {d} · AI 模型 {l} · 免费机翻 {o} · 缓存命中 {c} · 保留原文 {f}":
        "Sources: local glossary {d} · AI model {l} · free MT {o} · cache hits {c} · "
        "left as-is {f}",
    "转换出错：": "Conversion failed: ",
    "     请到「设置」里点「测试连接」查看具体原因。":
        "     Open Settings and click \"Test connection\" to see the actual reason.",
    "     这些条目已保留原文。多为译文里残留了英文、或数字对不上，明细见输出文件夹内的「未译清单」。":
        "     Those items were left in the source language — usually leftover English "
        "in the translation or mismatched numbers. Details are in the untranslated list.",
    "{n} 条没能译好，已保留原文 —— 多为纯代号、特殊符号或混排内容，硬翻反而毁图纸。明细见输出文件夹内的「未译清单」。":
        "{n} item(s) could not be translated and were left as-is — mostly pure codes, "
        "symbols or mixed text where translating would damage the drawing. "
        "Details are in the untranslated list.",
    "免费在线翻译（MyMemory）今日配额已用完 —— 它是没配 AI 接口时的备用机翻，配额按天重置。配好 AI 接口后就不再依赖它。":
        "The free online translator (MyMemory) has used up today's quota. It is the "
        "fallback when no AI API is configured, and the quota resets daily. "
        "Configure an AI API to stop depending on it.",
    "{a} 条本地解决，{b} 条需联网/AI": "{a} handled locally, {b} need network/AI",

    # ---------------- 完成 ----------------
    "没有产出文件。可能是 PDF 里没有可翻译的文字。":
        "Nothing was produced. The PDF probably has no translatable text.",
    "全部完成，{n} 套图纸已生成：": "All done — {n} drawing set(s) generated:",
    "（没有可换算的数值，标注版与翻译版完全一样，未单独生成）":
        "    (nothing to convert; the marked copy would be identical, so it was skipped)",
    "（输出单位选了「保持原样」，未生成标注版）":
        "    (output units set to \"keep as is\", so no marked copy was produced)",
    "（图像模式不生成标注版：原文文字是曲线，定位不到数值位置）":
        "    (image mode does not produce a marked copy: the original text is curves, "
        "so individual values cannot be located)",
    "   图像模式不生成标注版：原文文字是曲线，定位不到每个数值的位置。":
        "   Image mode does not produce a marked copy: the original text is drawn as "
        "curves, so individual values cannot be located.",
    # 结尾的复核提醒。英文刻意说得更直白：把「能赔钱」这件事讲明白，
    # 比一句 "please review" 更有分量，也是这类工具该有的诚实。
    "⚠请逐处复核译文和单位换算后再使用：机器翻译和自动换算都可能出错，直接拿去做报价、下单或加工可能造成损失。":
        "⚠ Check every translated line and converted value before you use this: machine "
        "translation and automatic conversion can both be wrong, and quoting, ordering or "
        "machining straight from the output can cost you real money.",

    # ---------------- 计量校验摘要 ----------------
    "计量数据校验：本份文件没有可换算的数值":
        "Measurement check: nothing to convert in this file",
    "计量数据校验：复算 {n} 条": "Measurement check: {n} item(s) recomputed",
    " · 自动纠正 {n} 处": " · {n} auto-corrected",
    " · 需人工确认 {n} 处": " · {n} need review",
    " · 全部一致": " · all consistent",

    # ---------------- 质检拒稿原因 ----------------
    "模型返回空": "empty response",
    "原样返回未翻译": "returned unchanged",
    "乱码": "garbled text",
    "中英混杂": "mixed languages",
    "残留英文": "leftover source language",
    "数字对不上": "number mismatch",
    "其他": "other",
    "模型返回的内容无法解析为译文表": "the model's reply could not be parsed as a translation list",
    "只输出 JSON 数组，不要任何其他文字。": "Output only the JSON array, nothing else.",

    # ---------------- 设置窗 ----------------
    "设置": "Settings",
    "外观": "Appearance",
    "保存并关闭": "Save and close",
    "恢复默认": "Reset to defaults",
    "服务商": "Provider",
    "接口地址": "Base URL",
    "以 /v1 结尾": "ends with /v1",
    "模型名": "Model",
    "可下拉选": "pick from list",
    "显示": "Show",
    "每批条数": "Items per batch",
    "1–40，越大越省请求": "1–40, higher saves requests",
    "密钥只保存在本机配置文件里，不会上传到任何地方。":
        "The key is stored only in the local config file. It is never uploaded anywhere.",
    "填好上面的三项，点下面的「测试连接」先确认能通。\n常见错误：401 密钥无效 / 402 余额不足 / 404 地址或模型名写错。":
        "Fill in the three fields above, then click \"Test connection\" to verify.\n"
        "Common errors: 401 invalid key / 402 insufficient balance / "
        "404 wrong base URL or model name.",
    "还没有 API Key？怎么拿": "No API key yet?",
    "打开申请页面": "Open signup page",
    "配置文件：{p}": "Config file: {p}",
    "测试连接": "Test connection",
    "查询余额": "Check balance",
    "测试中…": "Testing…",
    "查询中…": "Checking…",
    "正在测试连接…（一般 1–5 秒）": "Testing the connection… (usually 1–5 s)",
    "正在查询余额…（一般 1–5 秒）": "Checking the balance… (usually 1–5 s)",
    "测试连接时出错了": "Connection test failed",
    "查询余额时出错了": "Balance check failed",
    "✓ 连接正常": "✓ Connected",
    "，往返 {v:.1f} 秒": ", round trip {v:.1f}s",
    "连接失败": "Connection failed",
    "确认无误后点「保存并关闭」，配置才会写进文件。":
        "Click \"Save and close\" to write the settings to disk.",
    "接口一共报了 {n} 个模型，点「模型名」右侧的 ▾ 可以直接选。":
        "The endpoint reported {n} models. Use the ▾ next to \"Model name\" to pick one.",
    "按这个顺序排查：\n① key 是否复制完整，前后有没有多带空格；\n② 接口地址是否以 /v1 结尾（末尾别再带 /chat/completions）；\n③ 模型名大小写和横杠是否和文档一致；\n④ 换服务商时 key 也要换，各家密钥不通用。":
        "Check in this order:\n"
        "1) Is the key complete, with no leading/trailing spaces?\n"
        "2) Does the base URL end at /v1 (no trailing /chat/completions)?\n"
        "3) Does the model name match the docs exactly, including case and hyphens?\n"
        "4) Keys are per-provider — switching provider means switching the key.",
    "等了 {n} 秒还没收到回应，多半是网络不通或接口地址不对。\n可以先把这个地址复制到浏览器里打开试试：能打开说明地址通，打不开就是网络或代理的问题。\n国内网络访问 api.openai.com 通常不通，换 DeepSeek 或硅基流动即可。":
        "No response after {n} seconds — most likely the network is blocked or the base URL "
        "is wrong.\nTry opening the base URL in a browser: if it loads, the address is fine and "
        "the problem is the network or a proxy.\nOn mainland Chinese networks api.openai.com is "
        "usually unreachable; use DeepSeek or SiliconFlow instead.",
    "已切到自定义接口。地址填到 /v1 为止，模型名照对方文档抄；\n本机跑的 ollama / vLLM 也能接。":
        "Switched to a custom endpoint. Enter the base URL up to /v1 and copy the model name "
        "from their docs.\nLocal ollama / vLLM works here too.",
    "已带入「{name}」的接口地址和默认模型。\n接下来把 API Key 粘进去，点「测试连接」验证。":
        "Loaded the base URL and default model for {name}.\n"
        "Now paste your API key and click \"Test connection\".",
    "这个服务商没有预置模型名，按文档手动填写即可。":
        "No preset models for this provider — type the name from their docs.",
    "已在浏览器打开：{u}\n申请到密钥后粘到上面的 API Key 里。":
        "Opened in your browser: {u}\nPaste the key into the API Key field above.",
    "没能自动打开浏览器，请手动访问：{u}":
        "Could not open a browser automatically. Please visit: {u}",

    # ---------------- 外观页 ----------------
    "界面语言": "Interface language",
    "切换后立即生效，不用重启": "applies immediately, no restart",
    "语言": "Language",
    "主题配色": "Colour theme",
    "底色、卡片、描边、强调色一起换": "background, cards, borders and accent together",
    # 七套配色的名字。名字本身是意象，直译比音译好认：
    # 「墨松」翻成 "Mo Song" 没人知道是什么颜色
    "深空蓝": "Deep Space",
    "午夜紫": "Midnight Violet",
    "冰青": "Ice Cyan",
    "墨松": "Pine Green",
    "暖沙": "Warm Sand",
    "石墨灰": "Graphite",
    "晨雾白": "Morning Mist",
    "字体与字号": "Font and size",
    "字体": "Font",
    "字号": "Size",
    "8 – 16，默认 10": "8 – 16, default 10",
    "窗口透明度": "Window opacity",
    "调低能和背后的窗口叠着看": "lower it to see through to windows behind",
    "透明度": "Opacity",
    "不透明": "Opaque",
    "轻微": "Slight",
    "明显": "Noticeable",
    "通透": "Clear",
    "完成提示音": "Finish sound",
    "提示音": "Sound",
    "柔和提示音": "Soft",
    "系统提示音": "System",
    "不响": "Silent",
    "预览": "Preview",
    "选配色和字号时会跟着变": "follows the theme and font size",
    "把 PDF 图纸拖到这里\n翻译后文字位置保持原样，只换文字不换版式":
        "Drop PDF drawings here\nText keeps its position — only the words change",

    # ---------------- 启动与自检 ----------------
    "PDF 图纸翻译": "PDF Drawing Translator",
    "PDF 图纸翻译 · 启动失败": "PDF Drawing Translator · Startup failed",
    "PDF 图纸翻译 · 自检": "PDF Drawing Translator · Self-test",
    "详细信息已写入：": "Details written to:",
    "这个 Python 没有图形界面组件 tkinter，程序无法显示窗口。\n\n解决办法：用程序目录里的「启动.bat」启动，它会自动挑一个带 tkinter 的 Python；或者安装 python.org 的完整版 Python，安装时勾选 tcl/tk 组件。":
        "This Python build has no tkinter GUI component, so no window can be "
        "shown.\n\nFix: start the program with 启动.bat (it picks a Python that "
        "has tkinter), or install the full python.org build with the tcl/tk "
        "component ticked.",
    "缺少运行所需的组件：": "Missing required components: ",
    "解决办法：打开命令行执行以下命令安装依赖后重试：":
        "Fix: run the following in a terminal, then try again:",
    "程序启动时出错，窗口无法打开。": "The program failed to start; the window cannot open.",
    "通过": "PASS",
    "失败": "FAIL",
    "数据目录可写": "Data folder writable",
    "配置读写": "Config read/write",
    "API key 独立保存": "API key kept separately",
    "加密后不含明文，存放在数据目录之外":
        "encrypted, no plaintext on disk, stored outside the data folder",
    "系统中文字体": "System Chinese font",
    "输出前复核": "Pre-output review",
    "复核打回重译": "Review reject → retranslate",

    # v1.0.9：复核不再拦文件，改为出图 + 清单
    "   文件已生成，上面这些位置保留了原文，清单也写进了输出文件夹。":
        "   The file was produced. The spots above keep the original text; "
        "the same list is in the output folder.",
    "复核发现的位置：": "Spots flagged by the review:",
    "文字层折行": "Text-layer line wrapping",
    "复核不过也出文件": "Review failure still produces a file",
    "   一条都没有译成，输出的文件仍是原文，清单见文件夹内「未译清单」。":
        "   Nothing was translated — the output still reads as the source. "
        "See the untranslated list in the output folder.",
    "   没有找到可翻译的文字，输出的文件与原件内容相同。":
        "   No translatable text was found — the output is identical to the original.",
    "没有产出文件。原因见上方日志：若显示转换出错，请把该段内容发给开发者。":
        "No file was produced. See the log above for the reason; if it says "
        "conversion failed, send that section to the developer.",
    "不可译内容放行": "Untranslatable content allowed",

    # v1.1.0：复核记账、划账、软问题（数值个数对不上）与声明格回读
    "复核账本不串旧账": "Metric ledger drops stale entries",
    "未译清单不误划": "Untranslated list not over-pruned",
    "数值个数异常要重译": "Number-count mismatch triggers retranslation",
    "声明格写入可回读": "Unit-declaration cell can be read back",
    "英寸的裸写法": "Bare inch abbreviation (50 in wide)",
    "标注版认中文单位": "Annotated copy recognises Chinese units",
    "   其中 {n} 处格子太窄，只写了单位、没放下"
    "「原图已换算」那行小字。":
        "   In {n} of them the cell was too narrow for the "
        "\"converted from\" note; only the unit itself was written.",
    "英文词表已载入": "English dictionary loaded",
    "读取 PDF": "Read PDF",
    "换算": "convert",
    "换算 公制 → 英制": "convert metric → imperial",
    "换算 25.40mm → 1 英寸": "convert 25.40mm → 1 inch",
    "生成预览图": "Render preview",
    "端到端流程": "End-to-end flow",
    "一个都没找到": "none found",
    "没生成": "not generated",
    # 自检新增的两项：英制输出的整链路、日韩字形覆盖
    "公制图纸转英制": "Metric drawing to imperial",
    "英制输出的单位声明已改写": "Unit field rewritten for imperial output",
    "日文·韩文字形覆盖": "Japanese / Korean glyphs available",
    "本机部署免密钥": "Local model needs no API key",
    "目标语言自动＝系统语言": "Target Auto = system language",
    "界面语言默认跟随系统": "UI language follows the system by default",
    "改写 {n} 处": "{n} rewritten",
    "{n} 条": "{n} entries",
    "自检全部通过 —— 这个版本可以正常用。":
        "All checks passed — this build is good to use.",
    "自检发现问题，请把下面内容发给开发者。":
        "The self-test found problems. Please send the following to the developer.",
    "数据目录：{p}": "Data folder: {p}",

    # ---- 本机部署与接口报错 ----
    # 这一批原来全是硬编码中文，英文界面下用户看到的是一排看不懂的字，
    # 而它们恰好都出现在「出错的时候」——最需要看懂的时候。
    "本机部署": "Local model",
    "API Key": "API Key",
    "参数": "parameters",
    "请先填写 API Key": "Fill in the API Key first",
    "请先填写{miss}": "Fill in {miss} first",
    "AI 接口未配置完整：缺少{miss}": "The AI endpoint is incomplete: missing {miss}",
    "接口能连通，但没有填写模型名": "The endpoint is reachable, but no model name is filled in",
    "连接正常，该接口共 {n} 个模型可用。":
        "Connected. The endpoint reports {n} models.",
    "连接正常，已用小请求确认模型能正常返回内容。":
        "Connected. A small test request confirmed the model answers.",
    "接口能通，但模型没有返回任何内容。多半是推理型模型（如 deepseek-v4-pro / o 系列）把 token 都用在思考上了，换个 chat 系列的模型再试。":
        "The endpoint works but the model returned nothing. Reasoning models "
        "(deepseek-v4-pro, the o series) often spend every token thinking — "
        "switch to a chat model and try again.",
    "\n服务商的模型列表里没有 {m}，但实测能正常调用，可以继续用。":
        "\nThe model list does not include {m}, but a real call answered fine, "
        "so you can keep using it.",
    "已自动填入「{m}」，要换别的点 ▾ 选。":
        "Filled in \"{m}\" automatically — use the ▾ list to choose another one.",
    "本机部署不需要密钥，这里留空即可。文字只在这台机器内部传递，不经过互联网。":
        "A local model needs no key — leave this empty. Your text moves inside this "
        "machine only and never reaches the internet.",
    "已切到本机部署。API Key 留空，直接点「测试连接」——\n能读到本机有哪些模型，会自动帮你填进「模型名」。\n服务没启动的话先执行 ollama serve。":
        "Switched to a local model. Leave API Key empty and press Test connection —\n"
        "the models installed on this machine are read back and filled in for you.\n"
        "If that fails, start the server first: ollama serve.",
    "本机部署按这个顺序排查：\n① 模型服务是否在跑（命令行 ollama serve，或 LM Studio 里打开本地服务器）；\n② 地址和端口是否和服务启动日志一致，默认 http://127.0.0.1:11434/v1；\n③ 模型名是否真的 pull 过（ollama list 能看到才算）。":
        "For a local model, check in this order:\n"
        "1) Is the model server running (ollama serve, or the local server switched on in LM Studio)?\n"
        "2) Do the URL and port match its startup log? The default is http://127.0.0.1:11434/v1.\n"
        "3) Has the model actually been pulled? ollama list has to show it.",
    "这是本机部署的模型，不产生费用，没有余额可查。消耗的只是你自己的电和显卡。":
        "This model runs on your own machine, so there is no balance to check — "
        "it costs you electricity and GPU time, nothing else.",
    "本机模型响应超时。大模型在本机跑本来就慢，把「每批条数」调小到 10 左右，或换一个更小的模型再试。":
        "The local model timed out. Big models are slow on your own hardware — "
        "drop items per batch to about 10, or try a smaller model.",
    "连不上本机模型服务（{url}）：{reason}。\n先确认服务在跑：命令行执行 ollama serve，或打开 LM Studio 的本地服务器；地址和端口要与它的启动日志一致。":
        "Cannot reach the local model server ({url}): {reason}.\n"
        "Start it first — run ollama serve in a terminal, or switch on the local server "
        "in LM Studio. The URL and port must match its startup log.",
    "{name}没有开放余额查询接口，只能按消耗估算。":
        "{name} exposes no balance API, so cost can only be estimated from usage.",
    "密钥无效或已过期（401）。请检查 API Key 是否复制完整。":
        "The key is invalid or expired (401). Check that the whole API key was pasted.",
    "账户余额不足（402）。请先到服务商后台充值。":
        "Insufficient balance (402). Top up in the provider's console first.",
    "没有权限访问该模型（403）。可能是密钥权限或模型未开通。":
        "No permission for this model (403). Check the key's scope or whether the model is enabled.",
    "接口地址或模型名不存在（404）。请核对「接口地址」和「模型」。":
        "The base URL or model name does not exist (404). Check both fields.",
    "请求过于频繁或额度用尽（429）。请稍后再试。":
        "Too many requests or quota exhausted (429). Try again later.",
    "服务商暂时不可用（{code}），请稍后重试。":
        "The provider is temporarily unavailable ({code}). Try again later.",
    "接口返回错误 {code}：{detail}": "The endpoint returned error {code}: {detail}",
    "网络连接失败：{reason}。请检查网络、代理或接口地址是否正确。":
        "Network connection failed: {reason}. Check your network, proxy and base URL.",
    "请求超时。可以换一个更快的服务商，或减少单次翻译条数。":
        "The request timed out. Try a faster provider, or send fewer items per batch.",
    "请求失败：{name} {msg}": "Request failed: {name} {msg}",

    # ---------------- 文字已转曲线（CAD 导出） ----------------
    "   这份 PDF 的文字已转成曲线（CAD 导出时勾了「文字转曲」），提取不到可编辑文字，共 {c} 个字符。":
        "   The text in this PDF has been converted to outlines (a CAD export "
        "option), so no editable text can be extracted: {c} characters.",
    "   直接翻译不会有任何变化。要拿到干净译文，请让制图方重新导出带文字的 PDF。":
        "   Translating it will change nothing. For a clean result, ask whoever "
        "produced the drawing to re-export the PDF with text kept as text.",
    "   已跳过。图像模式所需的 OCR 组件加载失败，建议重新安装本程序。":
        "   Skipped. The OCR component required by image mode failed to load. "
        "Reinstalling the application should fix it.",
    "   已跳过这份文件。": "   Skipped this file.",
    "   未发现可翻译的文字，这份文件没有生成有效译文。":
        "   No translatable text was found. No usable translation was produced.",
    "   这份文件没有生成。处理上述问题后重跑即可。":
        "   No file was produced. Fix the issues above and run again.",
    " · 参考提示 {n} 处": " · {n} reference notes",
    "文字已转成曲线": "Text converted to outlines",
    "转曲 PDF 识别": "Detect outlined PDF",
    "OCR 文字语言判定": "OCR text language detection",
    "OCR 引擎": "OCR engine",
    "这份 PDF 的文字是图形，没有可编辑的文字层，常规翻译不会有任何变化。\n\n"
    "是否用图像模式尝试：整页识别，盖掉原文再写译文？\n"
    "效果有限：字号会被压缩，个别位置可能压到线框。\n\n"
    "更好的办法是让制图方重新导出带文字的 PDF。":
        "The text in this PDF is drawn as graphics: there is no editable text "
        "layer, so a normal translation changes nothing.\n\n"
        "Try image mode? Each page is recognised, the original is covered, then "
        "the translation is written in.\n"
        "Results are limited: font sizes get compressed and some labels may "
        "cover drawing lines.\n\n"
        "The better option is to ask for a PDF re-exported with text as text.",
}
