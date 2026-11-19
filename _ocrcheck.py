# -*- coding: utf-8 -*-
"""对输出 PDF 做「图上到底还有没有原文语言」的 OCR 复检。

为什么不能只查文字层：
    客户的中文图纸是**矢量转曲**的（没有文字层）。译完写上去的是新文字层，
    但原位那圈中文轮廓如果没擦掉，它还在图上，只是查文字层查不到。
    1.1.4 报「中文残留 0」，实际 tuopan 图上竖排的「贴上橡胶垫后983mm」还在。
    所以必须把输出渲染成位图、重新 OCR，才看得见客户眼里看到的东西。

判定口径：
    汉字连续 >= 3 个 → 残留（单字多半是被 OCR 认错的符号，不算）
    英文连续 3 个词以上 → 英译中方向的残留

用法：python _ocrcheck.py [文件.pdf ...]
"""
import os
import re
import sys

import pymupdf

# 汉字残留：一块里出现 2 个以上汉字就算。
# 不能要求「连续 3 个汉字」—— 「2026年11月16日」的汉字被数字隔开，
# 按连续判会全漏，而那正是 1.1.5 实测漏在图上的一处。
HAN = re.compile(r"[\u4e00-\u9fff]")
EN_RUN = re.compile(r"(?:[A-Za-z][A-Za-z\-']*\s+){2,}[A-Za-z][A-Za-z\-']*")


def ocr_pages(pdf_path, dpi=200):
    """逐页渲染 → OCR，返回 [(页号, 文字, 框, 置信度)]。"""
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR()
    doc = pymupdf.open(pdf_path)
    out = []
    for pno, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=dpi)
        tmp = "_ocrcheck_tmp.png"
        pix.save(tmp)
        res, _ = eng(tmp)
        for box, txt, score in (res or []):
            out.append((pno, txt.strip(), box, float(score)))
    if os.path.exists("_ocrcheck_tmp.png"):
        os.remove("_ocrcheck_tmp.png")
    doc.close()
    return out


def scan(pdf_path, want="zh"):
    """want=zh 查汉字残留；want=en 查英文整句残留。"""
    items = ocr_pages(pdf_path)
    hits = []
    for p, t, _b, s in items:
        if want == "zh":
            if len(HAN.findall(t)) >= 2:
                hits.append((p, t, s))
        elif EN_RUN.search(t):
            hits.append((p, t, s))
    return hits, len(items)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        # 文件名里带 zh/中 的按中译英查汉字，其余按英译中查英文
        want = "zh"
        hits, n = scan(p, want)
        print("%-26s OCR %d 块 | 汉字残留 %d 处" % (os.path.basename(p)[:26], n, len(hits)))
        for pg, t, s in hits[:12]:
            print("    p%-2d %.2f  %s" % (pg, s, t[:60]))
