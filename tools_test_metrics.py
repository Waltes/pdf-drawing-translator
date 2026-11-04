


# -*- coding: utf-8 -*-
"""
换算与校验层的回归测试。

改过 convert_to_metric / normalize_metric_format / verify_metric_output
之后跑一遍，确认该改的改了、不该动的没动。全程打桩，不调接口，不花钱。

    python tools_test_metrics.py

退出码 0 = 全过，1 = 有用例失败。
用例写法见下面 CASES 的注释。
"""
import os
import sys

sys.path.insert(0, os.getcwd())
from core.translate import (MetricReport, convert_to_metric, has_imperial,
                            verify_metric_output)

# ---------------------------------------------------------------------------
# 第一组：单位写法的直测
#
# 图纸上的英制标注不是只有 «4"» 一种写法。同一张图里可能同时出现
# 全角引号（4”）、两个单引号（4''）、带空格分数（1 1/2"）、in 缩写（4 in.）。
# 这些漏一个，那条标注就原样留在图上，车间照着做就是错件。
# 这一组把见过的写法全部钉住，改正则之后先跑这里。
#
# 写法：(原文, 期望输出)
#   - 期望输出必须逐字一致，不做模糊比对 —— 多一个空格都算失败。
#   - 「不该动」的用例期望输出等于原文，用来防误伤（介词 in、1990's、螺纹代号）。
# ---------------------------------------------------------------------------
UNIT_CASES = [
    # 英寸符号的五种常见写法
    ('4"', '101.60mm'),
    ('1-1/2"', '38.10mm'),
    ('4\u201d', '101.60mm'),        # 全角右引号，Word/InDesign 排版后抽出来
    ('4\u201c', '101.60mm'),        # 全角左引号
    ('4\u2033', '101.60mm'),        # U+2033 标准英寸符号
    ("4''", '101.60mm'),           # 两个直单引号，ASCII 图纸最常见
    ("1-1/2''", '38.10mm'),
    ('4\u2019\u2019', '101.60mm'),  # 两个全角单引号

    # 英尺
    ("8'", '2438.40mm'),
    ('8\u2019', '2438.40mm'),       # 全角右单引号
    ('8\u2032', '2438.40mm'),       # U+2032 分符号
    ('8 ft', '2438.40mm'),
    ('9 foot', '2743.20mm'),

    # 英尺+英寸合写
    ("2'-6\"", '762.00mm'),
    ("2'-0''", '609.60mm'),
    ("2'-0\u201d", '609.60mm'),

    # 带整数的混合分数：连字符、空格、连字符带空格三种分隔
    ('1-1/2"', '38.10mm'),
    ('1 1/2"', '38.10mm'),
    ('1 - 1/2"', '38.10mm'),
    ('1- 1/2"', '38.10mm'),
    ('2 3/8"', '60.33mm'),

    # 英寸的 in 缩写：只认紧贴数字、带句点、全大写三种
    ('4in', '101.60mm'),
    ('4 in.', '101.60mm'),
    ('4IN', '101.60mm'),
    ('4 IN', '101.60mm'),
    ('4 inch', '101.60mm'),
    ('4 inches', '101.60mm'),

    # 质量
    ('25 lb', '11.34kg'),
    ('8 oz', '0.227kg'),
    ('70lbs', '31.75kg'),

    # 上下文里的标注（前缀 / 后缀 / 成对出现）
    ('THK 4"', 'THK 101.60mm'),
    ('OD 1-1/2"', 'OD 38.10mm'),
    ('\u00d84"', '\u00d8101.60mm'),
    ('4" x 8"', '101.60mm x 203.20mm'),
    ('12"L x 6"W', '304.80mmL x 152.40mmW'),
    ('3/4" pipe', '19.05mm pipe'),

    # 紧贴数字的工程前缀（中间没有空格），用户那份护栏图纸里就有 R3-1/2"
    ('R3-1/2"', 'R88.90mm'),        # 半径
    ('R1/2"', 'R12.70mm'),
    ('R11/32"', 'R8.73mm'),
    ('SR1-1/2"', 'SR38.10mm'),      # 球面半径
    ('OD4"', 'OD101.60mm'),         # 外径，无空格
    ('T4"', 'T101.60mm'),           # 厚度
    ("R2'-6\"", 'R762.00mm'),       # 半径 + 英尺英寸合写

    # 全大写单位词（图纸标题栏里常见）
    ('4 INCH', '101.60mm'),
    ('4 INCHES', '101.60mm'),
    ('8 FEET', '2438.40mm'),
    ('25 LB', '11.34kg'),

    # 数值范围：两端都要换算。只换后一个是最危险的情况 ——
    # 图纸第 7 页原文 about 0.8-1.0 lb，旧代码算成 0.8–0.454kg，
    # 前半个还是磅的数值却顶着 kg 的单位，读起来完全错。
    ('0.8\u20131.0 lb', '0.363\u20130.454kg'),
    ('2\u20133 ft', '609.60\u2013914.40mm'),
    ('10~12 oz', '0.283~0.340kg'),
    ('1\u20132 lb', '0.454\u20130.907kg'),

    # 以下是「不该动」的，原文原样返回
    ('38\u201346\u00b0', '38\u201346\u00b0'),      # 角度范围，不是长度
    ('2\u20133 pcs', '2\u20133 pcs'),            # 数量范围
    ('part4"', 'part4"'),           # 小写单词粘连，不是尺寸
    ('type4"', 'type4"'),
    ('1/4"-20 UNC', '1/4"-20 UNC'),      # 螺纹代号
    ('NPT 1/2"', 'NPT 1/2"'),            # 管螺纹代号
    ('BSP 3/4"', 'BSP 3/4"'),
    ('1 in the box', '1 in the box'),    # 介词 in，不是英寸
    ('2 in total', '2 in total'),
    ("1990's", "1990's"),                # 所有格，不是英尺
    ('M8 x 30', 'M8 x 30'),
    ('DN50', 'DN50'),
    ('Rev E', 'Rev E'),
]


def test_units():
    """跑单位写法直测，返回失败条数"""
    bad = 0
    for src, want in UNIT_CASES:
        got = convert_to_metric(src)
        if got != want:
            bad += 1
            print(f"[FAIL] 单位写法 {src!r}")
            print(f"        得到 {got!r}  期望 {want!r}")
    print(f"\n单位写法：通过 {len(UNIT_CASES) - bad} / 失败 {bad}")
    return bad


CASES = [
    # (说明, 原文, 已换算译文, 期望的动作)
    ("完全一致，不该动",
     '4-1/8" upright', '外径104.78mm立杆', "pass"),

    ("AI 算错数，应改成复算值",
     '4-1/8" upright', '外径100mm立杆', "fix:104.78"),

    ("位数少一位，补零即可，数值不判错",
     '4-1/8" upright', '外径104.8mm立杆', "fix:104.80"),

    ("漏换算：译文里还留着英寸",
     '4-1/8" upright', '外径4-1/8"立杆', "fix:104.78"),

    ("质量一致",
     'Panel 25 lb', '面板11.34kg', "pass"),

    ("质量算错",
     'Panel 25 lb', '面板10kg', "fix:11.34"),

    ("小质量三位小数是对的",
     'Bag 8 oz', '袋子0.227kg', "pass"),

    ("小质量算错",
     'Bag 8 oz', '袋子0.03kg', "fix:0.227"),

    ("两个长度值都对",
     '3-3/4" x 1-1/2"', '95.25mm x 38.10mm', "pass"),

    ("两个长度值其中一个算错，数量一致可改",
     '3-3/4" x 1-1/2"', '95.25mm x 40.00mm', "fix:38.10"),

    ("语序被打乱，按值配对仍能对上",
     '3-3/4" wide x 1-1/2" high', '高38.10mm x 宽95.25mm', "pass"),

    ("个数对不上，只报不改",
     '3-3/4" x 1-1/2"', '95.25mm', "warn"),

    ("螺纹代号不动，也不该报警",
     'NPT 1/2" thread', 'NPT 1/2"螺纹', "pass"),

    ("代号加长度混排，只换算长度",
     '1/4"-20 screw, 2" long', '1/4"-20 螺钉，长50.80mm', "pass"),

    ("原文没有计量数据，什么都不做",
     'Weld the post to the base plate', '把立柱焊到底座上', "pass"),

    ("中文原文的公制值必须忠于原值",
     '长度 2000mm', 'LENGTH 2000.00mm', "pass"),

    ("中文原文的公制值被改了，要改回来",
     '长度 2000mm', 'LENGTH 200mm', "fix:2000.00"),

    ("英尺+英寸合写",
     "Overall 2'-0\"", "总高609.60mm", "pass"),

    ("英尺+英寸写错",
     "Overall 2'-0\"", "总高600mm", "fix:609.60"),

    ("单位类型不对：长度写成 kg",
     '4-1/8" upright', '外径104.78kg立杆', "warn"),

    # ---- 缩写写法也要过校验层：原文认不出，复算就无从谈起 ----
    ("两单引号表示的英寸，换算后一致",
     "Post 4''", '立柱101.60mm', "pass"),

    ("两单引号表示的英寸，AI 当成英尺算错了",
     "Post 4''", '立柱1219.20mm', "fix:101.60"),

    ("带空格分数，AI 只取了分数部分",
     'Pipe 1 1/2"', '管道12.70mm', "fix:38.10"),

    ("全角引号表示的英寸，漏换算",
     'Rod 3/4\u201d', '螺杆3/4\u201d', "fix:19.05"),

    ("in 缩写的英寸",
     'Gap 4 in.', '间隙101.60mm', "pass"),
]

def main():
    unit_fail = test_units()

    ok = fail = 0
    for note, src, out, expect in CASES:
        rep = MetricReport()
        fixed, issues = verify_metric_output(src, out, lang="en", page=1, report=rep)
        got_lv = [i[0] for i in issues]

        if expect == "pass":
            good = (fixed == out and not issues)
        elif expect.startswith("fix:"):
            want = expect.split(":", 1)[1]
            good = (want in fixed) and ("fix" in got_lv)
        else:
            good = ("warn" in got_lv) and (fixed == out)   # 报警但不动手

        tag = "OK  " if good else "FAIL"
        if good:
            ok += 1
        else:
            fail += 1
        print(f"[{tag}] {note}")
        print(f"        原文 {src!r}")
        print(f"        输入 {out!r}")
        print(f"        输出 {fixed!r}  级别 {got_lv}")
        if issues:
            print(f"        明细 {issues[0][4]}")

    print(f"\n校验层：通过 {ok} / 失败 {fail}")
    total_fail = fail + unit_fail
    print(f"合计失败 {total_fail} 条")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
