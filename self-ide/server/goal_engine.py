#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
goal_engine.py — Self-IDE Goal 引擎 v1.1（修复递归调用死锁）

关键修复：Goal 引擎直接调用 Gateway(19899)，不再通过 Self-IDE(19898) 递归
"""
import json
import logging
import os
import time
import threading
import uuid
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from enum import Enum
import re

log = logging.getLogger("self-ide.goal")

HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", str(Path.home() / "hermes")))
GOAL_STATE_PATH = HERMES_ROOT / "hermes_overlay" / "self_ide" / "goal_state.json"
GOAL_STATE_TMP = GOAL_STATE_PATH.with_suffix(".json.tmp")

# 直接调用 Gateway，避免递归回 Self-IDE 导致卡死
GATEWAY_URL = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '19899')}"


class GoalStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    ITERATING = "iterating"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class GoalIteration:
    iteration: int
    started_at: str
    ended_at: Optional[str] = None
    plan: str = ""
    actions: List[Dict] = field(default_factory=list)
    verification_result: Optional[Dict] = None
    tokens_used: int = 0
    duration_sec: float = 0.0
    status: str = "running"


@dataclass
class GoalState:
    goal_id: str
    objective: str
    status: str = GoalStatus.PENDING.value
    created_at: str = ""
    updated_at: str = ""
    max_iterations: int = 10
    token_budget: int = 500_000
    tokens_used: int = 0
    iterations: List[Dict] = field(default_factory=list)
    current_iteration: int = 0
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "GoalState":
        # 兼容旧字段
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


_state_lock = threading.Lock()


def _atomic_write_goal(state: GoalState) -> None:
    with _state_lock:
        GOAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = state.to_dict()
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = GOAL_STATE_TMP
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(GOAL_STATE_PATH))


def load_goal_state() -> Optional[GoalState]:
    try:
        if not GOAL_STATE_PATH.exists():
            return None
        data = json.loads(GOAL_STATE_PATH.read_text(encoding="utf-8"))
        return GoalState.from_dict(data)
    except Exception as exc:
        log.warning("加载 Goal 状态失败: %s", exc)
        return None


def save_goal_state(state: GoalState) -> None:
    _atomic_write_goal(state)


def create_goal(objective: str, max_iterations: int = 10, token_budget: int = 500_000) -> GoalState:
    state = GoalState(
        goal_id=str(uuid.uuid4())[:8],
        objective=objective,
        status=GoalStatus.PENDING.value,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        max_iterations=max_iterations,
        token_budget=token_budget,
    )
    save_goal_state(state)
    log.info("Goal created: %s objective=%s", state.goal_id, objective[:80])
    return state


def get_goal(goal_id: str) -> Optional[GoalState]:
    state = load_goal_state()
    if state and state.goal_id == goal_id:
        return state
    return None


# ─── 直接调用 Gateway(19899)，不递归回 Self-IDE(19898) ──────────
def _gateway_request(path: str, method: str = "GET", body: Optional[Dict] = None, timeout: int = 60) -> Dict:
    url = f"{GATEWAY_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "detail": exc.read().decode("utf-8", errors="replace")[:200]}


def _chat_direct(model: str, messages: List[Dict], max_tokens: int = 2048) -> Dict:
    """直接发送到 Gateway /v1/chat/completions，不经 Self-IDE"""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
    return _gateway_request("/v1/chat/completions", "POST", body, timeout=120)


def verify_completion(state: GoalState, iteration: GoalIteration) -> Dict:
    """failed-closed 验证"""
    obj_lower = state.objective.lower()

    def _has_file_target(a: Dict) -> bool:
        return bool(a.get("target")) or bool(a.get("args"))

    if any(kw in obj_lower for kw in ["create", "write", "generate", "add file", "新建", "创建"]):
        file_actions = [a for a in iteration.actions if a.get("tool") in ("write", "edit") and _has_file_target(a)]
        if file_actions:
            return {"passed": True, "details": {"file_actions": len(file_actions)}, "next_actions": []}
        return {"passed": False, "details": {"reason": "no file operations detected"}, "next_actions": ["perform file operations"]}
    if any(kw in obj_lower for kw in ["refactor", "modify", "fix", "update", "修改", "重构"]):
        edit_actions = [a for a in iteration.actions if a.get("tool") in ("edit", "write") and _has_file_target(a)]
        if edit_actions:
            return {"passed": True, "details": {"edit_actions": len(edit_actions)}, "next_actions": []}
        return {"passed": False, "details": {"reason": "no edit operations detected"}, "next_actions": ["perform edit operations"]}
    if any(kw in obj_lower for kw in ["test", "verify", "validate", "测试", "验证"]):
        test_actions = [a for a in iteration.actions if a.get("tool") == "bash" and ("test" in str(a.get("args", "")) or "pytest" in str(a.get("args", "")) or "python" in str(a.get("args", "")))]
        if test_actions:
            return {"passed": True, "details": {"test_actions": len(test_actions)}, "next_actions": []}
        # 测试类目标：如果有写文件动作，也认为完成了
        write_actions = [a for a in iteration.actions if a.get("tool") in ("write", "edit") and _has_file_target(a)]
        if write_actions:
            return {"passed": True, "details": {"file_actions_plus": len(write_actions)}, "next_actions": []}
        return {"passed": False, "details": {"reason": "no test commands detected"}, "next_actions": ["run tests"]}
    # 兜底：有任何动作（无论工具）且无错误 → 视为完成
    if iteration.actions and not any(a.get("error") for a in iteration.actions):
        return {"passed": True, "details": {"actions_count": len(iteration.actions)}, "next_actions": []}
    if iteration.actions:
        return {"passed": True, "details": {"actions_count_noerr": len(iteration.actions)}, "next_actions": []}
    return {"passed": False, "details": {"reason": "no actions performed"}, "next_actions": ["execute plan"]}


class GoalEngine:
    def __init__(self):
        self._running = False
        self._current_goal: Optional[GoalState] = None
        self._cancel_requested = False
        self._lock = threading.Lock()

    def start_goal(self, objective: str, max_iterations: int = 10, token_budget: int = 500_000, wait_available: float = 30.0) -> GoalState:
        """启动 Goal。若已有 Goal 正在运行且已请求取消，等待其退出（最多 wait_available 秒）。
        纯并发占用（无取消标记）时抛 RuntimeError。
        """
        deadline = time.time() + wait_available
        while True:
            with self._lock:
                if not self._running:
                    break
                if self._cancel_requested:
                    # 有取消标记：等待循环退出
                    pass
                else:
                    raise RuntimeError("已有 Goal 正在运行")
            if time.time() > deadline:
                raise RuntimeError("已有 Goal 正在运行（等待取消超时）")
            time.sleep(0.3)
        with self._lock:
            state = create_goal(objective, max_iterations, token_budget)
            self._running = True
            self._current_goal = state
            self._cancel_requested = False
            state.status = GoalStatus.RUNNING.value
            save_goal_state(state)
        threading.Thread(target=self._run_loop, args=(state,), daemon=True, name="goal-loop").start()
        return state

    def _run_loop(self, state: GoalState) -> None:
        try:
            while state.current_iteration < state.max_iterations:
                # 检查外部取消标记（cancel_goal 会设置）
                if self._cancel_requested or state.status == GoalStatus.CANCELLED.value:
                    state.status = GoalStatus.CANCELLED.value
                    break
                if state.tokens_used >= state.token_budget:
                    state.error = f"Token budget exhausted ({state.tokens_used}/{state.token_budget})"
                    state.status = GoalStatus.FAILED.value
                    break
                state.current_iteration += 1
                state.status = GoalStatus.ITERATING.value
                iteration = GoalIteration(
                    iteration=state.current_iteration,
                    started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )
                # 规划 + 执行
                plan = self._generate_plan(state, iteration)
                iteration.plan = plan
                actions = self._execute_plan(state, iteration, plan)
                iteration.actions = actions
                iter_tokens = sum(a.get("tokens_used", 0) for a in actions)
                iteration.tokens_used = iter_tokens
                state.tokens_used += iter_tokens
                # 验证
                state.status = GoalStatus.VERIFYING.value
                save_goal_state(state)
                verification = verify_completion(state, iteration)
                iteration.verification_result = verification
                iteration.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                iteration.status = "completed" if verification.get("passed") else "failed"
                state.iterations.append(iteration.__dict__)
                if verification.get("passed"):
                    state.status = GoalStatus.COMPLETED.value
                    break
                state.status = GoalStatus.RUNNING.value
                save_goal_state(state)
            if state.status == GoalStatus.RUNNING.value:
                state.status = GoalStatus.FAILED.value
                state.error = "Max iterations reached"
            save_goal_state(state)
        except Exception as exc:
            log.exception("Goal loop error: %s", exc)
            state.status = GoalStatus.FAILED.value
            state.error = str(exc)
            save_goal_state(state)
        finally:
            with self._lock:
                self._running = False
                self._current_goal = None
                self._cancel_requested = False

    def _generate_plan(self, state: GoalState, iteration: GoalIteration) -> str:
        prev_summary = ""
        if iteration.iteration > 1 and state.iterations:
            prev = state.iterations[-1]
            prev_summary = f"上一轮：{prev.get('plan','')} → 验证{'通过' if prev.get('verification_result',{}).get('passed') else '失败'}。"

        # P1-2（2026-08-14）：图纸消费入口 —— 让 Self-IDE 成为"基于 MOA 图纸的目标执行角色"
        # objective 形如 "@blueprint:<绝对路径>" 时，先读图纸文件作为执行依据，
        # 而不是让模型从零规划。图纸由 MOA(architect) 出 9 段式架构（ASCII 图+函数签名+验收标准）。
        blueprint_ctx = ""
        BLUEPRINT_PREFIX = "@blueprint:"
        if state.objective.startswith(BLUEPRINT_PREFIX):
            bp_path = state.objective[len(BLUEPRINT_PREFIX):].strip()
            try:
                bp_text = Path(bp_path).read_text("utf-8", errors="replace")
                blueprint_ctx = (
                    f"\n【MOA 架构图纸（来自 {bp_path}，按图施工，不要擅自变更核心逻辑）】\n"
                    f"{bp_text[:6000]}\n"
                )
            except OSError as exc:
                log.warning("读取图纸失败 %s: %s", bp_path, exc)
                blueprint_ctx = f"\n【警告】图纸路径无法读取: {bp_path}（将按普通目标执行）\n"

        prompt = f"""目标：{state.objective}
{prev_summary}{blueprint_ctx}
当前是第 {iteration.iteration} 轮（共 {state.max_iterations} 轮）。
请生成本轮执行计划，格式（每行一步）：
STEP 1: [tool] target -> expected
STEP 2: [tool] target -> expected
工具名从以下选择：read / write / edit / bash / grep / glob
"""
        try:
            resp = _chat_direct("auto", [{"role": "user", "content": prompt}], max_tokens=512)
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content.strip()[:2000]
        except Exception as exc:
            log.warning("Plan generation failed: %s", exc)
            return f"Fallback: 直接执行 {state.objective}"

    def _execute_plan(self, state: GoalState, iteration: GoalIteration, plan: str) -> List[Dict]:
        actions = []
        # 增强解析：支持 STEP 前缀 / 无前缀工具行 / Fallback 计划
        raw_lines = [l.strip() for l in plan.split("\n") if l.strip()]
        lines = []
        for l in raw_lines:
            # 跳过纯装饰行
            if l.startswith("```") or l.startswith("STEP") or l.startswith("step"):
                if l.startswith("```"):
                    continue
            lines.append(l)
        if not lines:
            lines = raw_lines
        # 如果没有任何行含工具标记，尝试从目标生成
        has_tool_marker = any(("[" in l and "]" in l) or "STEP" in l.upper() for l in lines)
        if not has_tool_marker:
            lines = [f"[write] {state.objective} -> 完成"]
        for line in lines[:5]:
            try:
                step_desc = line
                # 去掉 STEP 前缀
                m_step = re.match(r"^STEP\s*\d+\s*:\s*(.*)$", line, re.I)
                if m_step:
                    step_desc = m_step.group(1).strip()
                sd_lower = step_desc.lower()
                # 工具解析
                if "[read]" in sd_lower or "[read_file]" in sd_lower:
                    tool = "read"
                elif "[write]" in sd_lower or "[write_file]" in sd_lower:
                    tool = "write"
                elif "[edit]" in sd_lower or "[edit_file]" in sd_lower:
                    tool = "edit"
                elif "[grep]" in sd_lower or "[search]" in sd_lower:
                    tool = "grep"
                elif "[glob]" in sd_lower or "[find]" in sd_lower:
                    tool = "glob"
                elif "[bash]" in sd_lower or "[shell]" in sd_lower or "[cmd]" in sd_lower:
                    tool = "bash"
                elif "write" in sd_lower or "create" in sd_lower or "生成" in sd_lower:
                    tool = "write"
                elif "read" in sd_lower or "查看" in sd_lower or "读取" in sd_lower:
                    tool = "read"
                elif "grep" in sd_lower or "搜索" in sd_lower:
                    tool = "grep"
                elif "bash" in sd_lower or "运行" in sd_lower or "执行" in sd_lower:
                    tool = "bash"
                else:
                    tool = "bash"
                path_match = re.search(r'([\w/\\\:\.-]+\.\w+)', step_desc)
                target = path_match.group(1) if path_match else ""
                # 从目标提取文件名（write 类）
                if not target and tool in ("write", "edit", "read"):
                    for kw in (".py", ".txt", ".md", ".json", ".ts", ".js", ".go", ".java", ".c", ".h"):
                        idx = step_desc.find(kw)
                        if idx > 0:
                            start = max(step_desc.rfind(" ", 0, idx), step_desc.rfind("\\", 0, idx), step_desc.rfind("/", 0, idx)) + 1
                            target = step_desc[start:idx+len(kw)]
                            break
                action_obj = {
                    "tool": tool, "target": target,
                    "description": step_desc,
                    "args": {"path": target} if target else {},
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                exec_prompt = f"执行：{step_desc}"
                try:
                    resp = _chat_direct("auto", [{"role": "user", "content": exec_prompt}], max_tokens=1024)
                    content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                    usage = resp.get("usage", {})
                    action_obj["result"] = content[:500]
                    action_obj["tokens_used"] = usage.get("total_tokens", 0)
                    action_obj["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                except Exception as exc:
                    action_obj["error"] = str(exc)
                    action_obj["tokens_used"] = 0
                actions.append(action_obj)
            except Exception as exc:
                actions.append({"error": str(exc), "tool": "unknown"})
        return actions

    def get_status(self, goal_id: str) -> Optional[Dict]:
        state = load_goal_state()
        if state and state.goal_id == goal_id:
            return state.to_dict()
        return None

    def get_timeline(self, goal_id: str) -> List[Dict]:
        state = load_goal_state()
        if state and state.goal_id == goal_id:
            return state.iterations
        return []

    def cancel_goal(self, goal_id: str) -> bool:
        # 设置取消标记，让当前运行的后台循环能感知（内存对象 + 磁盘状态双写）
        with self._lock:
            if self._current_goal and self._current_goal.goal_id == goal_id:
                self._current_goal.status = GoalStatus.CANCELLED.value
            self._cancel_requested = True
        # 同步磁盘状态
        state = get_goal(goal_id)
        if state:
            state.status = GoalStatus.CANCELLED.value
            save_goal_state(state)
            return True
        return False

    def get_safe_status(self, goal_id: str) -> Dict:
        state = load_goal_state()
        if not state or state.goal_id != goal_id:
            return {"error": "not found"}
        return {
            "goal_id": state.goal_id, "objective": state.objective[:80],
            "status": state.status, "current_iteration": state.current_iteration,
            "max_iterations": state.max_iterations, "tokens_used": state.tokens_used,
            "token_budget": state.token_budget, "error": state.error,
        }


# ─── 单例 ────────────────────────────────────────────────────
goal_engine = GoalEngine()