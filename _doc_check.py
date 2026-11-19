# -*- coding: utf-8 -*-
"""文档类型（drawing / general）差异体检：同一批句子各跑两套规则，逐条对照。

为什么要单独一份脚本：
    _batch.py 只跑图纸（它还强制切定制版），_std_check.py 只验大小写。
    这次改的是「一套提示词服务两种内容」，要看的正是同一句话在两套规则下的
    差别 —— 只看其中一边，看不出规则到底生效没有。

用法：
    python _doc_check.py
"""
import json
import os
import sys

sys.path.insert(0, ".")

import core.translate as T
from core.translate import Translator, load_config

CFG = r"C:\Users\stanl\AppData\Roaming\PDFDrawingTranslator\data\config.json"
OUT = "_doc"

# 中→英：前四条是典型的「通用文档」，后三条是图纸标注，用来看两套规则的分界
CASES_ZH2EN = [
    ("说明书", "本产品适用于室内外环境，安装前请仔细阅读本说明书。"),
    ("规格", "输送带总长度2.79米，宽度600毫米，承重不低于150公斤。"),
    ("技术要求", "表面采用热浸镀锌处理，镀层厚度不小于65微米，不得有漏镀。"),
    ("宣传", "轻巧耐用，一人即可搬运，折叠后厚度仅120毫米。"),
    ("图纸标注", "立柱方管50*50*2.5mm"),
    ("图纸标注", "半径35mm圆管"),
    ("日期", "2026年11月16日"),
]

# 英→中：collar 在图纸库里被锁定成「锁紧环」，通用文档模式下不该这样
CASES_EN2ZH = [
    ("服装类", "The collar is reinforced with a double stitch."),
    ("机械类", "Tighten the collar with a 6mm hex key."),
]


def make(doc_type, src, dst):
    with open(CFG, encoding="utf-8") as f:
        cfg = load_config()
        cfg.setdefault("llm", {})["api_key"] = json.load(f)["llm"]["api_key"]
    cfg.setdefault("translate", {})["src"] = src
    cfg["translate"]["dst"] = dst
    cfg["translate"]["doc_type"] = doc_type
    # 文档模式下单位按界面联动落到 keep（这里显式模拟同一步）
    cfg["translate"]["units"] = "keep" if doc_type == "general" else "metric"
    return Translator(cfg)


def main():
    os.makedirs(OUT, exist_ok=True)
    report = {}
    for dt in ("drawing", "general"):
        T.CACHE_PATH = os.path.join(OUT, "_cache_%s.json" % dt)
        if os.path.exists(T.CACHE_PATH):
            os.remove(T.CACHE_PATH)
        tr = make(dt, "zh", "en")
        print("\n================ %s（术语库 %d 条） ================"
              % (dt, len(tr.glossary.zh2en)))
        rows = []
        for kind, text in CASES_ZH2EN:
            out = tr.translate_many([(text, "zh")])[0]
            rows.append({"kind": kind, "src": text, "out": out})
            print("  [%s] %s\n      -> %s" % (kind, text, out))
        report[dt] = rows

    for dt in ("drawing", "general"):
        T.CACHE_PATH = os.path.join(OUT, "_cache_%s_e2z.json" % dt)
        if os.path.exists(T.CACHE_PATH):
            os.remove(T.CACHE_PATH)
        tr = make(dt, "en", "zh")
        print("\n================ %s（英→中） ================" % dt)
        rows = []
        for kind, text in CASES_EN2ZH:
            out = tr.translate_many([(text, "en")])[0]
            rows.append({"kind": kind, "src": text, "out": out})
            print("  [%s] %s\n      -> %s" % (kind, text, out))
        report[dt + "_e2z"] = rows

    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
