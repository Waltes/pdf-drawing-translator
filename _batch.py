# -*- coding: utf-8 -*-
"""把客户分享过的所有图纸逐份跑一遍，各自体检。

跟 _stress.py 的分工：
    _stress.py  —— 同一份文件连跑 N 轮，抓「这次对、下次错」的抖动
    _batch.py   —— 一次过所有文件，抓「这类图纸以前没跑过」的问题
                   （不同图纸的标注密度、表格、位图、双语混排都不一样，
                    单文件压测覆盖不到）

用法：
    python _batch.py             # 全部
    python _batch.py BR01        # 只跑 key 里含 BR01 的

每份用独立缓存，不做跨文件复用 —— 缓存复用会把「这一份漏译」掩盖成
「上一份译对了」，检查就失效了。
"""
import atexit
import json
import os
import shutil
import sys
import time
import traceback

sys.path.insert(0, ".")

# ---- 必须先切到定制版再 import core（edition.py 在 import 时就定版） ----
_ROOT = os.path.dirname(os.path.abspath(__file__))
_ED = os.path.join(_ROOT, "edition.json")
_ED_BAK = os.path.join(_ROOT, "_edition_backup.json")
_copy = False
with open(_ED, encoding="utf-8") as _f:
    if json.load(_f).get("id") != "dynamic":
        shutil.copy2(_ED, _ED_BAK)
        shutil.copy2(os.path.join(_ROOT, "editions", "dynamic.json"), _ED)
        _copy = True


def _restore_edition():
    if _copy and os.path.exists(_ED_BAK):
        shutil.copy2(_ED_BAK, _ED)
        os.remove(_ED_BAK)


atexit.register(_restore_edition)

import _stress_check as CHK
import core.edition as EDITION
import core.translate as T
from core import pdf_engine
from core.translate import Translator, load_config

CFG = r"C:\Users\stanl\AppData\Roaming\PDFDrawingTranslator\data\config.json"
OUTDIR = "_batch"
D = r"C:\Users\stanl\Desktop" + "\\"

# ocr=True  整页转位图识别：给纯转曲文件（无文字层、无位图对象）
# ocr=False 常规路径：读文字层 + 对位图做 OCR
CASES = [
    dict(key="1_tuopan1110",  file="3.05米护栏托盘示意图2026-11-10.pdf",
         src="zh", dst="en", ocr=True,  note="中文转曲 5 页"),
    dict(key="2_tuopan1112",  file="【测试】3.05米护栏托盘示意图2026-11-12.pdf",
         src="zh", dst="en", ocr=True,  note="中文转曲 5 页，11-10 的修订版"),
    dict(key="3_hulan1118",   file="3.05米黑色护栏示意图汇总2026-11-18.pdf",
         src="zh", dst="en", ocr=True,  note="中文转曲 8 页（已测过）"),
    dict(key="4_xiecheng",    file="斜撑底座示意图2026-11-15(1).pdf",
         src="zh", dst="en", ocr=True,  note="中文转曲 3 页，斜排标注多"),
    dict(key="5_chuwugui",    file="新储物柜示意图2025-12-19.pdf",
         src="zh", dst="en", ocr=True,  note="中文转曲 2 页"),
    dict(key="6_sledbox",     file="SledBox_Drawing 1.pdf",
         src="en", dst="zh", ocr=False, note="英文 1 页，文字层稀疏+大量转曲"),
    dict(key="7_br01",        file="BR-01-Fence-Brace-RevE(2).pdf",
         src="en", dst="zh", ocr=False, note="英文 7 页，有文字层+6 张 3D 图"),
]


def guard():
    """环境自检：版本、严格排版、锁定词、API key。任何一条不对，后面全是白跑。"""
    if EDITION.edition_id() != "dynamic":
        raise SystemExit("edition 没切到 dynamic")
    if not EDITION.strict_layout():
        raise SystemExit("strict_layout 未开")
    with open(CFG, encoding="utf-8") as f:
        cfg = load_config()
        cfg.setdefault("llm", {})["api_key"] = json.load(f)["llm"]["api_key"]
    tr = Translator(cfg)
    if not tr.glossary.locked:
        raise SystemExit("客户锁定词为空，遮罩不生效")
    print("环境：dynamic / strict_layout=on / 锁定词 %d 条" % len(tr.glossary.locked),
          flush=True)
    return cfg


def run_one(case, cfg):
    """跑一份，返回体检结果。"""
    src_path = D + case["file"]
    if not os.path.exists(src_path):
        return {"key": case["key"], "skip": "文件不存在"}

    os.makedirs(OUTDIR, exist_ok=True)
    out_pdf = os.path.join(OUTDIR, case["key"] + ".pdf")
    cache = os.path.join(OUTDIR, "_cache_%s.json" % case["key"])
    if os.path.exists(cache):
        os.remove(cache)
    T.CACHE_PATH = cache

    # 每份单独造 Translator：方向是构造时从 cfg 读的，改完必须重建
    c = json.loads(json.dumps(cfg))          # 深拷贝，别让上一份的方向污染下一份
    c.setdefault("translate", {})["src"] = case["src"]
    c["translate"]["dst"] = case["dst"]
    tr = Translator(c)

    pairs = []

    def rec_batch(items):
        res = tr.translate_many(items)
        for (txt, _lang), out in zip(items, res):
            pairs.append({"src": txt, "out": out})
        return res

    def rec_one(text, lang):
        out = tr.translate_many([(text, lang)])[0]
        pairs.append({"src": text, "out": out, "single": True})
        return out

    stats = {}
    t0 = time.time()
    pdf_engine.process_pdf(
        src_path, out_pdf, rec_one, translate_batch=rec_batch,
        stats_out=stats, unit_mode="metric", render_preview=False,
        ocr_full_pages=case["ocr"], dst_lang=tr.dst_lang, reviewer=tr,
    )
    cost = round(time.time() - t0, 1)

    res = CHK.check(out_pdf, case["dst"])
    res.pop("fingerprint", None)
    res.update({
        "key": case["key"], "file": case["file"], "note": case["note"],
        "dir": "%s→%s" % (case["src"], case["dst"]), "ocr": case["ocr"],
        "seconds": cost,
        "stats": {k: (len(v) if isinstance(v, list) else v) for k, v in stats.items()},
        "untranslated": stats.get("untranslated", []),
        "tr_stats": dict(tr.stats),
        "pairs": pairs,
        "out": out_pdf,
    })
    return res


def main():
    cfg = guard()
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    cases = [c for c in CASES if only.lower() in c["key"].lower()] or CASES

    results = []
    for i, case in enumerate(cases, 1):
        print("=" * 64, flush=True)
        print("[%d/%d] %s | %s | %s" % (i, len(cases), case["key"], case["note"],
                                        "整页OCR" if case["ocr"] else "常规"), flush=True)
        try:
            r = run_one(case, cfg)
        except Exception:
            print("崩了：\n%s" % traceback.format_exc(), flush=True)
            results.append({"key": case["key"], "crash": traceback.format_exc()})
            continue
        if r.get("skip"):
            print("跳过：", r["skip"], flush=True)
            continue
        results.append(r)
        print("  完成 %.1fs | span %d | 未译 %d | 越界 %d | 叠字 %d | 中文 %d | 未译英文 %d "
              "| 禁用 %d | 双标 %d | 孤立 %d | <8pt %d | 直径 %d | 空页 %s"
              % (r["seconds"], r["n_spans"], len(r["untranslated"]), len(r["oob"]),
                 len(r["overlap"]), len(r["zh"]), len(r["en"]), len(r["banned"]),
                 len(r["dbl"]), len(r["lone"]), len(r["tiny"]), len(r["dia"]),
                 r["empty_pages"]), flush=True)
        print("      API %s" % r["tr_stats"], flush=True)
        with open(os.path.join(OUTDIR, case["key"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1, default=str)

    # ---- 汇总 ----
    print("\n" + "=" * 64)
    print("%-14s %6s %5s %5s %5s %5s %5s %5s %5s %5s" %
          ("图纸", "秒", "span", "未译", "越界", "叠字", "残留", "禁用", "标点", "空页"))
    for r in results:
        if r.get("crash") or r.get("skip"):
            print("%-14s 崩溃/跳过" % r["key"])
            continue
        d = "zh" if r["dir"].endswith("zh") else "en"
        residu = len(r["zh"]) if d == "en" else len(r["en"])
        punct = len(r["dbl"]) + len(r["lone"])
        print("%-14s %6.1f %5d %5d %5d %5d %5d %5d %5d %5s" %
              (r["key"], r["seconds"], r["n_spans"], len(r["untranslated"]),
               len(r["oob"]), len(r["overlap"]), residu, len(r["banned"]),
               punct, r["empty_pages"] or "-"))

    with open(os.path.join(OUTDIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in r.items() if k != "pairs"} for r in results],
                  f, ensure_ascii=False, indent=1, default=str)
    print("\n明细见 %s/" % OUTDIR)


if __name__ == "__main__":
    main()
