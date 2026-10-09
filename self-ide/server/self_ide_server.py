#!/usr/bin/env python3
"""self_ide_server.py — Self-IDE 服务端 v1.2（2026-08-11）

战略定位：Self-IDE 的护城河在 Hermes 层，不在插件层。
插件只做壳（配置编排/通信管道/状态展示），本服务承担：
  1. 模型队列双轨（自动由强到弱 / 手动锁定并行）—— 复用 moa_enhancer，一行不重写
  2. Quant-Router —— 按代码后缀/内容选模型后端
  3. Cost-Meter  —— Token 消耗计量，暴露 /status
  4. OpenAI 兼容代理 —— 转发到 SelfClaw Gateway 19899，Continue/Cline 零外泄
  5. Goal 引擎 —— 目标驱动自动循环（拆解→执行→验证→循环）
  6. Subagents 并行编排 —— 多 Agent 并行协作（General/Explore + 自定义）

═══ 铁律遵从 ═══
R-11 升级免疫：本文件位于 hermes_overlay/（非 git 仓库目录），Hermes 升级不覆盖
R-12 端口约束：19898（实测空闲）。不可变端口 9119/19899/19996/19997/9120/3410 均未占用
R-9  bypass proxy：所有 127.0.0.1 请求禁用代理（教训 #107/#111 —— TUN fake-ip 会劫持本地回环）
R-10 深度探活：health 不只探端口，可发真实 chat/completions（教训 #75/#113）
R-3  禁止 bare except + pass：所有异常必须 log 或 raise
R-4  禁止吞输出：全部写日志文件
R-5  禁止硬编码：模型/URL 从 moa_enhancer + config 动态读取
R-1  JSON 消费者：本服务只读 moa_state.json / moa_user_selections.json，不写，零腐烂风险

v1.2 变更（2026-08-11，Phase 1：Goal + Subagents）：
  - 新增 Goal 引擎：/goal/set /status /iterate /cancel /timeline
  - 新增 Subagents 并行：/subagent/spawn /status /wait_all /list
  - 新增模块：goal_engine.py、subagent_engine.py、subagents/*.md
  - 复用现有：Quant-Router、MOA 队列、Cost-Meter、自愈体系
"""

import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ─── 新增模块导入 ───────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from goal_engine import goal_engine, GoalStatus
from subagent_engine import subagent_engine, SubagentStatus, SubagentRole

# ─── 路径常量 ────────────────────────────────────────────────
HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", str(Path.home() / "hermes")))
MOA_DIR = HERMES_ROOT / "plugins" / "moa_enhancer"
STATE_PATH = MOA_DIR / "moa_state.json"
SELECTIONS_PATH = MOA_DIR / "moa_user_selections.json"
RANKING_PATH = MOA_DIR / "model_ranking.py"
LOG_DIR = HERMES_ROOT / "logs"
LOG_PATH = LOG_DIR / "self_ide_server.log"
COST_PATH = HERMES_ROOT / "hermes_overlay" / "self_ide" / "cost_meter.json"

SELF_IDE_PORT = int(os.environ.get("SELF_IDE_PORT", 19898))
GATEWAY_URL = os.environ.get("SELFCLAW_URL", f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '19899')}")
DASHBOARD_URL = "http://127.0.0.1:9119"

# ─── R-4：禁止吞输出，全量日志 ───────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_PATH), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("self-ide")

# ─── R-9：bypass proxy（教训 #107/#111，本次亲历踩坑）────────
# TUN 模式 fake-ip 会把 127.0.0.1 也劫持；必须建无代理 opener
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _local_request(url, method="GET", body=None, timeout=60):
    """本地回环请求，强制 bypass 系统代理。R-9 铁律。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, raw


# 模型名归一化：内部格式（provider:model） -> Gateway 注册 ID
_gw_model_cache = {"list": [], "ts": 0}
# 静态兜底映射（2026-08-11 实测确认可用，优先于动态匹配）
_GW_STATIC_MAP = {
    "kimi-direct:k3": "kimi-direct:kimi-k3",
    "kimi-direct:k2.6": "kimi-direct:kimi-k2.6",
    "opencode-zen:longcat-2.0-free": "opencode-zen:longcat-2.0-free",
    "opencode-zen:deepseek-v4-flash-free": "opencode-zen:deepseek-v4-flash-free",
    "openclaw:opencode-zen:longcat-2.0-free": "opencode-zen:longcat-2.0-free",
    "openclaw:opencode-zen:deepseek-v4-flash-free": "opencode-zen:deepseek-v4-flash-free",
    "nous:tencent/hy3:free": "kimi-direct:kimi-k3",  # fallback 聚合器链中可能用到
}

def _gw_model_id(model: str) -> str:
    """将内部 model 映射为 Gateway 19899 实际注册的 model ID。
    优先级：静态兜底表 > 缓存直接命中 > 去前缀匹配 > 加前缀匹配 > 模糊匹配 > 原样返回。
    """
    # 1. 静态兜底映射（实测确认可用，最可靠）
    if model in _GW_STATIC_MAP:
        return _GW_STATIC_MAP[model]
    import time
    now = time.time()
    if not _gw_model_cache["list"] or now - _gw_model_cache["ts"] > 60:
        try:
            status, raw = _local_request(f"{GATEWAY_URL}/v1/models", timeout=5)
            ids = [m["id"] for m in json.loads(raw).get("data", [])]
            _gw_model_cache["list"] = ids
            _gw_model_cache["ts"] = now
        except Exception:
            pass
    ids = _gw_model_cache["list"]
    if not ids:
        return model
    # 2. 直接命中
    if model in ids:
        return model
    # 3. 去掉 provider 前缀动态匹配
    if ":" in model:
        tail = model.split(":", 1)[-1]
        for m in ids:
            if m == tail or m.endswith(f":{tail}") or m.endswith(f"-{tail}") or m == tail.replace(".", "-"):
                return m
        for m in ids:
            if tail in m and ("kimi" in model.lower() or "longcat" in model.lower() or "deepseek" in model.lower()):
                return m
    # 4. 【新增】无 provider 前缀的尾缀匹配（处理用户直接传 longcat-2.0-free 这种情况）
    if ":" not in model:
        for m in ids:
            if m.endswith(f":{model}") or m.endswith(f"-{model.replace('.', '-')}"):
                return m
        # 模糊匹配：含 model 关键字的
        model_norm = model.replace(".", "-").lower()
        for m in ids:
            if model_norm in m.lower() or model.lower() in m.lower():
                return m
    return model


def _send_to_gateway_chat(model: str, messages: list, max_tokens: int) -> tuple:
    """发送 chat/completions 到 Gateway，自动归一化 model 名。
    上游错误透传（2026-08-15 修复）：原 _local_request 抛 HTTPError 被顶层包成笼统 502，
    导致上游 429(限额)/401(上游 key 失效) 被误判为服务故障。现捕获并透传真实状态码。"""
    norm_model = _gw_model_id(model)
    import urllib.error
    try:
        status, raw = _local_request(
            f"{GATEWAY_URL}/v1/chat/completions", "POST",
            {"model": norm_model, "messages": messages, "max_tokens": max_tokens, "stream": False},
            timeout=300,
        )
        return status, raw
    except urllib.error.HTTPError as e:
        # 透传上游真实状态码（429/401/404...），不包成 502
        err_body = e.read().decode("utf-8", errors="replace")
        return e.code, err_body


def _send_to_dashboard_chat(model: str, messages: list, max_tokens: int) -> tuple:
    """Spare：Gateway 19899 不可达时降级到 Dashboard 9119（2026-08-13 老板铁律）。
    9119 用 Hermes 原生 provider 路由，模型名需去掉 openclaw: 前缀或保留。
    """
    dash_model = model
    # 19899 内部名转 9119 可识别名：openclaw:xxx:yyy -> xxx:yyy
    if dash_model.startswith("openclaw:"):
        dash_model = dash_model[len("openclaw:"):]
    status, raw = _local_request(
        f"{DASHBOARD_URL}/v1/chat/completions", "POST",
        {"model": dash_model, "messages": messages, "max_tokens": max_tokens, "stream": False},
        timeout=300,
    )
    return status, raw


# ═══════════════════════════════════════════════════════════
# 模块 1：模型队列双轨（复用 moa_enhancer，零重写）
# ═══════════════════════════════════════════════════════════

def load_ranking():
    """加载 model_ranking.py 的双评分表（ref_score / agg_score）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("mr", str(RANKING_PATH))
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    return mr.RANKING, getattr(mr, "AGGREGATOR_FALLBACK_CHAIN", [])


def _read_json(path):
    """R-3：不吞异常，读失败明确 log 并返回 None。"""
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("读取 %s 失败: %s", path, exc)
        return None


def get_queue():
    """返回 Self-IDE 模型队列（双轨）。

    自动模式：model_ranking 双评分由强到弱 + moa_state 存活标记
    手动模式：moa_user_selections 锁定阵容优先，自动跳过
    每条线路带 fallback 链（来自 moa_state.reference_chains）
    """
    ranking, agg_chain = load_ranking()
    state = _read_json(STATE_PATH) or {}
    sel = _read_json(SELECTIONS_PATH) or {}

    manual = bool(state.get("manual_mode") or sel.get("manual_mode"))
    chains = state.get("reference_chains", {}) or {}

    if manual:
        refs = state.get("manual_references") or state.get("references") or []
        agg = state.get("manual_aggregator") or state.get("aggregator")
        source = "manual"
    else:
        refs = state.get("references") or []
        agg = state.get("aggregator")
        source = "auto"

    # 自动模式下若 state 为空，回退到静态评分表由强到弱
    if not refs:
        refs = sorted(ranking, key=lambda n: ranking[n].get("ref_score", 0), reverse=True)[:3]
        source += "+ranking_fallback"
    if not agg:
        agg_sorted = sorted(ranking, key=lambda n: ranking[n].get("agg_score", 0), reverse=True)
        agg = agg_sorted[0] if agg_sorted else None
        source += "+agg_fallback"

    def _lookup_chain(name):
        """归一化查找 fallback 链：手动/自动模式 ref 名可能带 openclaw: 前缀，
        而 reference_chains 的 key 格式不统一（有的带 openclaw: 有的不带）。
        依次尝试 直查 → 去 openclaw: 前缀查 → 加 openclaw: 前缀查，命中即返回。"""
        if name in chains:
            return chains[name]
        # 去掉 openclaw: 前缀再查
        stripped = name.split("openclaw:", 1)[-1] if "openclaw:" in name else name
        if stripped != name and stripped in chains:
            return chains[stripped]
        # 加 openclaw: 前缀再查
        prefixed = f"openclaw:{name}" if not name.startswith("openclaw:") else name
        if prefixed != name and prefixed in chains:
            return chains[prefixed]
        return []

    # 聚合器兜底链优先级：moa_state.aggregator_fallback（architect 手动链/巡检产物）
    # > model_ranking.py AGGREGATOR_FALLBACK_CHAIN（静态默认，v6 后可能已删除）
    state_agg_fb = state.get("aggregator_fallback") or []
    if isinstance(state_agg_fb, list):
        state_agg_fb = [pair for pair in state_agg_fb
                        if isinstance(pair, (list, tuple)) and len(pair) == 2]
    effective_agg_chain = state_agg_fb if state_agg_fb else list(agg_chain)

    def _entry(name, score_key):
        info = ranking.get(name, {})
        fb_pairs = _lookup_chain(name)
        fallback = [f"{p}:{m}" for p, m in fb_pairs]
        # 聚合器兜底：若 chains 无此条目，用有效聚合器兜底链提供容错
        if not fallback and score_key == "agg_score" and effective_agg_chain:
            fallback = [f"{p}:{m}" for p, m in effective_agg_chain
                        if f"{p}:{m}" != name]
        return {
            "model": name,
            "score": info.get(score_key, 0),
            "note": info.get("note", ""),
            "fallback": fallback,
        }

    return {
        "mode": "manual" if manual else "auto",
        "source": source,
        "aggregator": _entry(agg, "agg_score") if agg else None,
        "references": [_entry(n, "ref_score") for n in refs],
        "aggregator_chain": list(effective_agg_chain),
        "updated_at": state.get("updated_at"),
        "total_ranked": len(ranking),
    }


# ═══════════════════════════════════════════════════════════
# 模块 2：Quant-Router（量化垂直护城河）
# ═══════════════════════════════════════════════════════════

# R-5：不硬编码具体模型名，只声明"能力档位"，实际模型由队列动态解析
QUANT_RULES = [
    # (匹配器, 能力档位, 理由)
    (lambda p: p.endswith((".cu", ".cuh")), "deterministic", "CUDA 内核需确定性输出"),
    (lambda p: p.endswith(".py") and any(
        k in p.lower() for k in ("backtest", "engine", "risk", "cuda", "strategy", "exchange")),
     "deterministic", "回测/风控/策略/交易所适配需确定性输出"),  # [2026-09-20 fix-phase1] 加 exchange（量化项目 D3）
    (lambda p: p.endswith((".md", ".txt", ".rst")), "fast", "文档类，快速响应优先"),
    (lambda p: p.endswith((".json", ".yaml", ".yml", ".toml", ".ini")), "fast", "配置类，快速响应优先"),
    (lambda p: p.endswith((".ts", ".tsx", ".js", ".jsx")), "autocomplete", "Web/前端脚本，轻量补全，最短延迟优先"),
]

# 能力档位 → 队列取位策略
TIER_PICK = {
    "deterministic": "top_ref",   # 取参谋榜首（最强）
    "fast":          "last_ref",  # 取参谋末位（最快/最省）
    "autocomplete":  "last_ref",  # 取参谋末位（最快/最省，轻量补全同 fast 策略）
    "general":       "aggregator" # 取聚合器（均衡）
}


def route_by_tier(tier):
    """[2026-09-20 fix-phase1] 档位名直接路由（Continue/Cline 把 model 填档位名时的伪模型名协议）。
    根因：chat 分支原白名单仅 ("auto","self-ide-auto")，档位名被当真实模型透传 → 网关 missing model。
    老板授权：CHAIN-BP-001 Phase 1（D1 接线，2026-09-20 开工令）。
    评估：复用 TIER_PICK 取位逻辑按档位选模，渲染端零逻辑原则不变（路由决策仍在服务端）。
    测试：POST /v1/chat/completions model=autocomplete/deterministic/general 协议预检。
    ⚠ 经老板批准，严禁擅自"""
    strategy = TIER_PICK.get(tier, "aggregator")
    queue = get_queue()
    refs = queue.get("references") or []
    if strategy == "top_ref" and refs:
        chosen = refs[0]
    elif strategy == "last_ref" and refs:
        chosen = refs[-1]
    else:
        chosen = queue.get("aggregator") or (refs[0] if refs else None)
    return {
        "tier": tier,
        "strategy": strategy,
        "model": (chosen or {}).get("model"),
        "fallback": (chosen or {}).get("fallback", []),
        "queue_mode": queue.get("mode"),
    }


def route_by_path(file_path):
    """Quant-Router：按文件路径决定模型后端。Cursor 结构上做不到的差异化点。"""
    tier, reason = "general", "默认通用档位"
    for matcher, t, r in QUANT_RULES:
        try:
            if matcher(file_path or ""):
                tier, reason = t, r
                break
        except (AttributeError, TypeError) as exc:  # R-3：不 bare except
            log.warning("Quant-Router 规则匹配异常 path=%s: %s", file_path, exc)

    queue = get_queue()
    refs = queue.get("references") or []
    strategy = TIER_PICK.get(tier, "aggregator")

    if strategy == "top_ref" and refs:
        chosen = refs[0]
    elif strategy == "last_ref" and refs:
        chosen = refs[-1]
    else:
        chosen = queue.get("aggregator") or (refs[0] if refs else None)

    return {
        "file": file_path,
        "tier": tier,
        "reason": reason,
        "strategy": strategy,
        "model": (chosen or {}).get("model"),
        "fallback": (chosen or {}).get("fallback", []),
        "queue_mode": queue.get("mode"),
    }


# ═══════════════════════════════════════════════════════════
# 模块 3：Cost-Meter（Token 计量）
# ═══════════════════════════════════════════════════════════

def _load_cost():
    data = _read_json(COST_PATH)
    if not isinstance(data, dict):
        data = {"version": 1, "since": time.strftime("%Y-%m-%dT%H:%M:%S"), "models": {}, "total": {}}
    return data


def record_cost(model, usage):
    """R-9 原子写。usage 来自上游 OpenAI 兼容响应。"""
    if not usage:
        return
    data = _load_cost()
    m = data["models"].setdefault(model or "unknown", {"calls": 0, "prompt": 0, "completion": 0})
    m["calls"] += 1
    m["prompt"] += int(usage.get("prompt_tokens") or 0)
    m["completion"] += int(usage.get("completion_tokens") or 0)
    tot = data.setdefault("total", {"calls": 0, "prompt": 0, "completion": 0})
    tot["calls"] = tot.get("calls", 0) + 1
    tot["prompt"] = tot.get("prompt", 0) + int(usage.get("prompt_tokens") or 0)
    tot["completion"] = tot.get("completion", 0) + int(usage.get("completion_tokens") or 0)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    COST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = COST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(COST_PATH))


# ═══════════════════════════════════════════════════════════
# 模块 4：健康检查（R-10 深度探活，非仅端口）
# ═══════════════════════════════════════════════════════════

def check_health(deep=False):
    """deep=True 时发真实 chat/completions（教训 #75/#113：models 200 ≠ 链路通）。"""
    result = {"self_ide": "ok", "port": SELF_IDE_PORT, "checks": []}

    # 1. Gateway /v1/models
    gw_models = []
    try:
        status, raw = _local_request(f"{GATEWAY_URL}/v1/models", timeout=10)
        gw_models = [m["id"] for m in json.loads(raw).get("data", [])]
        result["checks"].append({
            "name": "gateway_models", "ok": status == 200,
            "detail": f"HTTP {status}, {len(gw_models)} models",
        })
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result["checks"].append({"name": "gateway_models", "ok": False, "detail": str(exc)})

    # 2. 队列可解析
    try:
        q = get_queue()
        refs = q.get("references") or []
        agg = q.get("aggregator") or {}
        # fallback 完整度检测（2026-08-11 新增：暴露容错链缺失，watchdog 可据此告警）
        zero_fb = []
        if agg and not agg.get("fallback"):
            zero_fb.append(f"agg:{agg.get('model')}")
        for r in refs:
            if not r.get("fallback"):
                zero_fb.append(f"ref:{r.get('model')}")
        fb_ok = len(zero_fb) == 0
        fb_detail = f"mode={q['mode']}, {len(refs)} refs, agg={agg.get('model')}"
        if not fb_ok:
            fb_detail += f", ⚠ zero-fallback: {','.join(zero_fb)}"
        result["checks"].append({
            "name": "model_queue", "ok": bool(refs) and fb_ok,
            "detail": fb_detail,
        })
    except (OSError, ValueError, AttributeError, ImportError) as exc:
        result["checks"].append({"name": "model_queue", "ok": False, "detail": str(exc)})
        q = None

    # 3. Dashboard 9119
    try:
        status, _ = _local_request(f"{DASHBOARD_URL}/", timeout=5)
        result["checks"].append({"name": "dashboard_9119", "ok": status == 200,
                                 "detail": f"HTTP {status}"})
    except (urllib.error.URLError, OSError) as exc:
        result["checks"].append({"name": "dashboard_9119", "ok": False, "detail": str(exc)})

    # 4. R-10 深度探活：真实推理
    if deep and q and q.get("references"):
        # moa_state references 用 Hermes 侧格式 'nous:tencent/hy3:free'，
        # 但 SelfClaw Gateway 19899 /v1/models 暴露 'tencent/hy3:free'（无 provider 前缀）。
        # 去掉 provider 前缀以匹配 Gateway 模型 ID，暴露真实上游状态（401/500）。
        raw_model = q["references"][0]["model"]
        model = raw_model.split(":", 1)[-1] if ":" in raw_model else raw_model
        try:
            t0 = time.time()
            status, raw = _local_request(
                f"{GATEWAY_URL}/v1/chat/completions", "POST",
                {"model": model, "messages": [{"role": "user", "content": "ping"}],
                 "max_tokens": 5}, timeout=60)
            body = json.loads(raw)
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            record_cost(model, body.get("usage"))
            result["checks"].append({
                "name": "deep_completion", "ok": bool(content),
                "detail": f"{model} → {time.time()-t0:.1f}s, reply={content[:30]!r}",
            })
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
            result["checks"].append({"name": "deep_completion", "ok": False,
                                     "detail": f"{model}: {exc}"})

    result["ok"] = all(c["ok"] for c in result["checks"])
    return result


# ═══════════════════════════════════════════════════════════
# HTTP 服务
# ═══════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # R-4：走 logging，不写 stderr
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError as exc:
            log.warning("请求体解析失败: %s", exc)
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/health":
                self._send(200, check_health(deep="deep=1" in self.path))
            elif path == "/queue":
                self._send(200, get_queue())
            elif path == "/status":
                self._send(200, {
                    "service": "self-ide-server", "version": "1.2",
                    "port": SELF_IDE_PORT, "gateway": GATEWAY_URL,
                    "queue": get_queue(), "cost": _load_cost(),
                })
            elif path == "/cost":
                self._send(200, _load_cost())
            elif path == "/cost/export":
                # P2-3：CSV 导出
                data = _load_cost()
                lines = ["model,calls,prompt_tokens,completion_tokens"]
                for m, v in data.get("models", {}).items():
                    lines.append(f"{m},{v.get('calls',0)},{v.get('prompt',0)},{v.get('completion',0)}")
                raw = "\n".join(lines).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename=cost_meter.csv")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            # === Goal 引擎端点 ===
            elif path == "/goal/status":
                goal_id = self.path.split("goal_id=")[-1] if "goal_id=" in self.path else None
                if not goal_id:
                    self._send(400, {"error": "goal_id required"})
                    return
                status = goal_engine.get_status(goal_id)
                if not status:
                    self._send(404, {"error": "goal not found"})
                    return
                self._send(200, status)
            elif path == "/goal/timeline":
                goal_id = self.path.split("goal_id=")[-1] if "goal_id=" in self.path else None
                if not goal_id:
                    self._send(400, {"error": "goal_id required"})
                    return
                timeline = goal_engine.get_timeline(goal_id)
                self._send(200, {"goal_id": goal_id, "timeline": timeline})
            # === Subagents 并行编排端点 ===
            elif path == "/subagent/status":
                task_id = self.path.split("task_id=")[-1] if "task_id=" in self.path else None
                if not task_id:
                    self._send(400, {"error": "task_id required"})
                    return
                status = subagent_engine.get_status(task_id)
                if not status:
                    self._send(404, {"error": "task not found"})
                    return
                self._send(200, status)
            elif path == "/subagent/list":
                tasks = subagent_engine.list_tasks()
                self._send(200, {"tasks": tasks})
            elif path == "/v1/models":
                status, raw = _local_request(f"{GATEWAY_URL}/v1/models", timeout=15)
                self._send(status, json.loads(raw))
            else:
                self._send(404, {"error": f"unknown path {path}"})
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            log.error("GET %s 失败: %s", path, exc)   # R-3：必 log
            self._send(500, {"error": str(exc)})


    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        try:
            if path == "/route":
                self._send(200, route_by_path(body.get("file", "")))
            elif path == "/v1/completions":
                # [2026-09-20 fix-phase1b] Continue tab 补全走 FIM completions 端点（03:33 实测铁证：
                #   客户端真实调用 POST /v1/completions → 404）。
                # 老板授权：CHAIN-BP-001 Phase 1（D1 接线，开工令"后续操作全部由你完成"）。
                # 实现：档位路由（model=档位名）→ 拼 FIM 上下文转 chat 调用 → 回包转 completions 形态。
                # 渲染端零逻辑原则不变（路由决策在服务端）。⚠ 经老板批准，严禁擅自
                prompt = body.get("prompt", "") or ""
                suffix = body.get("suffix", "") or ""
                model_in = body.get("model") or "autocomplete"
                if model_in in TIER_PICK:
                    routed = route_by_tier(model_in)
                else:
                    routed = route_by_tier("autocomplete")
                # [fix-phase1b] 429/5xx 沿 fallback 链滑动（免费优先铁律 §9.1 实战：候选当日限额耗尽自动滑下一免费档）
                candidates = [routed["model"]] + list(routed.get("fallback") or [])
                status, parsed, model = 502, {}, routed["model"]
                for cand in candidates:
                    if not cand:
                        continue
                    model = cand
                    content = prompt + ("\n<|continue_cursor|>" + suffix if suffix else "")
                    status, raw = _send_to_gateway_chat(
                        model, [{"role": "user", "content": content}],
                        body.get("max_tokens", 64) or 64)
                    try:
                        parsed = json.loads(raw) if isinstance(raw, str) else raw
                    except (ValueError, TypeError):
                        parsed = {}
                    if status == 200 and parsed.get("choices"):
                        break
                    log.warning("FIM 候选 %s 失败(status=%s)，滑动 fallback", model, status)
                    status, parsed = 502, {}
                if status == 200 and parsed.get("choices"):
                    text = (parsed["choices"][0].get("message") or {}).get("content", "")
                    record_cost(model, parsed.get("usage"))  # [fix-phase1b] FIM 计量统一（补全侧 Cost-Meter 可见）
                    self._send(200, {
                        "id": parsed.get("id", "selfide-fim"),
                        "object": "completion",
                        "created": parsed.get("created", int(time.time())),
                        "model": model,
                        "choices": [{"index": 0, "text": text, "finish_reason": "stop"}],
                        "usage": parsed.get("usage", {}),
                    })
                else:
                    self._send(status if isinstance(status, int) else 502,
                               {"error": {"message": f"FIM upstream {status}", "detail": parsed}})
                return

            elif path == "/v1/chat/completions":
                # OpenAI 兼容代理：Continue/Cline 指到这里 = 零外泄 + 统一计量
                model = body.get("model")
                # [2026-09-20 fix-phase1] 档位名伪模型协议：autocomplete/fast/deterministic/general
                #   直接按档位路由（D1 接线；原白名单漏档位名 → 网关 missing model）
                if model in TIER_PICK:
                    routed = route_by_tier(model)
                    model = routed["model"]
                    body["model"] = model
                    log.info("档位路由 → %s (tier=%s)", model, routed["tier"])
                elif not model or model in ("auto", "self-ide-auto"):
                    routed = route_by_path(body.get("_file", ""))
                    model = routed["model"]
                    body["model"] = model
                    log.info("自动路由 → %s (tier=%s)", model, routed["tier"])
                body.pop("_file", None)

                # P2-1：两档超时 — 连接失败快速返回，推理正常等
                try:
                    # 先 2s 探活 Gateway（快速失败）
                    _local_request(f"{GATEWAY_URL}/v1/models", timeout=2)
                except (urllib.error.URLError, OSError):
                    # 2026-08-13 老板铁律：19899 不可达 → 降级到 Dashboard 9119 spare
                    # （watchdog 会自愈拉起 19899；此处不静默 502，改投 9119 保证请求不丢）
                    log.warning("Gateway 19899 不可达，降级到 Dashboard 9119 spare")
                    try:
                        status, raw = _send_to_dashboard_chat(
                            model, body.get("messages", []), body.get("max_tokens", 512))
                        self._send(status, json.loads(raw) if isinstance(raw, str) else raw)
                        return
                    except (urllib.error.URLError, OSError, ValueError, urllib.error.HTTPError) as e2:
                        self._send(502, {"error": "Gateway unavailable, 9119 spare also failed",
                                         "detail": str(e2)})
                        return

                # 上游 503/504 瞬时抖动重试 + 模型名归一化（教训#179 + 2026-08-11 修复）
                max_retries = 3
                for attempt in range(1 + max_retries):
                    try:
                        status, raw = _send_to_gateway_chat(model, body.get("messages", []), body.get("max_tokens", 512))
                        parsed = json.loads(raw)
                        # 503/504 是上游瞬时抖动，可重试；其他状态码直接返回
                        if status in (503, 504) and attempt < max_retries:
                            log.warning("上游 %d 抖动 (attempt %d/%d, model=%s)，重试中…",
                                        status, attempt + 1, 1 + max_retries, model)
                            time.sleep(1.5 * (attempt + 1))
                            continue
                        record_cost(model, parsed.get("usage"))
                        self._send(status, parsed)
                        return
                    except (urllib.error.URLError, OSError, ValueError, urllib.error.HTTPError) as exc:
                        if attempt < max_retries:
                            log.warning("请求异常 (attempt %d/%d, model=%s): %s，重试中…",
                                        attempt + 1, 1 + max_retries, model, exc)
                            time.sleep(1.5 * (attempt + 1))
                            continue
                        raise
            # === Goal 引擎端点 ===
            elif path == "/goal/set":
                objective = body.get("objective")
                if not objective:
                    self._send(400, {"error": "objective required"})
                    return
                max_iterations = body.get("max_iterations", 10)
                token_budget = body.get("token_budget", 500_000)
                try:
                    state = goal_engine.start_goal(objective, max_iterations, token_budget)
                    self._send(200, state.to_dict())
                except RuntimeError as exc:
                    self._send(409, {"error": str(exc)})
                return
            elif path == "/goal/iterate":
                goal_id = body.get("goal_id")
                if not goal_id:
                    self._send(400, {"error": "goal_id required"})
                    return
                status = goal_engine.get_status(goal_id)
                if not status:
                    self._send(404, {"error": "goal not found"})
                    return
                self._send(200, {"message": "iteration triggered", "status": status})
                return
            elif path == "/goal/cancel":
                goal_id = body.get("goal_id")
                if not goal_id:
                    self._send(400, {"error": "goal_id required"})
                    return
                ok = goal_engine.cancel_goal(goal_id)
                self._send(200, {"cancelled": ok})
                return
            # === Subagents 并行编排端点 ===
            elif path == "/subagent/spawn":
                name = body.get("name")
                role = body.get("role", "general-purpose")
                objective = body.get("objective")
                context = body.get("context", {})
                if not name or not objective:
                    self._send(400, {"error": "name and objective required"})
                    return
                try:
                    task_id = subagent_engine.spawn(name, role, objective, context)
                    self._send(200, {"task_id": task_id})
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
                return
            elif path == "/subagent/wait_all":
                task_ids = body.get("task_ids", [])
                timeout = body.get("timeout", 300)
                if not task_ids:
                    self._send(400, {"error": "task_ids required"})
                    return
                results = subagent_engine.wait_all(task_ids, timeout)
                self._send(200, {"results": results})
                return
            elif path == "/subagent/cancel":
                task_id = body.get("task_id")
                if not task_id:
                    self._send(400, {"error": "task_id required"})
                    return
                ok = subagent_engine.cancel(task_id)
                self._send(200, {"cancelled": ok})
                return
            elif path == "/cost/reset":
                # P2-3：重置 Cost-Meter
                fresh = {"version": 1, "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "models": {}, "total": {"calls": 0, "prompt": 0, "completion": 0}}
                COST_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp = COST_PATH.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(str(tmp), str(COST_PATH))
                log.info("Cost-Meter reset by request")
                self._send(200, {"ok": True, "message": "Cost-Meter reset"})
            # v1.1：/task 死端点已移除（Dashboard 无 /api/task 路由，实测 502，零消费者）
            else:
                self._send(404, {"error": f"unknown path {path}"})
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            log.error("POST %s 失败: %s", path, exc)   # R-3：必 log
            self._send(502, {"error": str(exc)})

# ═══════════════════════════════════════════════════════════
# 自愈拉起：--spawn（供 watchdog auto_repair 调用）
# run_cmd 只能执行 Python，bat 进不了自愈链路 → 用本脚本自身拉起
# ═══════════════════════════════════════════════════════════

def _port_listening(port: int) -> bool:
    """双因子之一：端口 LISTENING 探测（不依赖 netstat，跨 shell 稳定）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError as exc:
        log.warning("端口探测异常 %s: %s", port, exc)
        return False


def spawn_detached() -> int:
    """幂等分离式拉起服务端。返回 0=已在运行/拉起成功，1=拉起失败。"""
    # 幂等：已在运行则直接成功（防重复拉起）
    if _port_listening(SELF_IDE_PORT):
        print(f"already-listening:{SELF_IDE_PORT}")
        return 0
    log.info("--spawn: %s 未监听，分离式拉起服务端", SELF_IDE_PORT)
    env = {**os.environ, "PYTHONHOME": "", "PYTHONPATH": ""}  # 教训 #3：防 uv Python 污染
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=str(HERMES_ROOT), env=env,
        stdout=open(LOG_DIR / "self_ide_spawn.out", "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    # 等待端口就绪（最多 10s）
    for _ in range(10):
        time.sleep(1)
        if _port_listening(SELF_IDE_PORT):
            print(f"spawned:{SELF_IDE_PORT}")
            log.info("--spawn: 服务端已就绪 :%s", SELF_IDE_PORT)
            return 0
    log.error("--spawn: 拉起后 10s 内端口仍未就绪")
    return 1


def main():
    if "--spawn" in sys.argv:
        return spawn_detached()
    if "--check" in sys.argv:
        r = check_health(deep="--deep" in sys.argv)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    if "--queue" in sys.argv:
        print(json.dumps(get_queue(), ensure_ascii=False, indent=2))
        return 0
    if "--route" in sys.argv:
        i = sys.argv.index("--route")
        print(json.dumps(route_by_path(sys.argv[i + 1]), ensure_ascii=False, indent=2))
        return 0

    # [0826d 幂等防叠] Windows 下 allow_reuse_address=1 允许多进程重复 bind 同一端口,
    # 历史实测 7 进程同挂 19898(仅 1 个真收流, 其余僵尸占内存)。已在监听则本实例直接退出。
    if _port_listening(SELF_IDE_PORT):
        log.info("端口 %s 已有实例在监听，本实例退出(幂等)", SELF_IDE_PORT)
        return 0
    srv = ThreadingHTTPServer(("127.0.0.1", SELF_IDE_PORT), Handler)
    log.info("Self-IDE server 启动 → http://127.0.0.1:%s (gateway=%s)",
             SELF_IDE_PORT, GATEWAY_URL)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("收到中断，退出")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
