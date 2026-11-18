"""
页面空间感知：译文该写在哪儿，先问页面。

为什么要有这一层
----------------
早先每条图内文字都是「盖自己的框，再往自己的框里写」，从不看周围。
实测这份客户图纸（新储物柜示意图）出现四种坏法：

  · 折行从框底往上叠，直接叠进上面那条标注的怀里
    （「Purple parts」折两行后压住「Inner frame 20×30×2mm」）
  · 译文比原文长，横着伸出框，压到右边那条标注
    （「3mm THK corner guard」压住「Yellow parts」）
  · 基线贴着框底，英文的下伸部（y/g/p）掉到框下的图线上
    （「Grey parts」的下半截压在黑条上）
  · 尺寸线、剖面线穿过原框，写上去就是字压线

这一层把页面变成一张可查询的二值图：哪里已经有东西、哪里是空的、
图框在哪。上层每写一条，先来问一句「这块能不能放」。

三条硬约束
----------
1. 不许压任何已有内容 —— 占用像素、已定稿的译文，都算
2. 不许越出图框 —— 客户要的是「不显示到文件框外」
3. 累计挪位有上限（默认 1.5 倍字高，由调用方传）—— 挪太远引线就指不上，
   而且制图规范里标注位置本身有含义，不是随便挪的

为什么不用 AI 看图定位置
------------------------
不是不能，是不该：同一页两次跑会给出不同位置（不可复现），
每次多花一次整页多模态请求（一份图纸多几毛钱），而且模型没有像素级
的坐标精度 —— 它说的是「右边一点」，我们需要的是一块矩形能不能放下
一行 5.5pt 的字。这件事栅格化 + 积分图几十毫秒就能算准，还不花钱。

精度
----
默认 150 dpi，一个像素约 0.48pt。判断「有没有东西」够用；
判断「差 0.1pt 会不会压线」不够用 —— 所以每个待写矩形额外留 EXTRA_CLEAR
的余量，宁可少放一条，也不写上去才发现压了线。
"""

import pymupdf

DEFAULT_DPI = 150

# 与背景色的 RGB 差值和超过这个值就算「有东西」。
# 30 是试出来的：抗锯齿边缘的浅灰在 10~20 之间，真正的线条和字都在 100 以上。
OCC_TOL = 30

# 待写矩形四周额外留的空隙（pt）。压到 0.3 而不是 0.8：
# 占用图是 150dpi，一个像素 0.48pt，留 0.8 会把「整条释放掉的原框」
# 重新判成占用 —— 原字的抗锯齿边缘正好落在框外那 0.8pt 里，
# 结果每条都觉得自己的位置不能用。0.3 不到一个像素，只用来吃掉舍入误差。
EXTRA_CLEAR = 0.3

# 判定「占用」的容差：整块里允许有零星几个像素被判为占用（抗锯齿、灰尘）。
# 一条 1pt 的线横穿 100pt 宽的框，在 150dpi 下占 3% 以上 —— 绝不会漏。
CLEAR_TOL = 0.004


def _numpy():
    """惰性取 numpy：没装也不该让整个程序起不来（只是空间感知失效）"""
    import numpy as np
    return np


def _detect_frame(page):
    """
    图框：页面上面积最大的那条描边矩形。

    用它而不是 page.rect，是因为这张图纸的图框右边留了 100pt 空白
    （第 1 页图框 100.7~741.2，页面宽 842）—— 那块空白在框外，
    写上去就是「显示到文件框外」，正是客户不要的。

    找不到就退回 page.rect：宁可少一层保护，也不能因为认不出图框
    就把整页判成禁区，一条都写不进去。
    """
    best = None
    for dr in page.get_drawings():
        if not dr.get("color"):
            continue            # 只有填充没有描边的，不是图框
        r = dr["rect"]
        if r.width < page.rect.width * 0.3 or r.height < page.rect.height * 0.3:
            continue            # 小格子不是图框
        if best is None or r.get_area() > best.get_area():
            best = r
    return pymupdf.Rect(best) if best else pymupdf.Rect(page.rect)


class PageSpace:
    """
    一页的占用图。构造一次，查整页。

    用法：
        space = PageSpace(page)              # 必须在任何写入之前构造
        space.release_many([c["rect"], ...]) # 这批原框马上要被涂掉，先释放
        space.is_clear(rect)                 # 这块能不能放
        space.reserve(rect)                  # 定稿，后来的别再压上来
    """

    def __init__(self, page, dpi=DEFAULT_DPI):
        self.page = page
        self.dpi = dpi
        self.scale = float(dpi) / 72.0
        self.frame = _detect_frame(page)
        self._reserved = []
        self._sat = None
        self._build()

    # ---------------- 建图 ----------------
    def _build(self):
        np = _numpy()
        pix = self.page.get_pixmap(dpi=self.dpi)
        arr = np.frombuffer(pix.samples, dtype=np.uint8)
        arr = arr.reshape(pix.height, pix.width, pix.n)[:, :, :3].astype(np.int16)
        self._w, self._h = pix.width, pix.height
        self._origin = (self.page.rect.x0, self.page.rect.y0)

        # 背景色取出现次数最多的那个色，不取左上角 —— 图纸左上角常常正好压着图框线
        small = arr[::7, ::7].reshape(-1, 3)
        quant = (small // 8).astype(np.int32)
        keys = quant[:, 0] * 1024 + quant[:, 1] * 32 + quant[:, 2]
        vals, counts = np.unique(keys, return_counts=True)
        k = int(vals[counts.argmax()])
        bg = np.array([(k // 1024) * 8 + 4, ((k // 32) % 32) * 8 + 4, (k % 32) * 8 + 4],
                      dtype=np.int16)
        self.bg = bg

        diff = np.abs(arr - bg).sum(axis=2)
        self._occ = diff > OCC_TOL
        self._dirty = True

    def _sat_build(self):
        """积分图：让「某个矩形里有多少占用像素」变成 O(1)"""
        np = _numpy()
        occ = self._occ.astype(np.int32)
        sat = np.zeros((self._h + 1, self._w + 1), dtype=np.int32)
        np.cumsum(np.cumsum(occ, axis=0), axis=1, out=sat[1:, 1:])
        self._sat = sat
        self._dirty = False

    # ---------------- 坐标换算 ----------------
    def _px(self, rect):
        """PDF 坐标 → 像素区间，已夹到图内"""
        x0 = int((rect.x0 - self._origin[0]) * self.scale)
        y0 = int((rect.y0 - self._origin[1]) * self.scale)
        x1 = int((rect.x1 - self._origin[0]) * self.scale + 0.999)
        y1 = int((rect.y1 - self._origin[1]) * self.scale + 0.999)
        return (max(0, x0), min(self._w, x1), max(0, y0), min(self._h, y1))

    # ---------------- 查询 ----------------
    def occupied_ratio(self, rect, ignore=None):
        """
        矩形内占用像素占比。

        ignore：这块马上要被我们自己涂成背景，算它的时候先扣掉。
        没有这个参数，每条标注都会觉得「自己的位置被自己占了」，
        一条也写不进去（实测就是这样，第一版全卡在这里）。
        """
        if self._dirty:
            self._sat_build()
        x0, x1, y0, y1 = self._px(rect)
        if x1 <= x0 or y1 <= y0:
            return 1.0
        sat = self._sat
        n = int(sat[y1, x1] - sat[y0, x1] - sat[y1, x0] + sat[y0, x0])
        area = (x1 - x0) * (y1 - y0)
        if ignore is not None and ignore.intersects(rect):
            inter = pymupdf.Rect(rect) & ignore
            ix0, ix1, iy0, iy1 = self._px(inter)
            if ix1 > ix0 and iy1 > iy0:
                n -= int(sat[iy1, ix1] - sat[iy0, ix1] - sat[iy1, ix0] + sat[iy0, ix0])
        # 被页面边界截掉的部分也算占用
        full = max(1, (rect.width * self.scale) * (rect.height * self.scale))
        if area < full * 0.85:
            n += int(full - area)
            area = int(full)
        return max(0, n) / float(area)

    def is_clear(self, rect, ignore=None, pad=EXTRA_CLEAR, safe=None):
        """
        这块能不能放下东西。

        先过图框（越界一律不行），再看占用。pad 是四周额外留的空隙。
        ignore 见 occupied_ratio：只该传「本条自己的原框」。
        传别人的框进来就有害了 —— 那条标注会以为这块没人要，
        把自己的译文挪进去，两条标注换位置，图上就乱了。

        safe：比图框再收一圈的「安全区」。文字贴着框线写，虽然没越界，
        视觉上就是快要出格（客户原话）。真放不下时调用方会传 None 把它放开，
        不能因为怕贴边就把整条留成原文。
        """
        if not self.frame.contains(rect):
            return False
        if safe is not None and not safe.is_empty and not safe.contains(rect):
            return False
        probe = pymupdf.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
        for rv in self._reserved:
            if probe.intersects(rv):
                return False
        return self.occupied_ratio(probe, ignore=ignore) <= CLEAR_TOL

    def inset(self, pad, pad_y=None):
        """
        图框往里收一圈，得到「安全区」。

        pad_y 单独给上下留白的量，默认与左右相同。文字离框线太近之所以难看，
        主要是左右两侧（译文比原文长，往两边长出去顶到框线）；上下方向
        图纸本来就是「标题紧贴图框上沿」的排法，按一个字高收进去会把
        所有视图标题判成越界 —— 实测第 1 页三个标题因此全部缩了一成字号。
        所以调用方可以只收左右，上下不动。

        收完是空的（图框本身就比两倍 pad 还小）就返回空矩形，
        调用方据此知道这条约束形同虚设，别再拿它当门槛。
        """
        f = self.frame
        pad = float(pad or 0.0)
        pad_y = pad if pad_y is None else float(pad_y)
        if f.width <= pad * 2 + 1 or f.height <= pad_y * 2 + 1:
            return pymupdf.Rect()
        return pymupdf.Rect(f.x0 + pad, f.y0 + pad_y, f.x1 - pad, f.y1 - pad_y)

    def occupied_span(self, window):
        """
        window 这个窗口里，有内容的那部分横向范围（PDF 坐标）。没内容返回 None。

        给「标题对着哪块图形居中」这个判断兜底用：图形是位图（不是矢量）时，
        get_drawings() 一条都拿不到，只能问占用图「这一带哪儿有东西」。
        取的是窗口内所有占用像素的外接范围，比逐个图元求并集粗，
        但判断「中心在哪」够用。
        """
        if self._dirty:
            self._sat_build()
        x0, x1, y0, y1 = self._px(window)
        if x1 <= x0 or y1 <= y0:
            return None
        cols = self._occ[y0:y1, x0:x1].any(axis=0)
        idx = cols.nonzero()[0]
        if len(idx) == 0:
            return None
        rows = self._occ[y0:y1, x0:x1].any(axis=1)
        ridx = rows.nonzero()[0]
        sx, sy = self.scale, self.scale
        return pymupdf.Rect(
            self._origin[0] + (x0 + float(idx[0])) / sx,
            self._origin[1] + (y0 + float(ridx[0])) / sy,
            self._origin[0] + (x0 + float(idx[-1]) + 1) / sx,
            self._origin[1] + (y0 + float(ridx[-1]) + 1) / sy)

    def clear_width(self, rect, limit, step=0.0, ignore=None):
        """
        从 rect.x0 往右，最多能延伸到多少宽度（含 rect 自身的可写部分）

        ignore：这块马上要被涂成背景（本条自己的原框），算占用时先扣掉。
        不传的话，原字自己的墨迹会把整条路堵死，探测结果永远是 0 ——
        严格模式（定制版）靠这个值决定「向右能长多长」，一传错就全废。
        """
        lo, hi = 0.0, max(0.0, float(limit))
        if hi <= 0:
            return 0.0
        if self.is_clear(pymupdf.Rect(rect.x0, rect.y0, rect.x0 + hi, rect.y1),
                         ignore=ignore):
            return hi
        for _ in range(12):     # 二分：宽度越小越容易过，单调成立
            mid = (lo + hi) / 2
            if self.is_clear(pymupdf.Rect(rect.x0, rect.y0, rect.x0 + mid, rect.y1),
                             ignore=ignore):
                lo = mid
            else:
                hi = mid
        return lo

    def clear_height_up(self, rect, limit):
        """从 rect.y1 往上，最多能延伸到多高"""
        lo, hi = 0.0, max(0.0, float(limit))
        if hi <= 0:
            return 0.0
        if self.is_clear(pymupdf.Rect(rect.x0, rect.y1 - hi, rect.x1, rect.y1)):
            return hi
        for _ in range(12):
            mid = (lo + hi) / 2
            if self.is_clear(pymupdf.Rect(rect.x0, rect.y1 - mid, rect.x1, rect.y1)):
                lo = mid
            else:
                hi = mid
        return lo

    # ---------------- 登记 ----------------
    def release_many(self, rects):
        """
        这些矩形即将被背景色涂掉，从占用图里释放掉。

        ⚠ 现在**不再全局使用**：一次性释放所有标注框，等于宣布
        「所有标注的地盘都是无主的」，第二条标注就会理直气壮地
        搬进第一条的位置，两条标注互换位置 —— 图上会乱。
        正确的做法见 is_clear(ignore=...)：每次只忽略自己那一块。
        这个接口留着是给批量场景（同一块区域多条重叠时一起放行）用的。
        """
        np = _numpy()
        for r in rects:
            x0, x1, y0, y1 = self._px(r)
            if x1 > x0 and y1 > y0:
                self._occ[y0:y1, x0:x1] = False
        self._dirty = True

    def reserve(self, rect):
        """定稿的译文位置。后来的候选不许压上来"""
        self._reserved.append(pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1))

    def reserve_gap(self, gap=EXTRA_CLEAR):
        """把已定稿位置四周留出空隙，避免两条译文只隔半个像素"""
        padded = []
        for r in self._reserved:
            padded.append(pymupdf.Rect(r.x0 - gap, r.y0 - gap, r.x1 + gap, r.y1 + gap))
        self._reserved = padded
