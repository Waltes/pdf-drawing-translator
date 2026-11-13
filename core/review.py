# -*- coding: utf-8 -*-
"""
输出前复核

为什么单独一层：翻译链路里已经逐条做了质检（数字保真、语言纯净、非空），
但那条质检只管「这一条能不能用」，管不了「整份文件交出去干不干净」。
客户实际踩过的两类坑都发生在这里：

  1. 条目被质检拒掉后静默退回原文，页面上就留着一句中文（v1.0.6 那两处
     「2根5毫米厚镀锌铁板2.79米长」）；
  2. 原文被擦掉、新字没写上，原位剩一块空白（v1.0.5 那批竖排标注）。

两类问题的共同点：文件已经生成、界面还报「完成」，得翻到那一页才发现。
所以这一层放在落盘之前 —— 复核不过就不出文件，先把问题摆到人面前。

复核分两段：
  规则段：本地算，零成本。查「写的字在不在」「还有没有原文语言」
          「有没有一处都没译的页」「换算数值复算对不对」。
  AI 段：只抽查可疑条目，不逐条重翻。可疑的判定见 Translator._review_worthy，
         主要是换算过的、和靠单位换算才通过数字校验的那批 —— 这类最容易
         「数字看着对、意思已经走样」。

v1.0.8 起，复核不再只是「过 / 不过」的开关，而是流程里的一环：
复核发现的问题按「能不能重写」分三类，能重写的打回去重译，改完再审。

  retry     有擦写框，能定位能重写 —— 打回重译
  tolerable 定位不到框，技术上就没法原位替换（印章、水印、图形边缘的字）
            —— 单独提示，允许跳过后出图
  fatal     能定位但重写不好，或者本身不是「翻译」的问题（整页无译文、
            纯尺寸标注的数值复算不符）—— 必须阻断

这三类的判据只有一条：**能不能定位到可擦写的框**。能定位就算错误，
不能定位就算疑似不可译。好处是不用图像识别去猜「这是不是印章」，
代价是可能把个别位置丢失的、真该翻的字也归进不可译 —— 所以它只做
「提示并允许跳过」，不静默放行。
"""
import re

# 判定为「空白块」时，回读原文框要外扩多少点。
# 字写进去时可能因为缩字号略高于原框，取大了会把邻行的字误当成自己的，取小了
# 又会把「字确实写了、只是偏了一点」误判成空白。2pt 是实测能分开的量。
_READ_PAD = 2.0

# 判断译文是否真的写上去了，取开头这么多个非空白字符去页面里找。
# 太短会撞上别处的字（"Red" 到处都是），太长又会因为断行、连字符对不上。
_FRAG_LEN = 8

_CJK = re.compile(r"[\u4e00-\u9fff]")
# 「算残留」的最低门槛：连续两个汉字。单个汉字多半是符号化的东西
# （「见」「注」），或者是图纸上的印章边缘，连着两个才算一句没翻的话。
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
# 目标语言是中文时，反过来查「还是英文单词」的情况。要求 4 个以上连续字母，
# 免得把 mm、A、B 这些代号和单位也算成没翻。
_LATIN_WORD = re.compile(r"[A-Za-z]{4,}")


def _is_leftover(text, dst_lang):
    """
    这条**没译出来**的原文，算不算「页面上还留着不该有的语言」。

    3mm、⌀20、A 这类碎片不算：它们没有可译内容，留在图上是对的。
    「2根5毫米厚镀锌铁板2.79米长」在英文产出里就算 —— 那是一句没翻的话。
    """
    if not text:
        return False
    if dst_lang == "zh":
        return bool(_LATIN_WORD.search(text))
    return bool(_CJK.search(text))


def _norm(s):
    """去掉所有空白再比：PDF 抽出来的文字断行、加空格的情况太多"""
    return re.sub(r"\s+", "", s or "")


def fix_key(page, src):
    """
    登记表 / 问题的配对键：同一页 + 同一句原文 = 同一处。

    必须去掉空白再比：复核是从页面回读出来判的，读回来的字符串
    常常多一个空格或少了断行处的一个空格，原样比会对不上。
    """
    try:
        pno = int(page or 0)
    except (TypeError, ValueError):
        pno = 0
    return (pno, _norm(src))


def _fragment(text):
    """取译文开头一小段可检索的字符，用来确认字真的落到了页面上"""
    compact = _norm(text)
    if len(compact) <= _FRAG_LEN:
        return compact
    return compact[:_FRAG_LEN]


class Issue:
    """
    一条复核问题。

    refs 是「能重写这条的登记记录」，由 ReviewReport 从 fixmap 里查出来挂上。
    有 refs 说明这一处知道在哪个框、能擦能写，属于「可以打回重译」；
    没有 refs 又属残留，说明连位置都没有，那是印章水印这类，只能提示。
    """

    __slots__ = ("page", "kind", "text", "detail", "src", "localized", "refs")

    def __init__(self, page, kind, text, detail, src=None, localized=True):
        self.page = page          # 1 起的页码，0 表示整份文件层面
        self.kind = kind          # blank / residue / coverage / number / ai
        self.text = text          # 涉及的截图文字（给人看）
        self.detail = detail      # 给人看的说明
        self.src = src if src is not None else (text or "")   # 原文，配对用
        self.localized = localized   # 是否定位到了擦写框
        self.refs = []            # 可重写的登记记录

    @property
    def category(self):
        """retry / tolerable / fatal，见模块顶部说明"""
        if self.refs:
            return "retry"
        if self.kind == "residue" and not self.localized:
            return "tolerable"
        return "fatal"

    def as_tuple(self):
        return (self.page, self.kind, self.text, self.detail)

    def line(self):
        where = f"第 {self.page} 页" if self.page else "整份文件"
        head = {
            "blank": "空白块",
            "residue": "原文未处理",
            "coverage": "没有译文",
            "number": "数值可疑",
            "ai": "语义可疑",
        }.get(self.kind, self.kind)
        return f"  {where} · {head}：{self.text}　—　{self.detail}"


class ReviewReport:
    """
    一份文件的复核结果。

    只要还有 retry / fatal 类问题就算「没通过」—— 用户选的是严格阻断，
    不设软硬之分。这么定是因为「小问题」在图纸上没有小问题：一句没翻的中文、
    一块空白，客户拿去就是交付事故。

    唯一的例外是 tolerable：印章、水印这类连位置都拿不到的字，
    技术上没法原位替换，一直拦着只会让人永远出不了图。所以单独列出来
    提示，由调用方决定是「继续出图」还是「改原图重跑」。
    """

    def __init__(self, fixmap=None):
        self.issues = []
        self.notes = []             # 不阻断、但值得顺带看一眼的提示
        self.checked_writes = 0     # 核过多少处写入
        self.checked_pages = 0      # 核过多少页残留
        self.ai_checked = 0         # AI 抽查了多少条
        self.ai_note = ""           # AI 段为什么没跑（没接口/没可疑条目）
        self.fixmap = fixmap or {}  # {fix_key: [登记记录]}，用来判能不能重写
        # 每页「我们动过手」的区域。文字层兜底扫描要靠它分辨两种残留：
        # 落在这些框里的，是我们自己写坏的字（该拦）；
        # 落在框外的，是从来没被识别过的印章水印（改不动，只能提示）。
        self._page_rects = {}
        for (pno, _key), recs in self.fixmap.items():
            for r in recs:
                rect = r.get("rect") if isinstance(r, dict) else None
                if rect is not None:
                    self._page_rects.setdefault(pno, []).append(rect)
        self.round = 1              # 这是第几轮复核（重译后会再审一次）
        self.history = []           # 每轮的问题数，给最终报告用
        self.retried = 0            # 累计重译了多少条

    def covers(self, pno, x0, y0, x1, y1):
        """这块区域有没有被我们写过（用来分辨「自己写坏的」和「印章水印」）"""
        for r in self._page_rects.get(pno, []):
            try:
                if not (x1 < r.x0 or r.x1 < x0 or y1 < r.y0 or r.y1 < y0):
                    return True
            except AttributeError:
                continue
        return False

    def add(self, page, kind, text, detail, src=None, localized=True):
        it = Issue(page, kind, text, detail, src=src, localized=localized)
        if self.fixmap:
            # 只有文字层 / 位图层的实际写入有登记记录，能查到才说明可重写
            it.refs = list(self.fixmap.get(fix_key(page, it.src), []))
        self.issues.append(it)
        return it

    @property
    def blocking(self):
        """
        还有没有没解决的问题。

        **注意：它不再决定「出不出文件」。** v1.0.9 起一律出图，这个名字
        沿用下来只是因为它在报告里表示「这份不正干净」，别再拿它当落盘开关。

        tolerable 也不算「干净」：印章、水印没处理就是没处理，
        文件照出，但清单里得有它。
        """
        return any(it.category != "tolerable" for it in self.issues)

    @property
    def tolerable(self):
        """只能提示、改不动的那批（印章 / 水印 / 无法定位的碎片）"""
        return [it for it in self.issues if it.category == "tolerable"]

    def retry_targets(self):
        """
        要打回重译的条目：[(登记记录, Issue), ...]

        同一处可能被两条问题指到（比如既算空白块又抽出数值不符），
        按记录的 id 去重，避免同一处写两遍。
        """
        seen, out = set(), []
        for it in self.issues:
            for rec in it.refs:
                if id(rec) in seen:
                    continue
                seen.add(id(rec))
                out.append((rec, it))
        return out

    def count_by_kind(self):
        out = {}
        for it in self.issues:
            out[it.kind] = out.get(it.kind, 0) + 1
        return out

    def summary(self):
        """一行话，贴在界面日志末尾"""
        if not self.blocking:
            base = (f"复核通过：核了 {self.checked_writes} 处写入 · "
                    f"{self.checked_pages} 页文字层 · {self.ai_checked} 条语义抽查")
            if self.round > 1:
                base = f"第 {self.round} 轮" + base
            if self.retried:
                base += f" · 重译 {self.retried} 处"
            tol = self.tolerable
            if tol:
                base += f" · 另有 {len(tol)} 处无法定位的残留（见下）"
            return base
        by = self.count_by_kind()
        names = {"blank": "空白块", "residue": "原文未处理",
                 "coverage": "整页无译文", "number": "数值可疑", "ai": "语义可疑"}
        parts = " · ".join(f"{names.get(k, k)} {v} 处" for k, v in by.items())
        # 「已出图」必须写在前面：v1.0.9 起复核不再拦文件，
        # 只报「未通过」会让人以为白跑一趟，其实文件已经在文件夹里了
        return f"第 {self.round} 轮复核未通过（文件已照常生成）：{parts}"

    def tolerable_lines(self):
        """
        「定位不到框、只能保留」的那批，单独一段给人看。

        单独拆出来是因为它和「问题清单」的处置完全不同：清单要求改图重跑，
        这一段只要求确认一下「这些确实不用翻」就能直接用文件。
        """
        tol = self.tolerable
        if not tol:
            return []
        out = ["以下位置没有替换，按「不可译内容」保留（多为印章、水印、图形里的字）："]
        out.extend(it.line() for it in tol[:20])
        if len(tol) > 20:
            out.append(f"  ……另有 {len(tol) - 20} 处未列出")
        out.append("  确认无误可直接使用该文件；若其中有该翻的内容，请改原图后重跑。")
        return out

    def lines(self, limit=60):
        """完整问题清单，界面和报告文件共用"""
        out = [f"复核结果：{self.summary()}"]
        if self.history:
            out.append("复核过程：" + " → ".join(self.history))
        if not self.blocking:
            tol = self.tolerable_lines()
            if tol:
                out.append("")
                out.extend(tol)
            if self.notes:
                out.append("")
                out.append("参考提示（不影响出文件，建议顺带核对）：")
                out.extend("  · " + str(n) for n in self.notes[:20])
                if len(self.notes) > 20:
                    out.append(f"  ……另有 {len(self.notes) - 20} 处未列出")
            return out
        out.append("")
        # 这句话决定用户下一步做什么：v1.0.9 起文件已经生成了，
        # 再写「文件没有生成」会让他去文件夹里翻半天找不存在的东西
        out.append("文件已生成，但以下位置没有处理好，请对照原图核对：")
        for it in self.issues[:limit]:
            out.append(it.line())
        if len(self.issues) > limit:
            out.append(f"  ……另有 {len(self.issues) - limit} 处未列出")
        out.append("")
        out.append("处理建议：")
        tips = []
        by = self.count_by_kind()
        if by.get("blank"):
            tips.append("「空白块」说明原文被擦掉但译文没写上，"
                        "多为该处空间太窄、字号缩到不可读。可把那条标注手动删短后重跑。")
        if by.get("residue"):
            tips.append("「原文未处理」说明页面上还留着没翻的字，"
                        "常见于图形里的文字（转曲、印章、水印），或框太窄放不下译文；"
                        "这类位置在本文件里保留原文，需要的话可手工补译。")
        if by.get("coverage"):
            tips.append("整页没有译文，通常是该页没有可识别文字，或识别通道没跑通。")
        if by.get("number") or by.get("ai"):
            tips.append("「数值可疑 / 语义可疑」是换算或翻译走样，"
                        "建议对照原图核对该处规格后再重跑。")
        if not tips:
            tips.append("请对照原图检查上述位置后重跑。")
        out.extend("  · " + t for t in tips)
        return out


def _mark_parts(m):
    """
    写入记录取值。v1.0.8 起记录改成字典（要带上原文和登记对象才能重写），
    这里保留元组分支，免得旧的自检脚本和外部调用直接崩。
    """
    if isinstance(m, dict):
        return (m.get("page"), m.get("rect"), m.get("out"), m.get("src"))
    try:
        return (m[0], m[1], m[2], None)
    except (TypeError, IndexError):
        return (None, None, None, None)


def review_document(doc, marks, dst_lang, untranslated=None,
                    metric_issues=None, metric_notes=None, translator=None,
                    fixmap=None, round_no=1, retried=0):
    """
    整份复核。doc 必须是**已经改完、还没落盘**的那份。

    marks          写入记录，字典列表 {page, rect, out, src}
    dst_lang       目标语言（zh / en / ja …），用来判什么是「残留」
    untranslated   未译清单 [(页码, 原文)]
    metric_issues  换算式复算报出的**确凿**疑点，阻断。
                   可传字典 {text, src, page}（能配对打回重译），
                   也可传纯字符串（只报不改）
    metric_notes   复算报出的**启发式**提示（如同句单位个数不等），只提示不阻断
    translator     传了才做 AI 抽查；没传就只跑规则段
    fixmap         可重写登记表 {(页码, 去空白原文): [记录]}。
                   传了才能判出哪些问题能打回重译，不传则一律算「改不动」
    round_no       第几轮复核（重译后复审会加一），只用于话术
    retried        前面几轮累计重译了多少条，只用于话术
    """
    rep = ReviewReport(fixmap=fixmap)
    rep.round = round_no
    rep.retried = retried

    # ---- 一、写的字到底在不在页面上 ----
    # 这是「空白块」唯一可靠的判据：擦除是不可逆的，只能写完再回读。
    for m in (marks or []):
        pno, rect, expected, src = _mark_parts(m)
        if expected is None or rect is None:
            continue
        try:
            pno = int(pno)
        except (TypeError, ValueError):
            continue
        if pno < 1 or pno > len(doc):
            continue
        frag = _fragment(expected)
        if not frag:
            continue
        rep.checked_writes += 1
        try:
            clip = rect + (-_READ_PAD, -_READ_PAD, _READ_PAD, _READ_PAD)
            got = doc[pno - 1].get_text("text", clip=clip)
        except Exception:
            continue
        if frag not in _norm(got):
            rep.add(pno, "blank", expected[:40],
                    "原位没有读回译文，可能是被擦成空白或字太小没写进去",
                    src=src or expected)

    # ---- 二、页面上还有没有原文语言的字 ----
    #
    # 分两路查，因为转曲文件和普通文件得用不同判据：
    #
    # (a) 未译清单：任何一条没译出来的原文，页面上就还是它。
    #     这一路才是主力。转曲 PDF 的文字已经变成曲线，get_text 读不到任何字，
    #     下面 (b) 在那种文件上永远返回「干净」—— 客户报的那两处中文漏译
    #     就属于这种，靠 get_text 是查不出来的。
    # (b) 文字层回读：抓 (a) 覆盖不到的，比如页面上一句既没被识别、
    #     也没进未译清单的文字（OCR 完全漏掉的那种）。
    leftover_norms = []
    if untranslated:
        for entry in untranslated:
            try:
                pno, text = entry[0], entry[1]
            except (TypeError, IndexError):
                continue
            if _is_leftover(text, dst_lang):
                leftover_norms.append(_norm(str(text)))
                rep.add(pno or 0, "residue", str(text)[:40],
                        "这一条没有译出，原位仍是原文", src=str(text))

    # 目标语言是中文时不查文字层：那本来就是我们要写上去的字。
    if dst_lang != "zh":
        for i in range(len(doc)):
            page = doc[i]
            # 用 blocks 而不是整段 text：能顺带拿到位置，报告里指得准
            try:
                blocks = page.get_text("blocks")
            except Exception:
                continue
            rep.checked_pages += 1
            seen = set()
            for b in blocks:
                if len(b) < 5:
                    continue
                if not isinstance(b[4], str):
                    continue
                # 先看这块区域我们动过手没有。动过的说明字是我们写的
                # （或者是改写时漏掉的同一块文字），那是真错误；
                # 没动过的说明它从来没被识别成可处理的文字，
                # 属于印章、水印、图形边缘那一类，技术上没法原位替换。
                touched = rep.covers(i + 1, b[0], b[1], b[2], b[3])
                for m in _CJK_RUN.finditer(b[4]):
                    word = m.group()
                    if word in seen:
                        continue
                    seen.add(word)
                    # 未译清单里已经报过的那几句，这里不再重复报 ——
                    # 同一句会被两条路径各抓一次，清单里就成了「同一处两条问题」，
                    # 用户对着数会发现对不上
                    if any(word in t for t in leftover_norms):
                        continue
                    rep.add(i + 1, "residue", word,
                            ("这一句仍是中文，没有翻译" if touched else
                             "这一句仍是原文，没有翻译（未定位到可替换的文字框）"),
                            src=word, localized=touched)

    # ---- 三、整份一条都没译出来 ----
    # 文件能生成、但内容和原文一样，是最容易被当成成功的一种失败。
    if untranslated is not None and marks is not None:
        if not marks and untranslated:
            rep.add(0, "coverage", f"{len(untranslated)} 条待译文本",
                    "一个字都没能替换，这份文件和原件一样")

    # ---- 四、换算数值复算没过 ----
    # translator 侧在出口会把每个换算值和原文独立复算一遍，算不上的记在账本里。
    for item in (metric_issues or []):
        # 字典形式带页码和原文，能配对到登记记录 → 可以打回重译
        if isinstance(item, dict):
            rep.add(item.get("page") or 0, "number", str(item.get("text", "")),
                    "换算后的数值与原文复算结果对不上",
                    src=str(item.get("src") or ""))
        else:
            rep.add(0, "number", str(item), "换算后的数值与原文复算结果对不上")
    # 启发式的那些只当提示：它们误报不少（见 MetricReport 的 noticed 说明），
    # 拿它们拦文件会把正确译文一起卡住。
    for item in (metric_notes or []):
        text = str(item)
        if text not in rep.notes:      # 同一句会被反复校验，提示别刷屏
            rep.notes.append(text)

    # ---- 五、AI 抽查 ----
    if translator is not None:
        pairs = translator.review_candidates()
        if not pairs:
            rep.ai_note = "没有可疑条目，跳过语义抽查"
        elif not getattr(translator, "llm", None) or \
                not translator.llm.available():
            rep.ai_note = "未配置 AI 接口，语义抽查跳过"
        else:
            try:
                bad = translator.review_pairs(pairs)
                rep.ai_checked = len(pairs)
                for it in bad:
                    rep.add(it.get("page") or 0, "ai",
                            str(it.get("text", "")),
                            str(it.get("problem") or "译文与原文内容不一致"),
                            src=str(it.get("text", "")))
            except Exception as exc:
                # 抽查本身出错不该否掉整份文件：规则段已经查过硬伤了。
                # 但要说清楚，不能让人以为查过了。
                rep.ai_note = f"语义抽查没跑成（{type(exc).__name__}），" \
                              f"只做了规则校验"
    else:
        rep.ai_note = "未做语义抽查"

    return rep
