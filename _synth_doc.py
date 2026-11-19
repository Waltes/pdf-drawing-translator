# -*- coding: utf-8 -*-
"""造一份「通用文档」样本，用来验 general 模式（非图纸）。

为什么要造：手头 9 份客户图纸全是图纸，测不到「说明书/合同/规范」这类内容。
general 模式要看的恰恰是这些 —— 句子是否完整、单位是否被强行换算、
图纸术语有没有被套上去。

样本内容刻意包含四类容易出错的地方：
  1. 带「米」的长度（图纸模式会改成 2790mm）
  2. 带「寸」的词（图纸模式会当管径转 DN）
  3. 完整段落（图纸模式会被压成电报体）
  4. 图纸模式会缩写的词（thickness / diameter / approximately）

跑法：
    python _synth_doc.py             # 造样本并译一份 general 版
    python _synth_doc.py make        # 只造样本
"""
import os
import sys

sys.path.insert(0, ".")

import pymupdf

FONT = r"C:\Windows\Fonts\msyh.ttc"
OUT = "_doc"

PARAS = [
    ("h", "使用说明书"),
    ("p", "一、适用范围"),
    ("p", "本产品适用于室内外环境的货物搬运与临时存放，工作温度范围为 "
          "-10℃ 至 45℃。请勿在强腐蚀性气体环境中使用。"),
    ("p", "二、安装"),
    ("p", "1. 安装前请仔细阅读本说明书，确认所有配件齐全。"),
    ("p", "2. 展开框架后，将输送带沿导轨平铺，总长度约 2.79 米，宽度 600 毫米。"),
    ("p", "3. 四角立柱采用 1.5 寸钢管，壁厚 2.5 毫米，插入底座后锁紧。"),
    ("p", "三、技术参数"),
    ("p", "额定承重：150 公斤；自重：约 38 公斤；折叠后厚度约 120 毫米。"),
    ("p", "表面处理采用热浸镀锌，镀层厚度不小于 65 微米，不得有漏镀、起皮。"),
    ("p", "四、维护"),
    ("p", "建议每三个月检查一次紧固件，发现松动应立即拧紧。"
          "长期停用时请置于干燥通风处，approximately 每半年做一次防锈处理。"),
    ("p", "本说明书如有变更，恕不另行通知。2026年11月16日"),
]


def make(path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)      # A4
    # 中文字距：insert_text 走的是简单字体接口，CJK 字符会按西文宽度铺开，
    # 渲染出来是「2 . 7 9 米」这种散架样。TextWriter 走的是文本对象，
    # 用字体自己的字宽，字距才对。这份样本要给 OCR 读，字距错了
    # 每个字会被识别成独立框，版面数据全乱，测出来的问题都是假的。
    font = pymupdf.Font("china-s")
    tw = pymupdf.TextWriter(page.rect)
    y = 80
    for kind, text in PARAS:
        size = 18 if kind == "h" else 11
        per = 24 if kind == "p" else 12
        chunks = [text[i:i + per] for i in range(0, len(text), per)]
        for ch in chunks:
            tw.append((70, y), ch, font=font, fontsize=size)
            y += size * 1.75
        y += 6
    tw.write_text(page, color=(0, 0, 0))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def run(src, doc_type):
    import json
    from core import pdf_engine
    from core.translate import Translator, load_config

    CFG = r"C:\Users\stanl\AppData\Roaming\PDFDrawingTranslator\data\config.json"
    with open(CFG, encoding="utf-8") as f:
        cfg = load_config()
        cfg.setdefault("llm", {})["api_key"] = json.load(f)["llm"]["api_key"]
    cfg.setdefault("translate", {})["src"] = "zh"
    cfg["translate"]["dst"] = "en"
    cfg["translate"]["doc_type"] = doc_type
    cfg["translate"]["units"] = "keep" if doc_type == "general" else "metric"
    tr = Translator(cfg)

    pairs = []

    def rec_batch(items):
        res = tr.translate_many(items)
        for (t, _l), o in zip(items, res):
            pairs.append((t, o))
        return res

    def rec_one(text, lang):
        o = tr.translate_many([(text, lang)])[0]
        pairs.append((text, o))
        return o

    dst = os.path.join(OUT, "doc_%s.pdf" % doc_type)
    stats = {}
    pdf_engine.process_pdf(
        src, dst, rec_one, translate_batch=rec_batch, stats_out=stats,
        unit_mode=("keep" if doc_type == "general" else "metric"),
        render_preview=False, ocr_full_pages=False,
        dst_lang=tr.dst_lang, reviewer=tr, doc_type=doc_type)

    print("\n===== %s 模式 -> %s =====" % (doc_type, dst))
    for s, o in pairs:
        print("  %s\n    -> %s" % (s[:52].replace("\n", " "), o[:110]))
    print("  未译：", stats.get("untranslated"))
    return dst


if __name__ == "__main__":
    src = make(os.path.join(OUT, "doc_src.pdf"))
    print("样本：", src)
    if len(sys.argv) == 1 or sys.argv[1] != "make":
        for dt in ("general", "drawing"):
            run(src, dt)
