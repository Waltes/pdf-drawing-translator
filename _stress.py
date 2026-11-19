# -*- coding: utf-8 -*-
"""多轮压力测试：同一份源图纸连跑 N 遍，抓「轮次间抖动」。

为什么要跑多遍：
    单轮全绿只能证明"这次对"。会咬人的 BUG 是"同一条标注这次译对、
    下次译错/清空/缩字号"—— 源头是模型输出随机 + 重译打回，
    单轮检查看不见，跨轮比对才看得见。

用法：
    python _stress.py            # 默认 3 轮
    python _stress.py 5          # 5 轮

每轮刻意清空翻译缓存（CACHE_PATH 指到临时文件），
否则第 2 轮起全命中缓存，等于只跑了一遍。
"""
import atexit
import json
import os
import shutil
import sys
import time
import traceback

sys.path.insert(0, ".")

# ---- 必须先切到定制版再 import core ----
# edition.py 在 import 时就把 edition.json 读成模块级 _CFG，之后再改文件不生效。
# 踩过一次：拿通用版跑了一整轮，禁用词 11 处，差点当成产品 BUG 报出去。
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
    """跑完（含异常）把 edition.json 还原成通用版，别把开发环境留在定制态。"""
    if _copy and os.path.exists(_ED_BAK):
        shutil.copy2(_ED_BAK, _ED)
        os.remove(_ED_BAK)


atexit.register(_restore_edition)

import _stress_check as CHK
import core.edition as EDITION
import core.translate as T
from core import pdf_engine
from core.translate import Translator, load_config

# 客户真图纸：8 页纯矢量转曲（无文字层、无位图），走整页 OCR
SRC = r"C:\Users\stanl\Desktop\3.05米黑色护栏示意图汇总2026-11-18.pdf"
# 定制版还没有自己的 config（没装过），借通用版 APPDATA 里那把能用的 key。
# 源码目录 data/config.json 里那把已经失效，用它必然 401。
CFG = r"C:\Users\stanl\AppData\Roaming\PDFDrawingTranslator\data\config.json"
OUTDIR = "_stress"

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def guard_edition():
    """跑之前验一遍环境：版本、严格排版、客户锁定词是不是真加载了。"""
    if EDITION.edition_id() != "dynamic":
        raise SystemExit("edition 没切到 dynamic，测试无意义")
    if not EDITION.strict_layout():
        raise SystemExit("strict_layout 未开，跑的不是定制版的排版逻辑")
    with open(CFG, encoding="utf-8") as f:
        cfg = load_config()
        cfg.setdefault("llm", {})["api_key"] = json.load(f)["llm"]["api_key"]
    tr = Translator(cfg)
    if not tr.glossary.locked:
        raise SystemExit("客户锁定词表为空，遮罩不会生效，测试无意义")
    print("环境确认：edition=dynamic strict_layout=on 锁定词=%s"
          % list(tr.glossary.locked), flush=True)
    print("术语库：%s" % (T.dict_engine.glossary_paths(),), flush=True)
    return cfg


def one_round(idx, cfg):
    """跑一轮，返回本轮的体检结果与逐条译文明细。"""
    os.makedirs(OUTDIR, exist_ok=True)
    out_pdf = os.path.join(OUTDIR, "run%d.pdf" % idx)
    # 每轮一份空缓存，逼它真的走网络
    tmp_cache = os.path.join(OUTDIR, "_cache_run%d.json" % idx)
    if os.path.exists(tmp_cache):
        os.remove(tmp_cache)
    T.CACHE_PATH = tmp_cache

    tr = Translator(cfg)

    pairs = []          # 逐条：原文 → 译文

    def rec_batch(items):
        """包一层批量翻译，把所有 (原文, 译文) 记下来。"""
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
        SRC, out_pdf, rec_one, translate_batch=rec_batch,
        stats_out=stats, unit_mode="metric", render_preview=False,
        ocr_full_pages=True, dst_lang=tr.dst_lang, reviewer=tr,
    )
    cost = round(time.time() - t0, 1)

    res = CHK.check(out_pdf)
    res.pop("fingerprint", None)      # 只在本进程内对比，不用落盘这么大
    res.update({
        "round": idx,
        "seconds": cost,
        "stats": {k: v for k, v in stats.items() if k != "untranslated"},
        "untranslated": stats.get("untranslated", []),
        "tr_stats": dict(tr.stats),
        "pairs": pairs,
    })
    return res


def main():
    cfg = guard_edition()
    runs = []
    for i in range(1, N + 1):
        print("=" * 60, flush=True)
        print("第 %d/%d 轮开始" % (i, N), flush=True)
        try:
            r = one_round(i, cfg)
        except Exception:
            print("第 %d 轮崩了：\n%s" % (i, traceback.format_exc()), flush=True)
            runs.append({"round": i, "crash": traceback.format_exc()})
            continue
        runs.append(r)
        print("第 %d 轮完成 %.1fs | span %d | 未译 %d | 越界 %d | 叠字 %d | 中文 %d "
              "| 禁用 %d | 双标点 %d | 孤立 %d | <8pt %d | 直径符号 %d | 空页 %s"
              % (i, r["seconds"], r["n_spans"], len(r["untranslated"]),
                 len(r["oob"]), len(r["overlap"]), len(r["zh"]),
                 len(r["banned"]), len(r["dbl"]), len(r["lone"]),
                 len(r["tiny"]), len(r["dia"]), r["empty_pages"]), flush=True)
        print("    API 调用 %s" % r["tr_stats"], flush=True)
        with open(os.path.join(OUTDIR, "run%d.json" % i), "w", encoding="utf-8") as f:
            # stats 里塞了 ReviewReport 之类对象，default=str 兜住
            json.dump(r, f, ensure_ascii=False, indent=1, default=str)

    # ---- 汇总：逐轮指标 ----
    print("\n" + "=" * 60)
    print("逐轮指标")
    hdr = ("轮", "秒", "span", "未译", "越界", "叠字", "中文", "禁用", "双标", "孤立", "<8pt", "直径")
    print("%-4s %6s %5s %5s %5s %5s %5s %5s %5s %5s %5s %5s" % hdr)
    for r in runs:
        if "crash" in r:
            print(" %-3d  崩溃" % r["round"])
            continue
        print("%-4d %6.1f %5d %5d %5d %5d %5d %5d %5d %5d %5d %5d"
              % (r["round"], r["seconds"], r["n_spans"], len(r["untranslated"]),
                 len(r["oob"]), len(r["overlap"]), len(r["zh"]),
                 len(r["banned"]), len(r["dbl"]), len(r["lone"]), len(r["tiny"]), len(r["dia"])))

    # ---- 核心：同一条原文，各轮译法是否一致 ----
    ok = [r for r in runs if "crash" not in r]
    if len(ok) < 2:
        print("\n轮次不足，跳过抖动比对")
        return

    by_src = {}
    for r in ok:
        for p in r["pairs"]:
            key = p["src"].strip()
            by_src.setdefault(key, {})[r["round"]] = p["out"]

    unstable = {}
    for src, got in by_src.items():
        vals = {k: (v or "").strip() for k, v in got.items()}
        uniq = set(vals.values())
        if len(uniq) > 1:
            unstable[src] = vals

    print("\n跨轮抖动：%d / %d 条原文在不同轮次出现了不同译文" % (len(unstable), len(by_src)))
    for src, vals in list(unstable.items())[:25]:
        print("\n  原文: %s" % src[:70])
        for rnd in sorted(vals):
            v = vals[rnd] or "(空)"
            print("    第%d轮: %s" % (rnd, v[:70]))

    # 只有部分轮次出现的原文（说明某轮整条漏掉）
    all_rounds = {r["round"] for r in ok}
    missing = {s: sorted(all_rounds - set(g)) for s, g in by_src.items()
               if len(g) < len(all_rounds)}
    print("\n某轮缺失的原文：%d 条" % len(missing))
    for s, rnds in list(missing.items())[:15]:
        print("   缺第%s轮: %s" % (rnds, s[:60]))

    with open(os.path.join(OUTDIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"runs": [{k: v for k, v in r.items() if k != "pairs"} for r in runs],
                   "unstable": unstable, "missing": missing},
                  f, ensure_ascii=False, indent=1, default=str)
    print("\n明细见 %s/summary.json" % OUTDIR)


if __name__ == "__main__":
    main()
