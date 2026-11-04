"""
端到端测试：造一张贴近真实 CAD 导出的图纸，跑完整流程

刻意构造的难点：
  1. 逐字插入的文字（CAD 导出最常见，一行被切成 N 个 span）
  2. 标题栏表格结构
  3. 技术要求多行段落（长句，词典覆盖不到，考验降级）
  4. 旋转 90 度的尺寸标注
  5. 中英混排图纸
"""
import os
import sys

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import pdf_engine
from core.translate import Translator


def build_test_drawing(path):
    """生成一张模拟工程图纸"""
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)   # A3 横向

    # ---- 图框 ----
    page.draw_rect(pymupdf.Rect(20, 20, 822, 575), color=(0, 0, 0), width=2)

    # ---- 标题栏（右下角）----
    tb = pymupdf.Rect(560, 470, 822, 575)
    page.draw_rect(tb, color=(0, 0, 0), width=1.2)
    page.draw_line((560, 500), (822, 500), color=(0, 0, 0), width=0.8)
    page.draw_line((560, 530), (822, 530), color=(0, 0, 0), width=0.8)
    page.draw_line((690, 470), (690, 575), color=(0, 0, 0), width=0.8)
    page.draw_line((690, 530), (822, 530), color=(0, 0, 0), width=0.8)

    # ---- 零件轮廓（验证图形不被擦掉）----
    page.draw_rect(pymupdf.Rect(120, 120, 380, 300), color=(0, 0, 0), width=1.5)
    page.draw_circle((480, 200), 60, color=(0, 0, 0), width=1.5)
    page.draw_circle((480, 200), 40, color=(0, 0, 0), width=1)
    # 孔位
    for cx, cy in [(160, 160), (340, 160), (160, 260), (340, 260)]:
        page.draw_circle((cx, cy), 8, color=(0, 0, 0), width=1)
    # 剖面线
    for x in range(200, 300, 7):
        page.draw_line((x, 320), (x + 25, 360), color=(0, 0, 0), width=0.5)
    page.draw_rect(pymupdf.Rect(200, 320, 300, 360), color=(0, 0, 0), width=1)

    # ---- 文字：整串插入 ----
    page.insert_text((420, 300), "法兰盘 DN80", fontname="china-s", fontsize=14)
    page.insert_text((420, 330), "材质：不锈钢 304", fontname="china-s", fontsize=12)
    page.insert_text((420, 360), "表面处理：拉丝", fontname="china-s", fontsize=12)

    # ---- 文字：逐字插入（模拟 CAD 拆字，考验合并逻辑）----
    x = 130
    for ch in "技术要求":
        page.insert_text((x, 400), ch, fontname="china-s", fontsize=13)
        x += 13.5
    lines = [
        "1. 未注圆角 R2，锐边倒钝。",
        "2. 未注尺寸公差按 GB/T 1804-m。",
        "3. 表面不允许有划痕、气泡。",
    ]
    y = 425
    for ln in lines:
        x = 130
        # 逐字写入，间距刻意贴近真实排版
        for ch in ln:
            page.insert_text((x, y), ch, fontname="china-s", fontsize=11)
            x += 11.2
        y += 18

    # ---- 旋转 90 度标注 ----
    page.insert_text((400, 480), "A-A 剖视", fontname="china-s", fontsize=11, rotate=90)

    # ---- 标题栏文字 ----
    page.insert_text((566, 490), "图号", fontname="china-s", fontsize=9)
    page.insert_text((696, 490), "比例", fontname="china-s", fontsize=9)
    page.insert_text((566, 520), "材质", fontname="china-s", fontsize=9)
    page.insert_text((696, 520), "数量", fontname="china-s", fontsize=9)
    page.insert_text((566, 560), "设计", fontname="china-s", fontsize=9)
    page.insert_text((696, 560), "日期", fontname="china-s", fontsize=9)
    page.insert_text((760, 490), "DWG-2026-018", fontname="helv", fontsize=9)
    page.insert_text((760, 520), "1:2", fontname="helv", fontsize=9)

    # ---- 英文标注（应转中文）----
    page.insert_text((140, 340), "FLANGE PLATE", fontname="helv", fontsize=12)
    page.insert_text((140, 365), "SURFACE ROUGHNESS Ra3.2", fontname="helv", fontsize=10)

    doc.save(path)
    doc.close()
    return path


def main():
    work = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(work, "test_input.pdf")
    dst = os.path.join(work, "test_output.pdf")

    build_test_drawing(src)
    print(f"[1] 测试图纸已生成: {src}")

    # 提取统计
    doc = pymupdf.open(src)
    groups = pdf_engine.extract_groups(doc[0])
    print(f"[2] 提取到 {len(groups)} 个文字块（已按间距合并拆字的 span）:")
    for g in groups[:14]:
        print(f"    {g.lang:4} {g.text!r:32} size={g.size:.1f} angle={g.angle:.0f}")
    print(f"    ...共 {len(groups)} 个")
    doc.close()

    # 翻译 + 重写
    tr = Translator()
    print("\n[3] 开始翻译与重写...")
    report = pdf_engine.process_pdf(
        src, dst,
        translate_fn=tr.translate,
        progress=lambda d, t, m: print("   ", m),
    )

    print(f"\n[4] 完成，共改写 {len(report)} 处：")
    for page_no, old, new, shrunk in report:
        flag = " [字号缩小]" if shrunk else ""
        print(f"    p{page_no} {old!r:30} -> {new!r}{flag}")

    print("\n[5] 翻译通道统计:", tr.stats)
    tr.save_cache()

    # 输出图对比
    pdf_engine.render_comparison(src, os.path.join(work, "test_input.png"))
    print(f"[6] 已渲染输出预览: {os.path.join(work, 'test_output_预览.png')}")

    # 校验：图形是否完好
    d2 = pymupdf.open(dst)
    print(f"[7] 输出文件图形图元数: {len(d2[0].get_drawings())}（应与输入接近）")
    d2.close()


if __name__ == "__main__":
    main()
