"""
术语词典匹配引擎（离线，零依赖，零网络）

设计要点：
  1. 图纸标注大量是「标准术语 + 尺寸符号 + 牌号」的混合体，
     逐词机翻容易翻歪（把 DN50 翻成 "DN fifty"），
     所以必须先「保护不译元素」，再对剩余文字做最长匹配。
  2. 中文无空格，必须用最长匹配前向扫描；英文有空格，用 n-gram 最长匹配。
  3. 命中即为专业术语，准确率 100%；未命中的部分如实标记，交由上层决定
     是否走在线或 LLM 通道。
"""

import csv
import os
import re
import unicodedata

from . import edition
from .paths import DATA_DIR

GLOSSARY_PATH = os.path.join(DATA_DIR, "glossary.csv")


def glossary_paths():
    """
    要加载的术语库文件，按优先级从高到低。

    定制版把自己的锁标词表排在最前面：客户指定了「托盘 = rack」，
    这条就必须压过通用库里同名的写法。加载用的是 setdefault（先到先得），
    所以顺序即优先级，后面那份只在前面没有该词条时补位。

    专属库缺失时只少一份词表，不影响启动 —— 客户把它误删了，
    程序照样能翻，只是回到通用译法。
    """
    paths = []
    extra = edition.extra_glossary()
    if extra:
        p = os.path.join(DATA_DIR, extra)
        if os.path.exists(p):
            paths.append(p)
    if os.path.exists(GLOSSARY_PATH):
        paths.append(GLOSSARY_PATH)
    return paths

# ---------------------------------------------------------------------------
# 不译元素：这些内容属于规格、牌号、标准号，翻译反而错，必须原样保留。
# 用「占位符」把它们从句子中摘出来，避免被切词匹配破坏。
# ---------------------------------------------------------------------------
# 注意顺序有意义：先匹配更具体的形态，再匹配更宽泛的形态
PROTECT_PATTERNS = [
    # 标准号：GB/T 1804、ISO 2768-1、DIN 7168、ASTM A36、JIS G3101
    r"(?:GB|GBT|GB/T|ISO|DIN|ASTM|JIS|EN|ANSI|BS|SUS|AISI)\s*[\d][\d\.\-:/]*[A-Za-z]?",
    # 螺纹：M8、M8×1.25、M10x1.5
    r"M\s*\d+(?:\.\d+)?\s*[×xX*]\s*\d+(?:\.\d+)?",
    r"\bM\d{1,3}\b",
    # 直径/半径标注：Ø50、Φ20、⌀8、ø12、∅12、R5、R2.5
    #
    # 小写 ø 必须一起收：真实图纸上最常见的就是它（CAD 字体里 o-slash 比 ⌀ 好打），
    # 而它跟大写的 Ø 是两个不同码位。漏掉它的后果不是「认不出直径」这么轻 ——
    # 这条尺寸会被当成普通词送去翻译，模型再把 ø 写成 @ 印到图上。
    r"[ØøΦφ⌀∅]\s*\d+(?:\.\d+)?",
    r"\bR\d+(?:\.\d+)?\b",
    # 公差标注：50±0.1、+0.2/-0.1
    r"\d+(?:\.\d+)?\s*±\s*\d+(?:\.\d+)?",
    r"[+\-]\s*\d+\.\d+",
    # 粗糙度：Ra3.2、Ra 0.8
    r"Ra\s*\d+(?:\.\d+)?",
    # 视图编号：A-A、B-B、SECTION A-A 里的编号属于代号，必须原样保留
    r"\b[A-Z]-[A-Z]\b",
    # 角度与度数：45°、90°
    r"\d+(?:\.\d+)?\s*°",
    # 材料牌号：304、316L、Q235、6061-T6、SUS304、SPCC
    r"\b\d{3,4}[A-Z]?\b(?=\s*(?:不锈钢|碳钢|铝|钢|STAINLESS|STEEL|AL))",
    r"\b[A-Z]{2,4}\d{2,4}[A-Z]?\b",
    # 图号形态：ABC-2024-001、DWG123
    r"\b[A-Z]{2,6}[-_]?\d{3,}[-_]?\d*\b",
    # 带单位的数字：50mm、3.5kg、1200x1000x810
    r"\d+(?:\.\d+)?\s*[×xX*]\s*\d+(?:\.\d+)?(?:\s*[×xX*]\s*\d+(?:\.\d+)?)?",
    r"\d+(?:\.\d+)?\s*(?:mm|cm|m|kg|g|t|ml|l|L|°C|℃|N|Nm|kN|MPa|bar|psi|H|W|V|A|pcs|PCS|sets)\b",
    # 纯数字（含小数）。关键：前后都不能接字母或连字符，
    # 否则 "4-HOLE" 的 4、"A-2" 的 2 会被单独摘走，导致整个词组失配。
    r"(?<![A-Za-z\-_])\d+(?:\.\d+)?(?![A-Za-z\-_])",
]
PROTECT_RE = re.compile("|".join(f"(?:{p})" for p in PROTECT_PATTERNS))

# 占位符前后用不可见控制符包裹，避免与正文冲突
PH_OPEN, PH_CLOSE = "\ue000", "\ue001"
PH_RE = re.compile(PH_OPEN + r"(\d+)" + PH_CLOSE)

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize(text: str) -> str:
    """规范化：全角转半角、统一直径符号、压缩空白、统一中英标点"""
    if not text:
        return ""
    # 全角字母数字标点 → 半角（NFKC 会把 ＡＢＣ１２３ 转成 ABC123）
    text = unicodedata.normalize("NFKC", text)
    # 各种直径写法统一成 Ø，便于后续识别。
    # 小写 ø 也要折进来：它和大写 Ø 是两个不同码位，词典与代号表里只存 Ø，
    # 不折的话「ø12」进不了直径标注那条规则，会被当普通词翻译掉。
    for ch in ("Φ", "φ", "⌀", "ϕ", "ø", "∅"):
        text = text.replace(ch, "Ø")
    # 中文冒号/逗号/括号统一成英文半角，词典里只存半角形态
    trans = {ord("："): ":", ord("，"): ",", ord("（"): "(", ord("）"): ")",
             ord("；"): ";", ord("、"): ","}
    text = text.translate(trans)
    # 压缩连续空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


# 只在短语里有意义、单独出现时不能替换的词。
#
# 来源：术语表里收了一条 NO → 不允许有（配合 NO SCRATCH ON SURFACE 这类整句）。
# 于是标题栏的 DRAWING NO. 被拆成 DRAWING + NO.，NO 单独命中「不允许有」，
# 整行变成「图纸不允许有.」—— 标题栏最高频的字段之一被译成了错话。
# 这些词在图纸上是语法成分（否定词、冠词、介词），不是术语，
# 单独出现时保留原文，比猜一个意思安全。
_NOT_STANDALONE = {
    "NO", "NOT", "AND", "OR", "OF", "FOR", "WITH", "WITHOUT", "THE",
    "TO", "IN", "ON", "AT", "BY", "AS", "PER", "BE", "IS", "ARE",
}


def _key_norm(s: str) -> str:
    """
    查表用的归一形式：去掉词尾标点，并把「句点 + 空格」当成词分隔。

    为什么需要：术语表收的是 "DRAWING NO."（带点），而图纸上同一格
    可能写成 "DRAWING NO.: SB-001"（后面直接跟冒号）。不说这个点，
    整条就查不到，退化成逐词翻译，标题栏就出现「图纸 NO.:」这种半截译文。

    句点后跟空格的规则来自 "REV. NO."（版本号）这类写法：
    点在这里只是缩写的结尾，不是数字的小数点，去掉才能对上 "REV NO"。
    小数点后面不跟空格，所以 "5.00MM" 不会被拆坏。
    """
    s = (s or "").strip()
    s = re.sub(r"\.\s+", " ", s)
    return re.sub(r"[.:;,]+$", "", s)


def _key_loose(s: str) -> str:
    """
    更宽松的归一形式：在 _key_norm 的基础上把连字符当成空格。

    为什么需要：同一件东西，术语表收的是 HOT DIP GALVANIZED，
    图纸上却常写成 Hot-dip galvanized。连字符只影响书写习惯，
    不改变它指的东西，所以查表时两种写法应当等价。
    这条只在精确匹配都失败后才用，避免把 X-RAY 这种真复合词拆坏。
    """
    return re.sub(r"\s+", " ", _key_norm((s or "").replace("-", " ")).strip())


def _singular_variants(word: str):
    """一个词的复数还原候选：GALVANIZED → GALVANIZE / GALVANIZED → GALVANIZ"""
    w = (word or "").upper()
    out = []
    if w.endswith("IES") and len(w) > 4:
        out.append(w[:-3] + "Y")
    if w.endswith("ES") and len(w) > 3:
        out.append(w[:-2])
    if w.endswith("S") and len(w) > 2:
        out.append(w[:-1])
    return out


def _lookup_phrase(seg, gl):
    """
    查一个词组（1 个或多个词），失败返回 None。

    四级回退，顺序有讲究：
      1. 原样 —— 作者写什么就是什么，最可信
      2. 去尾标点 —— DRAWING NO. 与 DRAWING NO 是同一条
      3. 连字符当空格 —— Hot-dip 与 HOT DIP 是同一条
      4. 末词复数还原 —— GALVANIZED 在表里收作 GALVANIZE
    每一级都命中就停，不回退到更松的那级去「找点东西出来」。
    """
    key = " ".join(p[1] for p in seg).upper()
    hit = gl.en2zh.get(key)
    if hit:
        return hit, False

    hit = gl.en2zh_norm.get(_key_norm(key))
    if hit:
        return hit, True

    hit = gl.en2zh_loose.get(_key_loose(key))
    if hit:
        return hit, True

    # 末词复数还原：只动最后一个词，其余保持
    if len(seg) >= 1:
        cores = [p[1] for p in seg]
        for cand in _singular_variants(cores[-1]):
            trial = " ".join(cores[:-1] + [cand]).upper()
            hit = (gl.en2zh.get(trial)
                   or gl.en2zh_norm.get(_key_norm(trial))
                   or gl.en2zh_loose.get(_key_loose(trial)))
            if hit:
                return hit, True
    return None, False


def _read_csv_rows(path):
    """
    读术语表，返回行列表（含表头）。读不出来返回空列表。

    为什么要试几种编码：
        这份 CSV 是明确交给客户编辑的。客户用 Excel 另存，默认不一定是 UTF-8；
        用记事本「另存为 ANSI」在老机器上就是 GBK。编码不对时 open 不报错，
        读到第一行才抛 UnicodeDecodeError —— 而那一下如果没人接，
        整个程序打不开，客户看到的是「双击没反应」。
        术语表读不出来只是少一份词表，程序必须照常能翻。
    """
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            # 表头行单独丢掉：调用方已经按固定列位解析，
            # 留着它只会多一条「中文=中文」的废词条
            return rows[1:] if rows else []
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


class Glossary:
    """术语库：同时持有 中→英 与 英→中 两张表"""

    def __init__(self, path: str = None):
        self.zh2en = {}
        self.en2zh = {}
        self.en2zh_norm = {}        # 去尾标点的查表副本，见 _key_norm
        self.en2zh_loose = {}       # 连字符当空格的副本，见 _key_loose
        self.zh_maxlen = 1          # 中文词条最大字数，用于最长匹配扫描
        self.en_maxwords = 1        # 英文词条最大词数，用于 n-gram 匹配
        # 锁定词：{中文: (指定译名, [禁用错译, ...])}。
        # 只有填了第 6 列「勿用」的词条才进这里，见 _load 的说明。
        self.locked = {}
        # 指定写法：{英文词: 该写成什么样}。第 7 列。
        # 译文默认全大写（见 core/casing.py），客户想让某个词保持自己的写法时，
        # 在这一列写死。空表就是「全按默认规则」。
        self.case_map = {}
        # 不传 path 时按发行版自动取：定制版专属库在前，通用库在后。
        # 传了 path 就只读那一份 —— 自检和测试脚本需要能精确指定。
        for p in ([path] if path else glossary_paths()):
            self._load(p)

    def _load(self, path: str):
        """
        读取 CSV 术语表。列：中文, English, 分类[, 方向[, 备注]]

        第 4 列「方向」是给有歧义的词条用的，留空表示双向都收：

            z2e  只当中→英的词条用。英文那边有别的更常见意思时不收反向。
                 典型是「托盘=rack」：中文说托盘就是 rack，但英文 rack
                 在别的图纸里多半是「货架/机架」，反向收进来会把来图译错。
            e2z  反过来，只当英→中的词条用。

        用关键字精确匹配而不是「有值就是方向」：这份 CSV 是给客户直接编辑的，
        有人会在第 4 列写备注，那时候不能被当成方向把整条词废掉。
        """
        if not os.path.exists(path):
            return
        for row in _read_csv_rows(path):
            if len(row) < 2:
                continue
            zh, en = normalize(row[0]), normalize(row[1])
            if not zh or not en:
                continue
            direction = (row[3].strip().lower() if len(row) > 3 else "")
            only_z2e = direction in ("z2e", "zh2en", "中→英", "中译英")
            only_e2z = direction in ("e2z", "en2zh", "英→中", "英译中")
            # 中→英：保留首次出现（文件靠前的优先，整句模板放前面）
            if not only_e2z:
                self.zh2en.setdefault(zh, en)
            # 英→中：英文统一大写入表，匹配时也大写化
            if not only_z2e:
                self.en2zh.setdefault(en.upper(), zh)
                # 同一个词条的「去尾标点」形式另存一份，供带冒号/句点的写法命中。
                # 两张副本都**无条件**存：只存「与原写法不同」的条目，
                # 会让没有连字符的词条反倒在宽松表里缺失 ——
                # 图纸写成 HOT-DIP、表里收 HOL DIP 时，宽松表就查不到。
                # 669 条撑到两倍也就千余条，这点内存不值得拿正确性换。
                nk = _key_norm(en.upper())
                if nk:
                    self.en2zh_norm.setdefault(nk, zh)
                # 连字符形式的副本：HOT-DIP GALVANIZE 与 HOT DIP GALVANIZED 互通
                lk = _key_loose(en.upper())
                if lk:
                    self.en2zh_loose.setdefault(lk, zh)
            # 第 6 列「勿用」：这个词在英文里绝不该出现的几种写法。
            #
            # 为什么需要它：词库只在「整词命中」时把中文换成英文，可图纸上的
            # 文字多数是半句、整句，走的是在线机翻或模型通道，那两条通道
            # 根本不查词库。客户说「托盘必须是 rack」，机器给出 pallet，
            # 词库里写得再清楚也没用 —— 只能等译文回来再纠一遍。
            #
            # 只列「绝不可能指这个东西」的词。像 base plate 这种既可能是
            # 底座也可能真是底板，列进来会越改越错。
            #
            # 注意这段**不在**上面那个 if 里：方向标了 z2e 的词恰恰是最需要
            # 纠错的（托盘、底座都是），跟着方向判断走会让它们一条都进不来。
            if len(row) > 5 and row[5].strip() and not only_e2z:
                wrongs = [w.strip() for w in
                          re.split(r"[|;；,，]", row[5]) if w.strip()]
                if wrongs:
                    self.locked.setdefault(zh, (en, wrongs))
            # 第 7 列「指定写法」：这一条译文必须写成这个样子，不受全大写规则影响。
            #
            # 两种写法都收：
            #   pcs=PCS      明确指定「pcs 这个词写成 PCS」
            #   Rack         只写形态时，用自身当键（忽略大小写匹配），
            #                即「rack 这个词写成 Rack」
            #
            # 为什么放在词库而不是大小写模块里：客户改的是这份 CSV，
            # 让他另外去改程序的配置，下次换台机器就丢了。
            if len(row) > 6 and row[6].strip() and not only_e2z:
                for item in re.split(r"[|;；,，]", row[6]):
                    item = item.strip()
                    if not item:
                        continue
                    if "=" in item:
                        k, v = item.split("=", 1)
                        if k.strip() and v.strip():
                            self.case_map[k.strip()] = v.strip()
                    else:
                        self.case_map[item] = item
            if CJK_RE.search(zh):
                self.zh_maxlen = max(self.zh_maxlen, len(zh))
            self.en_maxwords = max(self.en_maxwords, len(en.split()))

    def __len__(self):
        return len(self.zh2en)


def _protect(text: str):
    """把不译元素替换成占位符，返回 (处理后文本, 原件列表)"""
    kept = []

    def _sub(m):
        kept.append(m.group(0))
        return f"{PH_OPEN}{len(kept) - 1}{PH_CLOSE}"

    return PROTECT_RE.sub(_sub, text), kept


def _restore(text: str, kept):
    """把占位符换回原文。占位符在匹配过程中可能被空格隔开，做容错处理"""
    def _sub(m):
        idx = int(m.group(1))
        return kept[idx] if idx < len(kept) else ""
    out = PH_RE.sub(_sub, text)
    # 容错：占位符被拆开（如 PH 数字 PH 中间混入空格）
    out = out.replace(PH_OPEN, "").replace(PH_CLOSE, "")
    return out


# ---------------------------------------------------------------------------
# 固定句式规则
# 图纸标题栏里有一批高度模板化的短句（Sheet 3 of 7、Designed by xxx），
# 机翻会翻得很怪（"第3张的7"），这类必须用规则直接生成。
# 规则只做整条精确匹配，不做局部替换，避免误伤。
# ---------------------------------------------------------------------------
EN_RULES = [
    (r"^sheet\s+(\d+)\s+of\s+(\d+)$", r"第 \1 页，共 \2 页"),
    (r"^page\s+(\d+)\s+of\s+(\d+)$", r"第 \1 页，共 \2 页"),
    (r"^(\d+)\s+of\s+(\d+)$", r"第 \1 页，共 \2 页"),
    (r"^designed\s+by\s+(.+)$", r"设计：\1"),
    (r"^drawn\s+by\s+(.+)$", r"制图：\1"),
    (r"^checked\s+by\s+(.+)$", r"审核：\1"),
    (r"^approved\s+by\s+(.+)$", r"批准：\1"),
    (r"^rev\.?\s*([A-Za-z0-9]+)$", r"版本 \1"),
    (r"^revision\s*[:：]?\s*([A-Za-z0-9]+)$", r"版本 \1"),
    # 下面几条是「标签: 值」句式。值不能以冒号开头 ——
    # 原来的 (.+) 是贪婪的，[:：]? 可以不吃那个冒号，让 (.+) 把它吃进去，
    # 于是单独一个 "MATERIAL:" 被展开成 "材质：:"（两个冒号）。
    (r"^scale\s*[:：]?\s*([^:：\s].*)$", r"比例 \1"),
    (r"^date\s*[:：]?\s*([^:：\s].*)$", r"日期 \1"),
    (r"^qty\.?\s*[:：]?\s*(\d+)$", r"数量 \1"),
    (r"^material\s*[:：]?\s*([^:：\s].*)$", r"材质：\1"),
    (r"^finish\s*[:：]?\s*([^:：\s].*)$", r"表面处理：\1"),
    (r"^tolerance\s*[:：]?\s*([^:：\s].*)$", r"公差：\1"),
]

ZH_RULES = [
    (r"^第\s*(\d+)\s*页[,，]?\s*共\s*(\d+)\s*页$", r"SHEET \1 OF \2"),
    (r"^设计[:：]\s*(.+)$", r"DESIGNED BY \1"),
    (r"^制图[:：]\s*(.+)$", r"DRAWN BY \1"),
    (r"^审核[:：]\s*(.+)$", r"CHECKED BY \1"),
    (r"^批准[:：]\s*(.+)$", r"APPROVED BY \1"),
    (r"^版本\s*([A-Za-z0-9]+)$", r"REV. \1"),
]


def _apply_rules(text: str, rules):
    """整条套用固定规则，命中返回结果，否则返回 None"""
    for pattern, repl in rules:
        m = re.match(pattern, text.strip(), re.IGNORECASE)
        if m:
            return m.expand(repl)
    return None


def _tidy(text: str) -> str:
    """
    收尾排版修正。译文最终要放回图纸，格式必须干净：
      - 中文标点转成西文标点（中译英时不能残留「。」「、」）
      - 去掉标点前的多余空格（"MATERIAL :" → "MATERIAL:"）
      - 去掉中文字符两侧的空格（中文排版不留空格）
      - 压缩连续空白
    """
    punct_map = {"。": ".", "、": ",", "；": ";", "：": ":", "！": "!", "？": "?"}
    for zh_p, en_p in punct_map.items():
        text = text.replace(zh_p, en_p)
    text = re.sub(r"\s+([:;,.\)\]])", r"\1", text)
    text = re.sub(r"([\(\[\{])\s+", r"\1", text)
    # 中文与相邻字符之间不留空格
    text = re.sub(r"\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_punct(tok: str):
    """
    把英文 token 拆成 (前缀标点, 核心词, 后缀标点)。
    词典里只存干净词条，所以匹配前要剥掉 "MATERIAL:" 的冒号、"（" 等。
    """
    m = re.match(r"^([^\w]*)(.*?)([^\w]*)$", tok)
    if not m:
        return "", tok, ""
    return m.group(1), m.group(2), m.group(3)


def _case(s: str) -> str:
    """
    词典通道的译文大小写，与 core/casing.apply_style 走同一个发行版开关。

    定制版转全大写（图纸标注惯例）；通用版保持词表里的原写法 —— 通用版
    不做大小写风格干预，见 core/casing.py 开头的说明。
    """
    return s.upper() if edition.upper_case() else s


def zh_to_en(text: str, gl: Glossary):
    """
    中文 → 英文。
    返回 (译文, 未命中片段列表)。未命中片段非空说明有内容没翻译。
    """
    text = normalize(text)
    if not text:
        return "", []

    # 1. 整句模板优先：技术要求里的整句必须整体替换，逐词拼会读不通
    if text in gl.zh2en:
        # 图纸英文标注惯用全大写（定制版）；通用版照词表写法
        return _case(gl.zh2en[text]), []

    # 1.5 固定句式规则（第 3 页，共 7 页 这类）
    ruled = _apply_rules(text, ZH_RULES)
    if ruled:
        return ruled, []

    # 2. 保护不译元素
    work, kept = _protect(text)

    out, miss = [], []
    i = 0
    n = len(work)
    while i < n:
        # 占位符整体跳过（原样输出，稍后还原）
        if work[i] == PH_OPEN:
            j = work.find(PH_CLOSE, i)
            out.append(work[i:j + 1])
            i = j + 1
            continue

        ch = work[i]
        # 非中文（空格、标点、已保护的残留）原样保留
        if not CJK_RE.match(ch):
            out.append(ch)
            i += 1
            continue

        # 最长匹配：从当前汉字位置向回扫，找最长命中词条
        matched = False
        for size in range(min(gl.zh_maxlen, n - i), 0, -1):
            chunk = work[i:i + size]
            if chunk in gl.zh2en:
                # 只大写译文本身，被保护的尺寸/牌号不参与大写，避免 Ra3.2 变成 RA3.2
                out.append(" " + _case(gl.zh2en[chunk]) + " ")
                i += size
                matched = True
                break
        if not matched:
            # 单字也未命中 → 记为未翻译
            miss.append(ch)
            out.append(ch)
            i += 1

    result = "".join(out)
    result = _tidy(_restore(result, kept))
    return result, miss


def _lookup_word(word: str, gl: Glossary):
    """
    查单个英文词，带复数回退。
    图纸上 "EXTENDER POSTS"、"2 HOLES" 这种复数写法很常见，
    词典只收单数，不做回退就永远查不到。
    """
    w = word.upper()
    if w in gl.en2zh:
        return gl.en2zh[w]
    if w.endswith("IES") and (w[:-3] + "Y") in gl.en2zh:
        return gl.en2zh[w[:-3] + "Y"]
    if w.endswith("ES") and w[:-2] in gl.en2zh:
        return gl.en2zh[w[:-2]]
    if w.endswith("S") and w[:-1] in gl.en2zh:
        return gl.en2zh[w[:-1]]
    return None


def en_to_zh(text: str, gl: Glossary):
    """
    英文 → 中文。
    英文有空格分词，用 n-gram 最长匹配（1..最多词数）。
    未命中的英文单词保留原文并记录。
    """
    text = normalize(text)
    if not text:
        return "", []
    # 标点后面粘着字母时补一个空格：SURFACE TREATMENT:Hot-dip 这种写法，
    # 不补空格会被当成一整串规格，匹配不到任何词条，整条原样留在图上。
    # 只在后面跟字母时补 —— 尺寸 1:2、5.00mm 里的标点不能动。
    text = re.sub(r"([,;:])(?=[A-Za-z])", r"\1 ", text)

    if text.upper() in gl.en2zh:
        return gl.en2zh[text.upper()], []

    # 固定句式规则（Sheet 3 of 7、Designed by xxx 这类）
    ruled = _apply_rules(text, EN_RULES)
    if ruled:
        return ruled, []

    work, kept = _protect(text)

    # 按空白切词，但保留占位符为独立 token
    tokens = re.findall(PH_OPEN + r"\d+" + PH_CLOSE + r"|\S+", work)
    # 预先拆出每 token 的 (前缀标点, 核心词, 后缀标点)，这样 "MATERIAL:" 也能命中 MATERIAL
    parts = [("", t, "") if t.startswith(PH_OPEN) else _split_punct(t) for t in tokens]
    out, miss = [], []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # 占位符直接透传
        if tok.startswith(PH_OPEN):
            out.append(tok + " ")
            i += 1
            continue

        matched = False
        # 从最长词组往短试，命中即用
        for size in range(min(gl.en_maxwords, len(tokens) - i), 0, -1):
            seg = parts[i:i + size]
            # 词组里混入占位符则不能整体匹配（否则会把数字吃掉）
            if any(not p[1] for p in seg):
                continue
            hit, from_loose = _lookup_phrase(seg, gl)
            key = " ".join(p[1] for p in seg).upper()
            # 单词查不到时，再试一次复数回退（POSTS → POST）
            if hit is None and size == 1:
                hit = _lookup_word(seg[0][1], gl)
                from_loose = hit is not None
            # 语法成分单独出现时不替换：NO 单独译成「不允许有」会把
            # DRAWING NO. 变成「图纸不允许有.」，而它在这条里只是「编号」
            if hit and size == 1 and _key_norm(key) in _NOT_STANDALONE:
                hit = None
            if hit:
                head, tail = seg[0][0], seg[-1][2]
                # 命中的是「带句点的词条」（DRAWING NO. → 图纸编号）时，
                # 那个句点是词条的组成部分，不该跟着译文后面再出现一次，
                # 否则图上会写成「图纸编号.: SB-001」。
                if from_loose and tail.startswith("."):
                    tail = tail[1:]
                out.append(head + hit + tail)
                i += size
                matched = True
                break
        if not matched:
            out.append(tok + " ")
            miss.append(tok)
            i += 1

    result = "".join(out)
    result = _tidy(_restore(result, kept))
    return result, miss


def build_case_style(sample: str):
    """
    判断原文大小写习惯，译文沿用，避免中英混排看起来风格不一。
    返回一个把文本套用该风格的函数。
    """
    letters = [c for c in sample if c.isalpha()]
    if not letters:
        return lambda s: s
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    if upper_ratio > 0.8 and len(letters) >= 2:
        return lambda s: s.upper()          # 图纸标注惯用全大写
    return lambda s: s


if __name__ == "__main__":
    gl = Glossary()
    print(f"术语库加载: {len(gl)} 条，中文最长 {gl.zh_maxlen} 字，英文最长 {gl.en_maxwords} 词\n")

    tests_zh = ["主阀体", "材质：不锈钢", "表面粗糙度 Ra3.2", "未注圆角 R2",
                "技术要求", "法兰 DN50", "图号 DWG-2024-001", "锐边倒钝",
                "去毛刺，不允许有锐边", "产品表面不允许有气泡缩水飞边"]
    for t in tests_zh:
        r, m = zh_to_en(t, gl)
        flag = "" if not m else f"   ←未译:{''.join(m)}"
        print(f"  中→英 {t!r:22} => {r!r}{flag}")

    print()
    tests_en = ["MAIN VALVE BODY", "FLANGE 4-HOLE", "SECTION A-A",
                "MATERIAL: STAINLESS STEEL", "HEX BOLT M8x30",
                "SURFACE ROUGHNESS Ra3.2"]
    for t in tests_en:
        r, m = en_to_zh(t, gl)
        flag = "" if not m else f"   ←未译:{' '.join(m)}"
        print(f"  英→中 {t!r:26} => {r!r}{flag}")
