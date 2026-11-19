# -*- coding: utf-8 -*-
"""输出 PDF 静态体检：只用文字层，不依赖翻译过程。

每项检查都对应一次真实交付事故，见 packaging/release_notes_dynamic_1.1.*.md。
单独可跑：python _stress_check.py 某个.pdf
"""
import re
import sys

import pymupdf

# 汉字（含中文标点单列）
HAN = re.compile(r"[\u4e00-\u9fff]")
# 客户明令不用的叫法（1.1.4 交付前查过一遍）
BANNED = ["PALLET", "TRAY", "SKID", "GUARDRAIL", "HANDRAIL", "BRACKET"]
# 重复标点：OCR 切块各带一个标点，合并后会连出两个（1.1.0 的「双冒号」）
DBL_PUNCT = re.compile(r"::|,,|\.\.(?!\.)|;;|、、|：：")
# 纯标点的孤立 span（1.1.4 第四条）
LONE = re.compile(r"^[\s\.,;:：，。、；！？'\"\-–—()（）*×]+$")
# 直径符号的替代写法：⌀(U+2300) / ∅(U+2205)。西文字体普遍没有这两个字形，
# 写入时会回退到别的字体，同一张图上出现两种直径字形（1.1.4 实测第 2 页 3 处）。
DIA = re.compile("[\u2300\u2205]")
TINY_PT = 8.0


def _spans(doc):
    """把所有页的 span 摊平，带上页号与字号。"""
    out = []
    for pno, page in enumerate(doc):
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    t = sp.get("text", "")
                    if not t.strip():
                        continue
                    out.append({
                        "page": pno + 1,
                        "text": t,
                        "bbox": sp["bbox"],
                        "size": round(sp["size"], 2),
                    })
    return out


def _inter(a, b):
    """两矩形交集面积；用于判断"字压在字上"。"""
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
    x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _area(a):
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])


def check(pdf_path):
    """返回体检结果字典。"""
    doc = pymupdf.open(pdf_path)
    spans = _spans(doc)
    res = {
        "zh": [],          # 中文残留
        "banned": [],      # 禁用叫法
        "dbl": [],         # 重复标点
        "lone": [],        # 孤立标点
        "oob": [],         # 越出页面
        "overlap": [],     # 字压字（写两遍）
        "tiny": [],        # 小于 8pt
        "dia": [],         # 非 Ø 的直径符号（会让字体回退）
        "empty_pages": [], # 整页无字（可能是漏译，也可能是原图空白）
    }

    for sp in spans:
        t = sp["text"].strip()
        if HAN.search(t):
            res["zh"].append((sp["page"], t))
        if DIA.search(t):
            res["dia"].append((sp["page"], t))
        up = t.upper()
        for w in BANNED:
            # 整词匹配，避免 "PALLETIZED" 之类误报也放过 —— 这里就要严
            if re.search(r"\b%s\b" % w, up):
                res["banned"].append((sp["page"], t, w))
                break
        if DBL_PUNCT.search(t):
            res["dbl"].append((sp["page"], t))
        if LONE.match(t):
            res["lone"].append((sp["page"], t))
        if sp["size"] < TINY_PT:
            res["tiny"].append((sp["page"], sp["size"], t))

        page = doc[sp["page"] - 1]
        pw, ph = page.rect.width, page.rect.height
        x0, y0, x1, y1 = sp["bbox"]
        # 容差 1pt：基线圆整会差零点几
        if x0 < -1 or y0 < -1 or x1 > pw + 1 or y1 > ph + 1:
            res["oob"].append((sp["page"], sp["text"][:40],
                               "x %.0f~%.0f / 页宽 %.0f" % (x0, x1, pw)))

    # 叠字：同页两 span，小的一方的面积有 35% 以上落在另一方里
    for i in range(len(spans)):
        a = spans[i]
        for j in range(i + 1, len(spans)):
            b = spans[j]
            if b["page"] != a["page"]:
                continue
            if b["bbox"][1] > a["bbox"][3] or b["bbox"][3] < a["bbox"][1]:
                continue
            small = min(_area(a["bbox"]), _area(b["bbox"]))
            if small <= 1:
                continue
            if _inter(a["bbox"], b["bbox"]) / small > 0.35:
                res["overlap"].append((a["page"], a["text"][:32], b["text"][:32]))

    for pno, page in enumerate(doc):
        if not page.get_text().strip():
            res["empty_pages"].append(pno + 1)

    # 指纹：跨轮对比用。只留文本，坐标一律不要（坐标每次微差是正常的）
    res["fingerprint"] = sorted(
        (sp["page"], re.sub(r"\s+", " ", sp["text"].strip()).lower())
        for sp in spans)
    res["n_spans"] = len(spans)
    res["pages"] = len(doc)
    doc.close()
    return res


def failed(res):
    """判定：这些项必须为 0 才敢交付。empty_pages 不算失败（原图可能真没字）。"""
    return {k: v for k, v in res.items()
            if k in ("zh", "banned", "dbl", "lone", "oob", "overlap", "tiny", "dia")
            and v}


if __name__ == "__main__":
    r = check(sys.argv[1])
    print("页数 %d / span %d" % (r["pages"], r["n_spans"]))
    for k in ("zh", "banned", "dbl", "lone", "oob", "overlap", "tiny", "dia"):
        print("%-8s %d" % (k, len(r[k])))
        for item in r[k][:10]:
            print("    ", item)
    print("整页无字", r["empty_pages"])
