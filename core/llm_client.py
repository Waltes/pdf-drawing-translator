"""
AI 接口客户端（OpenAI 兼容协议）

为什么要单独一层：
  用户可能用 DeepSeek、通义、Kimi、智谱、硅基流动，甚至自建中转。
  它们的请求格式几乎一致（都是 /chat/completions），差别只在
  接口地址、模型名、有没有余额查询接口、以及每百万 token 的单价。
  把这些差异收进一张预设表里，上层翻译逻辑就完全不用关心用哪家。

三件事：
  1. chat()          发一次对话请求，返回文本 + 本次 token 用量
  2. query_balance() 查账户余额（只有部分服务商开放这个接口）
  3. UsageBook       记账：本次消耗、历史累计、按单价估算费用

关于费用：单价写死在 PRESETS 里，是各服务商官方公示价的近似值。
  它只用于「让你大概知道花了多少钱」，不参与任何决策。
  真实扣费以服务商账单为准；单价变了可以改 data/prices.json 覆盖。
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .paths import DATA_DIR

USAGE_PATH = os.path.join(DATA_DIR, "usage.json")
PRICES_PATH = os.path.join(DATA_DIR, "prices.json")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

# ---------------------------------------------------------------------------
# 服务商预设
#   base_url    : OpenAI 兼容根地址（不含 /chat/completions）
#   models      : 下拉候选，第一个是默认
#   balance_url : 余额查询接口，None 表示该服务商没开放
#   in_price / out_price : 每百万 token 的人民币价格（估算用）
# ---------------------------------------------------------------------------
PRESETS = {
    "deepseek": {
        "label": "DeepSeek（性价比最高，推荐）",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "balance_url": "https://api.deepseek.com/user/balance",
        "balance_kind": "deepseek",
        "prices": {"deepseek-chat": (2.0, 3.0), "deepseek-reasoner": (4.0, 16.0)},
    },
    "qwen": {
        "label": "阿里通义千问（DashScope 兼容模式）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max", "qwen3-max"],
        "balance_url": None,
        "balance_kind": None,
        "prices": {"qwen-plus": (0.8, 2.0), "qwen-turbo": (0.3, 0.6),
                   "qwen-max": (2.4, 9.6), "qwen3-max": (2.4, 9.6)},
    },
    "moonshot": {
        "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2-0905-preview", "moonshot-v1-8k", "moonshot-v1-32k"],
        "balance_url": "https://api.moonshot.cn/v1/users/me/balance",
        "balance_kind": "moonshot",
        "prices": {"kimi-k2-0905-preview": (4.0, 16.0), "moonshot-v1-8k": (12.0, 12.0),
                   "moonshot-v1-32k": (24.0, 24.0)},
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4.6", "glm-4-plus", "glm-4-flash"],
        "balance_url": None,
        "balance_kind": None,
        "prices": {"glm-4.6": (4.0, 16.0), "glm-4-plus": (5.0, 5.0), "glm-4-flash": (0.0, 0.0)},
    },
    "siliconflow": {
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["Qwen/Qwen3-32B", "Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3"],
        "balance_url": "https://api.siliconflow.cn/v1/user/info",
        "balance_kind": "siliconflow",
        "prices": {"Qwen/Qwen3-32B": (4.13, 4.13),
                   "Qwen/Qwen2.5-72B-Instruct": (4.13, 4.13),
                   "deepseek-ai/DeepSeek-V3": (2.0, 8.0)},
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"],
        "balance_url": None,
        "balance_kind": None,
        "prices": {"gpt-4o-mini": (1.1, 4.4), "gpt-4.1-mini": (2.9, 11.6), "gpt-4o": (18.0, 72.0)},
    },
    "custom": {
        "label": "自定义（任意 OpenAI 兼容接口）",
        "base_url": "",
        "models": [],
        "balance_url": None,
        "balance_kind": None,
        "prices": {},
    },
}

# 没配价格时用的兜底单价，仅用于让费用显示不至于为 0
FALLBACK_PRICE = (2.0, 6.0)

# ---------------------------------------------------------------------------
# 密钥申请引导
#
# 设置页要给的是「怎么拿到 key」，但只写 DeepSeek 一家没有意义 —— 用户可能
# 手里已经有别家的 key，或者干脆是公司内网的兼容接口。所以：
#   GENERIC_KEY_HINT : 对所有服务商都成立的一段话，永远显示
#   KEY_GUIDE        : 按当前选中的服务商给出申请入口和三步操作
# custom（自定义）没有申请入口，给的是「地址和模型名从哪儿找」
# ---------------------------------------------------------------------------
GENERIC_KEY_HINT = ("任何 OpenAI 兼容的服务都能接：地址和模型名照服务商文档抄，"
                    "密钥各平台互不通用，换服务商要换 key。")

KEY_GUIDE = {
    "deepseek": {
        "url": "https://platform.deepseek.com/api_keys",
        "btn": "打开 DeepSeek 密钥页",
        "steps": ("① 打开申请页面，手机号或邮箱注册登录；\n"
                  "② 左侧菜单进「API keys」，点「创建 API key」；\n"
                  "③ 复制那串 sk- 开头的字符（只显示一次），粘到上面的输入框。\n"
                  "新账号一般有赠送额度，够翻几十份图纸。"),
    },
    "qwen": {
        "url": "https://bailian.console.aliyun.com/",
        "btn": "打开百炼控制台",
        "steps": ("① 用阿里云账号登录百炼控制台，按提示开通模型服务；\n"
                  "② 右上角头像 →「API-KEY」→ 创建我的 API-KEY；\n"
                  "③ 复制后粘到上面。接口地址保持 compatible-mode/v1 不要改。"),
    },
    "moonshot": {
        "url": "https://platform.moonshot.cn/console/api-keys",
        "btn": "打开 Kimi 开放平台",
        "steps": ("① 打开 Kimi 开放平台注册登录（用手机号即可）；\n"
                  "② 左侧「API Key 管理」→ 新建，起个名字；\n"
                  "③ 复制生成的密钥粘到上面。"),
    },
    "zhipu": {
        "url": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
        "btn": "打开智谱密钥页",
        "steps": ("① 打开智谱开放平台注册并实名；\n"
                  "② 「API Keys」→ 新建；\n"
                  "③ 密钥是「id.secret」两段拼在一起的，整段复制别只复制半截。"),
    },
    "siliconflow": {
        "url": "https://cloud.siliconflow.cn/account/ak",
        "btn": "打开硅基流动控制台",
        "steps": ("① 登录硅基流动控制台（新用户有赠送额度）；\n"
                  "② 左侧「账户管理」→「API 密钥」→ 新建；\n"
                  "③ 复制后粘到上面。模型名要带斜杠前缀，例如 Qwen/Qwen3-32B。"),
    },
    "openai": {
        "url": "https://platform.openai.com/api-keys",
        "btn": "打开 OpenAI 密钥页",
        "steps": ("① 需要能直连 openai.com 的网络环境，国内多数网络访问不了；\n"
                  "② 「API keys」→ Create new secret key；\n"
                  "③ 复制后粘到上面。用不了的话换 DeepSeek 或硅基流动，效果接近。"),
    },
    "custom": {
        "url": None,
        "btn": "",
        "steps": ("只要对方提供 OpenAI 兼容接口就能接，参数这样填：\n"
                  "① 接口地址写到版本号为止，一般形如 https://你的域名/v1；\n"
                  "② 模型名照对方文档里写的抄，别自己编；\n"
                  "③ 本机跑 ollama / vLLM 也走这里，地址填 http://127.0.0.1:11434/v1。"),
    },
}


def key_guide(provider_id):
    """取某个服务商的申请引导；没登记的当自定义处理，不会返回 None"""
    return KEY_GUIDE.get(provider_id or "", KEY_GUIDE["custom"])


def _load_price_overrides():
    """允许用户用 data/prices.json 覆盖单价，格式 {"模型名": [输入价, 输出价]}"""
    if not os.path.exists(PRICES_PATH):
        return {}
    try:
        with open(PRICES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: (float(v[0]), float(v[1])) for k, v in data.items() if isinstance(v, list)}
    except Exception:
        return {}


def preset_for(provider_id):
    return PRESETS.get(provider_id or "", PRESETS["custom"])


def price_of(provider_id, model):
    """查某个模型的单价，返回 (输入价, 输出价)，单位：元/百万 token"""
    over = _load_price_overrides()
    if model in over:
        return over[model]
    p = preset_for(provider_id).get("prices", {})
    if model in p:
        return p[model]
    # 同系列模型名带后缀时（如 deepseek-chat-0324）退到前缀匹配
    for k, v in p.items():
        if model.startswith(k):
            return v
    return FALLBACK_PRICE


def _norm_base(base_url):
    """把用户填的地址整理成可以直接拼 /chat/completions 的形式"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")]
    return url


# 本机地址必须绕过系统代理，否则本地/内网部署的接口会被代理拦掉
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(req, timeout):
    """统一的请求入口：本机地址直连，其余走系统代理设置"""
    host = ""
    try:
        host = (urllib.parse.urlparse(req.full_url).hostname or "").lower()
    except Exception:
        pass
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return _LOOPBACK_OPENER.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _post_json(url, payload, api_key, timeout=90):
    """发一个 JSON POST，返回 (解析后的字典, 原始文本)。异常向上抛"""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
        "User-Agent": UA,
    })
    with _open(req, timeout) as r:
        raw = r.read().decode("utf-8", "ignore")
    return json.loads(raw), raw


def _get_json(url, api_key, timeout=20):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + api_key,
        "User-Agent": UA,
    })
    with _open(req, timeout) as r:
        raw = r.read().decode("utf-8", "ignore")
    return json.loads(raw), raw


def friendly_error(exc):
    """
    把底层异常翻译成用户能看懂的话。
    窗口里直接显示这些文字，所以不能出现 traceback 或英文堆栈。
    """
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        try:
            detail = exc.read().decode("utf-8", "ignore")[:300]
        except Exception:
            detail = ""
        if code == 401:
            return "密钥无效或已过期（401）。请检查 API Key 是否复制完整。"
        if code == 402:
            return "账户余额不足（402）。请先到服务商后台充值。"
        if code == 403:
            return "没有权限访问该模型（403）。可能是密钥权限或模型未开通。"
        if code == 404:
            return "接口地址或模型名不存在（404）。请核对「接口地址」和「模型」。"
        if code == 429:
            return "请求过于频繁或额度用尽（429）。请稍后再试。"
        if code >= 500:
            return f"服务商暂时不可用（{code}），请稍后重试。"
        return f"接口返回错误 {code}：{detail}"
    if isinstance(exc, urllib.error.URLError):
        reason = str(getattr(exc, "reason", exc))
        return f"网络连接失败：{reason}。请检查网络、代理或接口地址是否正确。"
    if isinstance(exc, TimeoutError):
        return "请求超时。可以换一个更快的服务商，或减少单次翻译条数。"
    return f"请求失败：{type(exc).__name__} {exc}"


class UsageBook:
    """
    记账本：记录本次会话和历史累计的 token 消耗与估算费用。

    为什么要落盘：用户会关心「这个工具一共花了我多少钱」，
    只显示单次不够，必须能跨次累计。
    """

    def __init__(self):
        self.session = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                        "cached_tokens": 0, "cost": 0.0, "items": 0, "failed_calls": 0}
        self.history = self._load()

    def _load(self):
        default = {"cumulative": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                 "cost": 0.0}, "runs": []}
        if os.path.exists(USAGE_PATH):
            try:
                with open(USAGE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                default["cumulative"].update(data.get("cumulative", {}))
                default["runs"] = data.get("runs", [])[-200:]
            except Exception:
                pass
        return default

    def record(self, usage, cost):
        """记一次请求。usage 取自接口返回的 usage 字段（可能缺失）"""
        usage = usage or {}
        p = int(usage.get("prompt_tokens") or 0)
        c = int(usage.get("completion_tokens") or 0)
        hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        self.session["calls"] += 1
        self.session["prompt_tokens"] += p
        self.session["completion_tokens"] += c
        self.session["cached_tokens"] += hit
        self.session["cost"] += cost
        self.history["cumulative"]["calls"] += 1
        self.history["cumulative"]["prompt_tokens"] += p
        self.history["cumulative"]["completion_tokens"] += c
        self.history["cumulative"]["cost"] += cost

    def note_items(self, n):
        """记录本次成功翻译了多少条文字（用于展示「每千条多少钱」这类直观指标）"""
        self.session["items"] += n

    def note_failure(self):
        self.session["failed_calls"] += 1

    def summary(self):
        """给界面用的一句话摘要"""
        s = self.session
        total = s["prompt_tokens"] + s["completion_tokens"]
        return (f"本次 {s['calls']} 次请求 · 输入 {s['prompt_tokens']:,} / "
                f"输出 {s['completion_tokens']:,} tokens（合计 {total:,}）· "
                f"估算 ¥{s['cost']:.4f}")

    def cumulative_text(self):
        c = self.history["cumulative"]
        return (f"历史累计 {c['calls']} 次请求 · "
                f"{c['prompt_tokens'] + c['completion_tokens']:,} tokens · "
                f"估算 ¥{c['cost']:.4f}")

    def add_run(self, file_name, items):
        """一次文件处理完成，归档到历史"""
        s = self.session
        self.history["runs"].append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "file": os.path.basename(file_name or ""),
            "items": items,
            "prompt_tokens": s["prompt_tokens"],
            "completion_tokens": s["completion_tokens"],
            "cost": round(s["cost"], 6),
        })
        self.history["runs"] = self.history["runs"][-200:]
        self.save()

    def reset_session(self):
        for k in self.session:
            self.session[k] = 0 if isinstance(self.session[k], int) else 0.0

    def save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(USAGE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


class LLMClient:
    """一次配置对应一个客户端。配置改了要重新构造"""

    def __init__(self, cfg):
        """
        cfg: {"provider": "deepseek", "base_url": "...", "model": "...",
              "api_key": "...", "temperature": 0.1, "batch_size": 30,
              "timeout": 90, "enabled": True}
        """
        self.cfg = dict(cfg or {})
        self.provider = self.cfg.get("provider") or "deepseek"
        self.preset = preset_for(self.provider)
        self.base_url = _norm_base(self.cfg.get("base_url") or self.preset["base_url"])
        self.model = (self.cfg.get("model") or "").strip()
        self.api_key = (self.cfg.get("api_key") or "").strip()
        self.temperature = float(self.cfg.get("temperature", 0.1))
        self.batch_size = int(self.cfg.get("batch_size", 30) or 30)
        self.timeout = int(self.cfg.get("timeout", 90) or 90)
        self.usage = UsageBook()
        self.last_error = ""

    # ---------------- 配置检查 ----------------
    def available(self):
        """
        能不能用：地址 + 密钥 + 模型齐了就算能用。

        enabled 只作历史遗留的开关，缺省按 True 处理。原来看的是
        `self.cfg.get("enabled")`，而老配置里根本没有这个键（默认 False 兜底），
        结果用户明明填好了 key，界面还写「AI 接口：未配置」。
        """
        if not (self.base_url and self.api_key and self.model):
            return False
        return bool(self.cfg.get("enabled", True))

    def missing_reason(self):
        miss = []
        if not self.base_url:
            miss.append("接口地址")
        if not self.api_key:
            miss.append("API Key")
        if not self.model:
            miss.append("模型名")
        return "、".join(miss)

    # ---------------- 核心：一次对话 ----------------
    def chat(self, messages, max_tokens=None, json_mode=False, timeout=None):
        """
        返回 (文本, usage 字典)。失败抛异常，由上层用 friendly_error 转成中文。
        """
        if not self.available():
            raise RuntimeError("AI 接口未配置完整：缺少" + (self.missing_reason() or "参数"))

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if json_mode:
            # 部分服务商不支持 json_object，失败时上层会自动重试普通模式
            payload["response_format"] = {"type": "json_object"}

        url = self.base_url + "/chat/completions"
        t0 = time.time()
        data, _raw = _post_json(url, payload, self.api_key, timeout or self.timeout)
        self.last_latency = time.time() - t0

        content = ""
        try:
            content = (data["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            content = ""

        usage = data.get("usage") or {}
        p = int(usage.get("prompt_tokens") or 0)
        c = int(usage.get("completion_tokens") or 0)
        hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        in_price, out_price = price_of(self.provider, self.model)
        # 缓存命中的输入 token 通常按更低价格计费，这里按 DeepSeek 的 1 折估算
        cost = (max(p - hit, 0) * in_price + hit * in_price * 0.1 + c * out_price) / 1_000_000
        self.usage.record(usage, cost)
        return content, usage

    # ---------------- 测试连接 ----------------
    def test(self):
        """
        返回 {"ok": bool, "msg": str, "latency": 秒, "models": [...]}

        先试 /models（不花 token）。拿到列表还不够 —— 一是有些服务商根本不提供
        这个接口，二是模型名可能没进列表却照样能用（DeepSeek 的老别名就是这样）。
        所以「模型不在列表里」时再补一次极短的对话坐实，别给用户一个假警报。

        反过来也一样要防：推理型模型（v4-pro 这类）会把 token 全花在思考上，
        返回内容为空。这种「接口通但没输出」必须报失败，否则翻译出来整页空白。
        """
        if not (self.base_url and self.api_key):
            return {"ok": False, "msg": "请先填写接口地址和 API Key",
                    "latency": 0, "models": []}

        models = []
        t0 = time.time()
        try:
            data, _ = _get_json(self.base_url + "/models", self.api_key, timeout=15)
            models = [m.get("id", "") for m in (data.get("data") or []) if m.get("id")]
            if models and self.model and self.model in models:
                return {"ok": True, "models": models,
                        "latency": time.time() - t0,
                        "msg": f"连接正常，该接口共 {len(models)} 个模型可用。"}
        except urllib.error.HTTPError as e:
            if e.code in (401, 402, 403):
                return {"ok": False, "msg": friendly_error(e), "latency": 0, "models": []}
            # 其他情况（比如该服务商不提供 /models）继续走对话验证
        except Exception:
            pass

        # 用一次极短的对话验证模型是否真的可用
        if not self.model:
            return {"ok": False, "msg": "接口能连通，但没有填写模型名",
                    "latency": 0, "models": models}
        try:
            t0 = time.time()
            content, _u = self.chat([{"role": "user", "content": "ping"}],
                                    max_tokens=16, timeout=25)
            latency = time.time() - t0
        except Exception as e:
            return {"ok": False, "msg": friendly_error(e), "latency": 0, "models": models}

        if not content:
            return {"ok": False, "models": models, "latency": latency,
                    "msg": ("接口能通，但模型没有返回任何内容。多半是推理型模型"
                            "（如 deepseek-v4-pro / o 系列）把 token 都用在思考上了，"
                            "换个 chat 系列的模型再试。")}
        extra = ""
        if models and self.model not in models:
            extra = (f"\n服务商的模型列表里没有 {self.model}，但实测能正常调用，"
                     "可以继续用。")
        return {"ok": True, "models": models, "latency": latency,
                "msg": "连接正常，已用小请求确认模型能正常返回内容。" + extra}

    # ---------------- 余额查询 ----------------
    def query_balance(self):
        """
        返回 {"ok": bool, "text": 展示文本, "amount": 数值或 None, "currency": "CNY"}
        只有服务商开放了余额接口才能查到，其余情况如实说明，不假装。
        """
        kind = self.preset.get("balance_kind")
        url = self.preset.get("balance_url")
        if not kind or not url:
            return {"ok": False, "amount": None, "currency": "",
                    "text": f"{self.preset['label']} 没有开放余额查询接口，只能按消耗估算。"}
        if not self.api_key:
            return {"ok": False, "amount": None, "currency": "", "text": "请先填写 API Key"}

        try:
            data, raw = _get_json(url, self.api_key, timeout=20)
        except Exception as e:
            return {"ok": False, "amount": None, "currency": "", "text": friendly_error(e)}

        try:
            if kind == "deepseek":
                infos = data.get("balance_infos") or []
                if not infos:
                    return {"ok": False, "amount": None, "currency": "", "text": "接口未返回余额"}
                info = infos[0]
                amount = float(info.get("total_balance", 0))
                cur = info.get("currency", "CNY")
                sym = "¥" if cur.upper() in ("CNY", "RMB") else "$"
                return {"ok": True, "amount": amount, "currency": cur,
                        "text": f"账户余额 {sym}{amount:.2f}"
                                f"（赠送 ¥{float(info.get('granted_balance', 0)):.2f} · "
                                f"充值 ¥{float(info.get('topped_up_balance', 0)):.2f}）"}
            if kind == "moonshot":
                d = data.get("data") or {}
                amount = float(d.get("available_balance", 0))
                return {"ok": True, "amount": amount, "currency": "CNY",
                        "text": f"账户余额 ¥{amount:.2f}"}
            if kind == "siliconflow":
                d = data.get("data") or {}
                amount = float(d.get("totalBalance", d.get("balance", 0)) or 0)
                return {"ok": True, "amount": amount, "currency": "CNY",
                        "text": f"账户余额 ¥{amount:.2f}"}
            return {"ok": False, "amount": None, "currency": "", "text": "未知的余额接口格式"}
        except Exception:
            # 接口格式变了也不要崩，把原始返回截一段给用户看
            return {"ok": False, "amount": None, "currency": "",
                    "text": "余额接口返回了无法解析的内容：" + (raw or "")[:160]}
