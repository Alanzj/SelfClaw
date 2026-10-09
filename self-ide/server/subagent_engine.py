#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
subagent_engine.py — Self-IDE Subagents 并行编排引擎

核心能力：
- 子 Agent 并行执行：ThreadPoolExecutor + 独立 message history
- 子 Agent 定义：Markdown 格式配置文件（~/.self-ide/subagents/*.md）
- 内置角色：general-purpose（全工具）、explore（只读）
- 冲突防护：文件系统写入互斥锁
- 状态跟踪：spawn/status/wait_all/list 端点

设计原则：
- 复用现有组件：Quant-Router、MOA 队列、Cost-Meter
- 每个子 Agent 独立上下文（message history 隔离）
- 落在 hermes_overlay/self_ide/（升级免疫）
"""
import json
import logging
import os
import threading
import time
import uuid
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from enum import Enum

log = logging.getLogger("self-ide.subagent")

# ─── 路径常量 ────────────────────────────────────────────────
HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", str(Path.home() / "hermes")))
SUBAGENT_DIR = HERMES_ROOT / "hermes_overlay" / "self_ide" / "subagents"
SUBAGENT_STATE_PATH = HERMES_ROOT / "hermes_overlay" / "self_ide" / "subagent_state.json"
SELF_IDE_PORT = int(os.environ.get("SELF_IDE_PORT", 19898))
GATEWAY_URL = f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '19899')}"


class SubagentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubagentRole(Enum):
    GENERAL = "general-purpose"   # 全工具：读写执行
    EXPLORE = "explore"           # 只读：Read/Grep/Glob，代码定位/调用链分析


@dataclass
class SubagentConfig:
    """子 Agent 定义（Markdown 格式可序列化）"""
    name: str
    role: str = SubagentRole.GENERAL.value
    model: str = "auto"  # 由 Quant-Router 自动路由
    tools: List[str] = field(default_factory=lambda: ["read", "write", "edit", "bash", "grep", "glob"])
    system_prompt: str = ""
    description: str = ""
    max_tokens: int = 50_000
    timeout_sec: int = 300


@dataclass
class SubagentTask:
    """子 Agent 任务"""
    task_id: str
    name: str
    role: str
    objective: str
    context: Dict = field(default_factory=dict)  # 传入上下文
    status: str = SubagentStatus.PENDING.value
    result: Optional[Dict] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    tokens_used: int = 0


@dataclass
class SubagentState:
    """Subagent 全局状态"""
    session_id: str
    tasks: List[Dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "SubagentState":
        return cls(**data)


# ─── 状态持久化（原子写）───────────────────────────────────
_state_lock = threading.Lock()

def _atomic_write_subagent(state: SubagentState) -> None:
    with _state_lock:
        SUBAGENT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = state.to_dict()
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = SUBAGENT_STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(SUBAGENT_STATE_PATH))


def load_subagent_state() -> SubagentState:
    try:
        if not SUBAGENT_STATE_PATH.exists():
            return SubagentState(session_id=str(uuid.uuid4())[:8], created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        data = json.loads(SUBAGENT_STATE_PATH.read_text(encoding="utf-8"))
        return SubagentState.from_dict(data)
    except (OSError, ValueError) as exc:
        log.warning("加载 Subagent 状态失败: %s", exc)
        return SubagentState(session_id=str(uuid.uuid4())[:8], created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))


def save_subagent_state(state: SubagentState) -> None:
    _atomic_write_subagent(state)


# ─── 内置子 Agent 定义（Markdown 格式）───────────────────────
DEFAULT_SUBAGENTS = {
    "general-purpose": SubagentConfig(
        name="general-purpose",
        role=SubagentRole.GENERAL.value,
        model="auto",
        tools=["read", "write", "edit", "bash", "grep", "glob", "list_dir"],
        system_prompt="""You are a general-purpose coding agent. You can read, write, edit files, run commands, search code.
Follow the user's objective precisely. Report progress and results clearly.""",
        description="通用编码 Agent：读写文件、运行命令、搜索代码、执行重构",
    ),
    "explore": SubagentConfig(
        name="explore",
        role=SubagentRole.EXPLORE.value,
        model="auto",
        tools=["read", "grep", "glob", "list_dir"],
        system_prompt="""You are a code exploration agent. You can ONLY read files, search code, list directories.
You CANNOT write, edit, delete, or run commands. Your job is to locate code, trace call chains, analyze architecture.
Report findings with file paths and line numbers.""",
        description="只读探索 Agent：代码定位、调用链追踪、架构分析",
    ),
}


# ─── 调用 Gateway(19899) 直连，避免递归回 Self-IDE ────────
def _gw_request(path: str, method: str = "GET", body: Optional[Dict] = None, timeout: int = 60) -> Dict:
    url = f"{GATEWAY_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "detail": exc.read().decode("utf-8", errors="replace")[:200]}


def _chat_completions(model: str, messages: List[Dict], max_tokens: int = 2048) -> Dict:
    """直接调用 Gateway /v1/chat/completions"""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
    return _gw_request("/v1/chat/completions", "POST", body, timeout=120)


# ─── 文件写入互斥锁（防并发冲突）───────────────────────────
_file_locks: Dict[str, threading.Lock] = {}
_file_locks_lock = threading.Lock()

def get_file_lock(filepath: str) -> threading.Lock:
    """获取文件级写入锁"""
    with _file_locks_lock:
        if filepath not in _file_locks:
            _file_locks[filepath] = threading.Lock()
        return _file_locks[filepath]


# ─── Subagent 执行器───────────────────────────────────────
class SubagentExecutor:
    """单个子 Agent 的执行器（独立 message history）"""

    def __init__(self, config: SubagentConfig):
        self.config = config
        self.message_history: List[Dict] = []
        self._init_messages()

    def _init_messages(self):
        if self.config.system_prompt:
            self.message_history.append({"role": "system", "content": self.config.system_prompt})

    def execute(self, objective: str, context: Dict = None) -> Dict:
        """执行子 Agent 任务（同步阻塞）"""
        context = context or {}
        self.message_history.append({"role": "user", "content": objective})

        # 调用 Self-IDE chat/completions 代理执行任务
        try:
            resp = _chat_completions(
                self.config.model,
                self.message_history,
                max_tokens=min(self.config.max_tokens, 4096),
            )
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = resp.get("usage", {})
            
            # 记录到历史，支持后续迭代
            self.message_history.append({"role": "assistant", "content": content})
            
            result = {
                "status": "completed",
                "summary": content[:1000],
                "full_content": content,
                "tokens_used": usage.get("total_tokens", 0),
                "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            log.info("Subagent %s completed, tokens=%d", self.config.name, usage.get("total_tokens", 0))
            return result
        except Exception as exc:
            log.exception("Subagent %s execution failed: %s", self.config.name, exc)
            return {
                "status": "failed",
                "error": str(exc),
                "tokens_used": 0,
                "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }


class SubagentEngine:
    """Subagent 并行编排引擎"""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._running_tasks: Dict[str, SubagentTask] = {}
        self._lock = threading.Lock()

    def spawn(self, name: str, role: str, objective: str, context: Dict = None) -> str:
        """生成并启动子 Agent 任务，返回 task_id"""
        config = DEFAULT_SUBAGENTS.get(name)
        if not config:
            config = self._load_custom_agent(name)
        if not config:
            raise ValueError(f"Unknown subagent: {name}")

        task_id = str(uuid.uuid4())[:8]
        task = SubagentTask(
            task_id=task_id,
            name=name,
            role=role or config.role,
            objective=objective,
            context=context or {},
        )

        with self._lock:
            self._running_tasks[task_id] = task

        future = self.executor.submit(self._run_task, task_id, config, objective)
        future.add_done_callback(lambda f: self._on_task_done(task_id, f))

        log.info("Subagent spawned: %s (%s) task_id=%s", name, role, task_id)
        return task_id

    def _load_custom_agent(self, name: str) -> Optional[SubagentConfig]:
        """从磁盘加载自定义 Agent 定义（Markdown 格式）"""
        md_path = SUBAGENT_DIR / f"{name}.md"
        if not md_path.exists():
            return None
        # 简化解析：读取 name/model/tools/system_prompt
        try:
            content = md_path.read_text(encoding="utf-8")
            lines = {l.strip(): "" for l in content.split("\n")}
            # 下面用正则提取配置
            import re
            name_m = re.search(r"^\*\*Role\*\*:\s*(\S+)", content, re.M)
            model_m = re.search(r"^\*\*Model\*\*:\s*(\S+)", content, re.M)
            tools_m = re.search(r"^\*\*Tools\*\*:\s*(.+)", content, re.M)
            sys_m = re.search(r"## System Prompt\s*\n(.+?)(?=\n## |\Z)", content, re.S)
            desc_m = re.search(r"## Description\s*\n(.+?)(?=\n## |\Z)", content, re.S)
            
            cfg = SubagentConfig(
                name=name,
                role=name_m.group(1) if name_m else "general-purpose",
                model=model_m.group(1) if model_m else "auto",
                tools=[t.strip() for t in (tools_m.group(1).split(",") if tools_m else ["read"])],
                system_prompt=sys_m.group(1).strip() if sys_m else "",
                description=desc_m.group(1).strip() if desc_m else "",
            )
            log.info("Loaded custom subagent: %s", name)
            return cfg
        except Exception as exc:
            log.warning("Failed to load custom subagent %s: %s", name, exc)
            return None

    def _run_task(self, task_id: str, config: SubagentConfig, objective: str) -> Dict:
        """在线程池中运行子 Agent"""
        task = self._running_tasks[task_id]
        task.status = SubagentStatus.RUNNING.value
        task.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")

        try:
            agent = SubagentExecutor(config)
            result = agent.execute(objective, task.context)
            task.result = result
            task.status = SubagentStatus.COMPLETED.value if result.get("status") == "completed" else SubagentStatus.FAILED.value
            task.tokens_used = result.get("tokens_used", 0)
            if result.get("status") == "failed":
                task.error = result.get("error")
        except Exception as exc:
            log.exception("Subagent %s task %s failed: %s", config.name, task_id, exc)
            task.status = SubagentStatus.FAILED.value
            task.error = str(exc)
        finally:
            task.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        return task.__dict__

    def _on_task_done(self, task_id: str, future):
        with self._lock:
            if task_id in self._running_tasks:
                task = self._running_tasks[task_id]
                self._persist_task(task)

    def _persist_task(self, task: SubagentTask):
        state = load_subagent_state()
        task_dict = task.__dict__
        # 更新或追加到任务列表
        for i, t in enumerate(state.tasks):
            if t.get("task_id") == task.task_id:
                state.tasks[i] = task_dict
                save_subagent_state(state)
                return
        state.tasks.append(task_dict)
        save_subagent_state(state)

    def wait_all(self, task_ids: List[str], timeout: float = None) -> List[Dict]:
        """等待多个任务完成，返回结果列表"""
        results = []
        deadline = time.time() + (timeout or 60)
        for task_id in task_ids:
            while time.time() < deadline:
                with self._lock:
                    task = self._running_tasks.get(task_id)
                if task and task.status in (SubagentStatus.COMPLETED.value, SubagentStatus.FAILED.value, SubagentStatus.CANCELLED.value):
                    results.append(task.__dict__)
                    break
                time.sleep(0.2)
        return results

    def get_status(self, task_id: str) -> Optional[Dict]:
        with self._lock:
            task = self._running_tasks.get(task_id)
        if task:
            return task.__dict__
        return None

    def list_tasks(self) -> List[Dict]:
        with self._lock:
            return [t.__dict__ for t in self._running_tasks.values()]

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._running_tasks.get(task_id)
            if task and task.status == SubagentStatus.RUNNING.value:
                task.status = SubagentStatus.CANCELLED.value
                return True
        return False

    def shutdown(self):
        self.executor.shutdown(wait=True)


# 单例实例
subagent_engine = SubagentEngine(max_workers=4)