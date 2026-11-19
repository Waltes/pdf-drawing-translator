# -*- coding: utf-8 -*-
"""通用版（standard）专属体检：验证「不做全大写」这条开关。

和 _batch.py 的分工：
    _batch.py      固化在定制版跑（切 edition.json + 校验锁定词 + 严格版式）
    _std_check.py  强制通用版跑，只看大小写行为，输出逐条译文

为什么必须单独一份脚本：
    _batch.py 一进来就把 edition.json 换成 dynamic.json，跑出来的全是全大写，
    用它验通用版等于验错版本 —— 这类「测的是另一个版」的错已经犯过一次
    （1.1.4 收尾时禁用词报了 11 处，其实是 edition 停在 standard）。

用法：
    python _std_check.py              # 默认跑托盘 11-19 + 合成验收件
    python _std_check.py 路径.pdf      # 指定文件（中→英）
"""
import json
import os
import sys

sys.path.insert(0, ".")

import core.edition as EDITION
import core.translate as T
from core import pdf_engine
from core.translate import Translator, load_config

CFG = r"C:\Users\stanl\AppData\Roaming\PDFDrawingTranslator\data\config.json"
OUTDIR = "_std"
D = r"C:\Users\stanl\Desktop" + "\\"

FILES = [
    ("tuopan1119", D + "无侧板分体地毯托盘示意图2026-11-19.pdf"),
    ("accept", os.path.join("_synth", "验收件_源.pdf")),
]


def upper_ratio(text):
    """字母里大写的占比。用来判「这条是不是被强行大写了」"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def run_one(name, src_path):
    if not os.path.exists(src_path):
        print("跳过（文件不存在）：%s" % src_path)
        return None
    os.makedirs(OUTDIR, exist_ok=True)
    out_pdf = os.path.join(OUTDIR, name + ".pdf")
    T.CACHE_PATH = os.path.join(OUTDIR, "_cache_%s.json" % name)
    if os.path.exists(T.CACHE_PATH):
        os.remove(T.CACHE_PATH)

    with open(CFG, encoding="utf-8") as f:
        cfg = load_config()
        cfg.setdefault("llm", {})["api_key"] = json.load(f)["llm"]["api_key"]
    cfg.setdefault("translate", {})["src"] = "zh"
    cfg["translate"]["dst"] = "en"
    tr = Translator(cfg)

    pairs = []

    def rec_batch(items):
        res = tr.translate_many(items)
        for (txt, _lang), out in zip(items, res):
            pairs.append((txt, out))
        return res

    def rec_one(text, lang):
        out = tr.translate_many([(text, lang)])[0]
        pairs.append((text, out))
        return out

    stats = {}
    pdf_engine.process_pdf(
        src_path, out_pdf, rec_one, translate_batch=rec_batch,
        stats_out=stats, unit_mode="metric", render_preview=False,
        ocr_full_pages=True, dst_lang=tr.dst_lang, reviewer=tr)

    # 全大写条数：占比 > 0.9 且字母数 ≥ 3 才算「整条被大写」，
    # 纯缩写（PVC、ISO）本来就该全大写，不能用 1.0 硬判
    allup = [(s, o) for s, o in pairs
             if len([c for c in o if c.isalpha()]) >= 3 and upper_ratio(o) > 0.9]
    mixed = [(s, o) for s, o in pairs
             if len([c for c in o if c.isalpha()]) >= 3 and 0.0 < upper_ratio(o) <= 0.9]
    print("\n=== %s | 共 %d 条 | 整条全大写 %d | 正常大小写 %d"
          % (name, len(pairs), len(allup), len(mixed)))
    print("--- 前 20 条译文 ---")
    for s, o in pairs[:20]:
        print("  %-40r -> %r" % (s[:38], o[:70]))
    if allup:
        print("--- 仍被全大写的条目（若通用版，应尽量为 0）---")
        for s, o in allup[:12]:
            print("  %-40r -> %r" % (s[:38], o[:70]))
    return {"name": name, "pairs": pairs, "allup": len(allup), "mixed": len(mixed)}


def main():
    print("环境：%s / version=%s / uppercase=%s"
          % (EDITION.edition_id(), EDITION.version(), EDITION.upper_case()))
    if EDITION.upper_case():
        raise SystemExit("edition 不是通用版（uppercase 开着），这批数据不能用来验通用版")
    files = [(n, p) for n, p in FILES]
    if len(sys.argv) > 1:
        files = [("argv", sys.argv[1])]
    out = []
    for n, p in files:
        r = run_one(n, p)
        if r:
            out.append(r)
    with open(os.path.join(OUTDIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
