# -*- coding: utf-8 -*-
"""CCR 闭环智能体 v2.1 —— Hermes 强化 + 离线双向语音
优化点：
1. 规范变量命名/代码注释，提升可读性
2. 增强异常处理，减少崩溃风险
3. 优化路径处理逻辑，避免跨盘符越权
4. 完善类型注解，提升代码可维护性
5. 优化内存数据管理，避免内存泄漏
6. 增加配置项集中管理，便于维护
"""
import os
import sys
import time
import json
import threading
from typing import List, Dict, Any, Optional, Tuple
from openai import OpenAI
import httpx

# -------------------------- 配置项集中管理 --------------------------
WORKSPACE_DIR = "D:/"  # 工作空间根目录（限制文件操作范围）
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ccr_memory.json")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB 文件大小限制
MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10MB 内容大小限制
MAX_STEPS = 8  # 智能体最大执行步骤
VOICE_RATE = 150  # 语音朗读速率
API_TIMEOUT = 60.0  # API 请求超时时间
API_MAX_RETRIES = 1  # API 最大重试次数
MEMORY_MAX_ERRORS = 200  # 错误记录最大条数

# -------------------------- 语音模块初始化（edge-tts TTS + 离线 STT） --------------------------
# TTS 语音合成引擎（优先 edge-tts，回退 pyttsx3）
_TTS_ENGINE = None
_TTS_BACKEND = "none"  # "edge-tts" | "pyttsx3" | "none"
try:
    import edge_tts
    _TTS_BACKEND = "edge-tts"
except ImportError:
    try:
        import pyttsx3
        _TTS_ENGINE = pyttsx3.init()
        _TTS_ENGINE.setProperty('rate', VOICE_RATE)
        _TTS_BACKEND = "pyttsx3"
    except Exception:
        pass

# ASR 语音识别引擎
_RECOGNIZER = None
_MICROPHONE = None
try:
    import speech_recognition as sr
    _RECOGNIZER = sr.Recognizer()
    _MICROPHONE = sr.Microphone()
except ImportError:
    print("警告：未安装 speech_recognition，语音识别功能不可用", file=sys.stderr)
except Exception as e:
    print(f"警告：初始化 ASR 引擎失败 - {str(e)}，语音识别功能不可用", file=sys.stderr)

# 全局语音开关
voice_input_enabled = False
tts_enabled = False

# TTS 播放控制
_tts_stop_event = threading.Event()
_tts_thread = None

# -------------------------- 语音功能函数 --------------------------
def speech_to_text() -> str:
    """语音识别（中文），返回识别文本，失败返回空字符串。
    优先使用 Google 在线识别（精度高），失败后回退离线 PocketSphinx。
    """
    if not _RECOGNIZER or not _MICROPHONE:
        print("错误：语音识别引擎未初始化", file=sys.stderr)
        return ""

    try:
        with _MICROPHONE as source:
            _RECOGNIZER.adjust_for_ambient_noise(source, duration=0.5)
            audio = _RECOGNIZER.listen(source, timeout=5, phrase_time_limit=10)
    except Exception as e:
        print(f"错误：麦克风录音失败 - {str(e)}", file=sys.stderr)
        return ""

    # 尝试 Google 在线识别
    try:
        text = _RECOGNIZER.recognize_google(audio, language="zh-CN")
        return text.strip() if text else ""
    except sr.UnknownValueError:
        pass  # 无法理解，尝试离线
    except sr.RequestError:
        print("提示：Google 识别不可用，尝试离线识别...", file=sys.stderr)
    except Exception:
        pass

    # 回退：离线 PocketSphinx（如已安装）
    try:
        text = _RECOGNIZER.recognize_sphinx(audio, language="zh-CN")
        return text.strip() if text else ""
    except Exception:
        pass

    return ""


def _play_audio_file(filepath: str) -> None:
    """跨平台播放音频文件（阻塞直到播放完成或被 stop）"""
    import subprocess
    _tts_stop_event.clear()
    if _tts_stop_event.is_set():
        return
    try:
        if sys.platform == "win32":
            # Windows: 用 PowerShell 播放（支持 MP3/WAV）
            ps_cmd = (
                f"Add-Type -AssemblyName PresentationCore; "
                f"$p = New-Object Windows.Media.MediaPlayer; "
                f"$p.Open([System.IO.Path]::GetFullPath('{filepath}')); "
                f"$p.Play(); "
                f"Start-Sleep -Milliseconds 300; "
                f"while ($p.NaturalDuration.HasTimeSpan -and $p.Position -lt $p.NaturalDuration.TimeSpan) {{ "
                f"  Start-Sleep -Milliseconds 200; "
                f"}}; $p.Close()"
            )
            subprocess.run(["powershell", "-c", ps_cmd], timeout=180, capture_output=True)
        elif sys.platform == "darwin":
            subprocess.run(["afplay", filepath], timeout=180, capture_output=True)
        else:
            for cmd in [["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
                        ["aplay", filepath], ["paplay", filepath]]:
                try:
                    subprocess.run(cmd, timeout=180, capture_output=True)
                    break
                except FileNotFoundError:
                    continue
    except Exception as e:
        print(f"错误：音频播放失败 - {str(e)}", file=sys.stderr)


def text_to_speech(text: str) -> None:
    """语音朗读（异步执行，不阻塞主线程）。支持 edge-tts（微软免费 TTS）和 pyttsx3 回退。"""
    if not text or not text.strip():
        return
    global _tts_thread
    _tts_stop_event.clear()

    def _speak_task():
        try:
            if _TTS_BACKEND == "edge-tts":
                import asyncio, tempfile
                tmp = os.path.join(tempfile.gettempdir(), "openclaw_tts.mp3")
                voice = getattr(_TTS_ENGINE, '_voice', None) or "zh-CN-XiaoxiaoNeural"
                communicate = edge_tts.Communicate(text, voice=voice, rate="+0%")
                asyncio.run(communicate.save(tmp))
                if not _tts_stop_event.is_set():
                    _play_audio_file(tmp)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            elif _TTS_BACKEND == "pyttsx3" and _TTS_ENGINE:
                _TTS_ENGINE.say(text)
                _TTS_ENGINE.runAndWait()
        except Exception as e:
            print(f"错误：语音朗读失败 - {str(e)}", file=sys.stderr)

    _tts_thread = threading.Thread(target=_speak_task, daemon=True)
    _tts_thread.start()


def stop_speech() -> None:
    """停止当前语音朗读"""
    _tts_stop_event.set()
    if _TTS_BACKEND == "pyttsx3" and _TTS_ENGINE:
        try:
            _TTS_ENGINE.stop()
        except Exception:
            pass

# -------------------------- 文件操作工具集 --------------------------
class ToolSet:
    @staticmethod
    def safe_path(user_path: str) -> str:
        """
        路径安全校验：限制操作范围在 WORKSPACE_DIR 内，防止越权访问
        :param user_path: 用户传入的路径
        :return: 安全的绝对路径
        :raise ValueError: 路径越权时抛出异常
        """
        if not isinstance(user_path, str):
            raise ValueError("路径必须为字符串类型")
        
        # 标准化路径（处理斜杠/反斜杠、重复斜杠等）
        user_path = user_path.strip()
        norm_path = os.path.normpath(user_path.replace("\\", "/"))
        
        # 转换为绝对路径
        if os.path.isabs(norm_path):
            abs_path = norm_path
        else:
            abs_path = os.path.normpath(os.path.join(WORKSPACE_DIR, norm_path))
        
        # 强制转为小写，避免盘符大小写问题（Windows）
        abs_path_lower = abs_path.lower()
        workspace_lower = WORKSPACE_DIR.lower()
        
        # 校验是否在工作空间内
        if not abs_path_lower.startswith(workspace_lower):
            raise ValueError(f"无权访问路径：{abs_path}（仅允许访问 {WORKSPACE_DIR} 内的文件）")
        
        return abs_path

    @staticmethod
    def read_file(filepath: str, max_size: int = MAX_FILE_SIZE) -> str:
        """
        安全读取文件内容
        :param filepath: 文件路径
        :param max_size: 最大文件大小限制（字节）
        :return: 文件内容或错误信息
        """
        try:
            # 路径安全校验
            safe_path = ToolSet.safe_path(filepath)
            
            # 校验文件是否存在
            if not os.path.isfile(safe_path):
                return f"错误：文件不存在 - {safe_path}"
            
            # 校验文件大小
            file_size = os.path.getsize(safe_path)
            if file_size > max_size:
                return f"错误：文件过大（{file_size} 字节），最大允许 {max_size} 字节"
            
            # 读取文件（UTF-8 编码，无法解码的字符替换）
            with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            
            return content
        except ValueError as e:
            return f"错误：{str(e)}"
        except Exception as e:
            return f"读取文件失败：{str(e)}"

    @staticmethod
    def write_file(filepath: str, content: str, max_size: int = MAX_CONTENT_SIZE) -> str:
        """
        安全写入文件内容
        :param filepath: 文件路径
        :param content: 要写入的内容
        :param max_size: 最大内容大小限制（字节，按 UTF-8 编码计算）
        :return: 成功信息或错误信息
        """
        # 校验内容大小
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > max_size:
            return f"错误：内容过大（{len(content_bytes)} 字节），最大允许 {max_size} 字节"
        
        try:
            # 路径安全校验
            safe_path = ToolSet.safe_path(filepath)
            
            # 确保目录存在
            os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            
            # 写入文件（UTF-8 编码）
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            return f"成功写入：{safe_path}"
        except ValueError as e:
            return f"错误：{str(e)}"
        except Exception as e:
            return f"写入文件失败：{str(e)}"

    @staticmethod
    def list_directory(dirpath: str = "", page: int = 1, page_size: int = 50) -> str:
        """
        列出目录内容（分页）
        :param dirpath: 目录路径（空则使用 WORKSPACE_DIR）
        :param page: 页码（从 1 开始）
        :param page_size: 每页条目数
        :return: 目录列表信息或错误信息
        """
        try:
            # 处理页码/页大小的合法值
            page = max(1, page)
            page_size = max(1, min(page_size, 100))  # 限制最大页大小为 100
            
            # 确定目标目录
            if dirpath:
                safe_path = ToolSet.safe_path(dirpath)
                target_dir = safe_path if os.path.isdir(safe_path) else os.path.dirname(safe_path)
            else:
                target_dir = WORKSPACE_DIR
            
            # 列出目录条目
            entries = os.listdir(target_dir)
            total = len(entries)
            
            # 计算分页范围
            start = (page - 1) * page_size
            end = start + page_size
            page_entries = entries[start:end]
            
            # 构建返回结果
            result = [
                f"目录：{target_dir}",
                f"共 {total} 个项目，显示第 {start+1}-{min(end, total)} 项",
                "------------------------"
            ]
            
            for name in page_entries:
                full_path = os.path.join(target_dir, name)
                # 区分文件/目录图标
                if os.path.isdir(full_path):
                    entry_type = "📁"
                    size_info = ""
                else:
                    entry_type = "📄"
                    size_info = f" ({os.path.getsize(full_path)} 字节)"
                
                result.append(f"{entry_type} {name}{size_info}")
            
            return "\n".join(result)
        except ValueError as e:
            return f"错误：{str(e)}"
        except Exception as e:
            return f"列出目录失败：{str(e)}"

# -------------------------- 工具调用配置 --------------------------
TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件的内容（仅允许读取D盘内的文件）",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件的路径（相对D盘根目录或绝对路径）"}
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "向指定文件写入内容（仅允许写入D盘内的文件）",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件的路径（相对D盘根目录或绝对路径）"},
                    "content": {"type": "string", "description": "要写入的文本内容"}
                },
                "required": ["filepath", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "列出指定目录的内容（分页，仅允许访问D盘内的目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "dirpath": {"type": "string", "description": "目录路径（空则列出D盘根目录）"},
                    "page": {"type": "integer", "description": "页码（默认1）"},
                    "page_size": {"type": "integer", "description": "每页条目数（默认50）"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索，获取最新信息、新闻、知识、代码示例等",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            }
        }
    }
]


def _web_search_impl(query: str) -> str:
    """轻量级联网搜索实现（通过 OpenClaw 网关或直接请求）"""
    # 方案1：尝试从 DS.tools 导入
    try:
        from DS.tools import web_search
        return web_search(query, timeout=30.0)
    except Exception:
        pass
    # 方案2：通过网关代理搜索
    try:
        import httpx as _httpx
        with _httpx.Client(timeout=30.0) as c:
            r = c.post(
                "http://127.0.0.1:19999/v1/chat/completions",
                json={
                    "model": "deepseek:v4-flash-paid",
                    "messages": [{"role": "user", "content": f"搜索并总结：{query}"}],
                    "stream": False
                },
                headers={"Authorization": "Bearer claw", "Content-Type": "application/json"}
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "搜索无结果")
    except Exception:
        pass
    return f"搜索不可用（DS.tools 和网关均不可达），查询：{query}"


def execute_tool(name: str, args: Dict[str, Any]) -> str:
    """
    执行指定的工具函数
    :param name: 工具名称
    :param args: 工具参数
    :return: 工具执行结果
    """
    tool_mapping = {
        "read_file": ToolSet.read_file,
        "write_file": ToolSet.write_file,
        "list_directory": ToolSet.list_directory,
        "web_search": lambda query="": _web_search_impl(query)
    }
    
    if name not in tool_mapping:
        return f"错误：未知工具 - {name}"
    
    try:
        return tool_mapping[name](**args)
    except TypeError as e:
        return f"错误：工具参数错误 - {str(e)}"
    except Exception as e:
        return f"工具执行失败：{str(e)}"

# -------------------------- Hermes 记忆管理 --------------------------
class HermesMemory:
    """记忆管理类：记录工具执行历史、错误、成功率等"""
    def __init__(self, path: str = MEMORY_FILE):
        self.path = path
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        """加载记忆文件（JSON格式），加载失败返回空结构"""
        if not os.path.exists(self.path):
            return self._get_default_data()
        
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 校验数据结构完整性
            for key in ["dirs", "errors", "success", "tools", "habits"]:
                if key not in data:
                    data[key] = self._get_default_data()[key]
            return data
        except Exception as e:
            print(f"警告：加载记忆文件失败 - {str(e)}，使用默认记忆结构", file=sys.stderr)
            return self._get_default_data()

    def _get_default_data(self) -> Dict[str, Any]:
        """获取默认的记忆数据结构"""
        return {
            "dirs": {},          # 访问过的目录记录
            "errors": [],        # 错误记录
            "success": [],       # 成功记录
            "tools": {},         # 工具成功率统计
            "habits": [],        # 用户习惯记录
            "project_structure": {},  # 项目结构缓存
            "frequent_paths": [],     # 常用目录
            "error_patterns": {}      # 错误模式统计
        }

    def save(self) -> None:
        """保存记忆数据到文件（容错处理）"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            # 写入JSON（格式化，支持中文）
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"错误：保存记忆文件失败 - {str(e)}", file=sys.stderr)

    def record_dir(self, path: str) -> None:
        """记录访问过的目录"""
        try:
            safe_path = ToolSet.safe_path(path)
            self.data["dirs"][safe_path] = time.time()
            self.save()
        except Exception as e:
            print(f"错误：记录目录失败 - {str(e)}", file=sys.stderr)

    def record_error(self, tool: str, args: Dict[str, Any], msg: str) -> None:
        """记录工具执行错误"""
        error_record = {
            "tool": tool,
            "args": args,
            "msg": msg,
            "time": time.time()
        }
        self.data["errors"].append(error_record)
        # 限制错误记录数量，避免文件过大
        if len(self.data["errors"]) > MEMORY_MAX_ERRORS:
            self.data["errors"] = self.data["errors"][-MEMORY_MAX_ERRORS:]
        # 更新工具统计（失败）
        if tool not in self.data["tools"]:
            self.data["tools"][tool] = {"total": 0, "success": 0}
        self.data["tools"][tool]["total"] += 1
        self.save()

    def record_success(self, tool: str, args: Dict[str, Any]) -> None:
        """记录工具执行成功"""
        success_record = {
            "tool": tool,
            "args": args,
            "time": time.time()
        }
        self.data["success"].append(success_record)
        # 更新工具统计（成功）
        if tool not in self.data["tools"]:
            self.data["tools"][tool] = {"total": 0, "success": 0}
        self.data["tools"][tool]["total"] += 1
        self.data["tools"][tool]["success"] += 1
        self.save()

    def get_success_rate(self, tool: str) -> float:
        """获取工具执行成功率"""
        tool_stats = self.data["tools"].get(tool, {"total": 0, "success": 0})
        total = tool_stats.get("total", 0)
        success = tool_stats.get("success", 0)
        return success / max(total, 1)  # 避免除以0

    def record_path(self, path: str) -> None:
        """记录常用路径（去重，最多保留20条）"""
        if path not in self.data.get("frequent_paths", []):
            self.data.setdefault("frequent_paths", []).append(path)
            if len(self.data["frequent_paths"]) > 20:
                self.data["frequent_paths"] = self.data["frequent_paths"][-20:]
            self.save()

    def record_error_pattern(self, error_type: str) -> None:
        """记录错误模式统计"""
        patterns = self.data.setdefault("error_patterns", {})
        patterns[error_type] = patterns.get(error_type, 0) + 1
        self.save()

    def get_habits_context(self) -> str:
        """获取用户习惯上下文（供 LLM 参考）"""
        parts = []
        fp = self.data.get("frequent_paths", [])
        if fp:
            parts.append(f"常用目录：{', '.join(fp[-5:])}")
        patterns = self.data.get("error_patterns", {})
        if patterns:
            top_errors = sorted(patterns.items(), key=lambda x: -x[1])[:3]
            parts.append(f"常见错误：{', '.join(f'{k}({v}次)' for k,v in top_errors)}")
        return "；".join(parts) if parts else ""

# -------------------------- CCR 智能体核心类 --------------------------
class CCRAgent:
    """CCR 闭环智能体：集成工具调用、记忆、反思修复、策略进化"""
    def __init__(self, model_id: str, api_base: str = "http://127.0.0.1:19999/v1", api_key: str = "claw"):
        self.model_id = model_id
        # 初始化 OpenAI 客户端
        self.client = OpenAI(
            base_url=api_base,
            api_key=api_key,
            timeout=API_TIMEOUT,
            max_retries=API_MAX_RETRIES
        )
        self.tools = TOOLS
        self.max_steps = MAX_STEPS
        self.memory = HermesMemory()

    def plan_task(self, prompt: str) -> List[Dict[str, Any]]:
        """
        任务规划：自动拆分任务为步骤列表（步骤号、目标、工具）
        支持多步骤任务识别、文件路径提取、操作类型判断
        """
        steps = []
        prompt_lower = prompt.lower()

        # 提取文件路径（D:\... 或 ./... 格式）
        import re
        paths = re.findall(r'[Dd]:[/\\][^\s"\'<>|*?]+', prompt)
        if not paths:
            paths = re.findall(r'\./[^\s"\'<>|*?]+', prompt)

        # 关键词到工具的映射（按优先级排序）
        action_map = [
            (["搜索", "search", "查找资料", "查一下"], "web_search", {"query": prompt}),
            (["创建", "新建", "写入", "write", "生成文件", "保存到"], "write_file", {}),
            (["读取", "读文件", "查看内容", "read", "cat "], "read_file", {}),
            (["列出", "目录", "list", "ls ", "查看目录", "文件列表"], "list_directory", {}),
            (["修改", "编辑", "update", "替换", "改"], "write_file", {}),
        ]

        matched_tools = set()
        for keywords, tool, default_args in action_map:
            if any(kw in prompt_lower for kw in keywords):
                if tool not in matched_tools:
                    matched_tools.add(tool)
                    args = dict(default_args)
                    if tool in ("read_file", "write_file") and paths:
                        args["filepath"] = paths[0]
                    elif tool == "web_search":
                        args["query"] = prompt[:100]
                    steps.append({
                        "step": len(steps) + 1,
                        "action": f"执行 {tool}",
                        "tool": tool,
                        "args": args
                    })

        # 如果没有匹配，默认读取工作目录
        if not steps:
            target = paths[0] if paths else "D:/"
            steps.append({
                "step": 1,
                "action": "分析并读取文件",
                "tool": "read_file",
                "args": {"filepath": target}
            })

        return steps

    def reflect_and_repair(self, result: str, tool: str, args: Dict[str, Any]) -> str:
        """
        反思与修复：工具执行失败时，分析原因（路径/参数/格式），自动修正重试，最多3次
        """
        error_keywords = ["错误", "失败", "Error", "error", "exception", "异常", "不存在", "权限", "超时"]

        for attempt in range(3):
            # 执行成功则直接返回
            if not any(kw in result for kw in error_keywords):
                return result

            # 分析错误类型并修复
            if "不存在" in result or "not found" in result.lower():
                # 路径错误：尝试修复路径格式
                if "filepath" in args:
                    fp = args["filepath"]
                    fp = fp.replace("//", "/").replace("\\\\", "\\")
                    fp = fp.strip()
                    # 如果是相对路径，尝试加上工作目录前缀
                    if not os.path.isabs(fp):
                        fp = os.path.normpath(os.path.join(WORKSPACE_DIR, fp))
                    args["filepath"] = fp
                    self.memory.record_error_pattern("path_not_found")

            elif "过大" in result or "too large" in result.lower():
                # 文件过大：截断内容
                if "content" in args and len(args["content"]) > MAX_CONTENT_SIZE:
                    args["content"] = args["content"][:MAX_CONTENT_SIZE]
                if "max_size" not in args:
                    args["max_size"] = 2 * MAX_FILE_SIZE
                self.memory.record_error_pattern("file_too_large")

            elif "权限" in result or "permission" in result.lower():
                # 权限错误：无法自动修复
                self.memory.record_error_pattern("permission_denied")
                return result

            elif "编码" in result or "encoding" in result.lower():
                # 编码错误：尝试指定编码
                args["encoding"] = "utf-8"
                self.memory.record_error_pattern("encoding_error")

            elif "超时" in result or "timeout" in result.lower():
                # 超时：不重试
                self.memory.record_error_pattern("timeout")
                return result

            else:
                # 未知错误：记录并返回
                self.memory.record_error_pattern("unknown")

            # 重试工具执行
            result = execute_tool(tool, args)

        return result

    def evolve_strategy(self) -> str:
        """
        策略进化：统计工具成功率，自动优化调用逻辑
        :return: 调试信息（成功率最高的工具 + 进化建议）
        """
        best_tool = "read_file"
        best_rate = 0.0
        stats = {}

        for tool in ["read_file", "write_file", "list_directory", "web_search"]:
            rate = self.memory.get_success_rate(tool)
            stats[tool] = f"{rate:.0%}"
            if rate > best_rate:
                best_rate = rate
                best_tool = tool

        # 获取错误模式分析
        habits = self.memory.get_habits_context()
        return f"最优工具：{best_tool}({best_rate:.0%}) | 统计：{stats}" + (f" | {habits}" if habits else "")

    @staticmethod
    def _is_incomplete_code(text: str) -> bool:
        """检测代码是否未完成（缺函数尾、缺括号、缺缩进）"""
        if not text:
            return False
        lines = text.rstrip().split("\n")
        if not lines:
            return False
        last_line = lines[-1].rstrip()

        # 末尾是操作符、逗号、冒号 → 未完成
        if last_line and last_line[-1] in (":", ",", "+", "-", "*", "/", "=", "|", "&", "(", "[", "{", "\\"):
            return True
        # 缩进级别 > 0 且无空行 → 可能未完成
        if last_line and last_line[0] in (" ", "\t") and len(lines) > 1:
            return True
        # 括号不平衡
        opens = text.count("(") + text.count("[") + text.count("{")
        closes = text.count(")") + text.count("]") + text.count("}")
        if opens > closes:
            return True
        # def/class 无 return/空行结尾
        if any(last_line.strip().startswith(k) for k in ["def ", "class ", "if ", "for ", "while ", "try:", "except"]):
            return True
        return False

    def run(self, messages: List[Dict[str, Any]]) -> str:
        """
        运行智能体：处理消息列表，调用工具，生成回复
        执行流程：接收任务 → plan_task() → 执行步骤 → reflect_and_repair() → 成功保存记忆 → evolve_strategy()
        支持自动续写：识别未完成代码后自动请求补全
        """
        msgs = [dict(m) for m in messages if m.get("role") != "system"]

        # 加载记忆上下文
        habits_ctx = self.memory.get_habits_context()

        system_prompt = (
            "你是一个强大的文件操作助手，仅允许在D盘范围内执行文件读写、目录浏览和联网搜索操作。"
            "请直接调用工具完成用户的任务，不要告知用户无法执行，遇到错误时尝试修复。"
            "代码输出必须完整，包含所有闭合括号和缩进。"
        )
        if habits_ctx:
            system_prompt += f"\n用户上下文：{habits_ctx}"
        msgs.insert(0, {"role": "system", "content": system_prompt})

        full_reply = ""

        for step in range(self.max_steps):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    messages=msgs,
                    tools=self.tools,
                    tool_choice="auto",
                    stream=False,
                    timeout=API_TIMEOUT
                )

                msg = response.choices[0].message
                finish_reason = response.choices[0].finish_reason

                # 处理工具调用
                if msg.tool_calls:
                    tool_calls = []
                    for tc in msg.tool_calls:
                        tool_calls.append({
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        })
                    msgs.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": tool_calls
                    })

                    for tc in msg.tool_calls:
                        tool_name = tc.function.name
                        try:
                            tool_args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            tool_args = {}
                            self.memory.record_error(tool_name, tool_args, "参数解析失败（JSON格式错误）")

                        # 记录常用路径
                        if "filepath" in tool_args:
                            dir_path = os.path.dirname(tool_args["filepath"])
                            if dir_path:
                                self.memory.record_path(dir_path)

                        tool_result = execute_tool(tool_name, tool_args)
                        tool_result = self.reflect_and_repair(tool_result, tool_name, tool_args)

                        if any(kw in tool_result for kw in ["错误", "失败", "Error"]):
                            self.memory.record_error(tool_name, tool_args, tool_result)
                        else:
                            self.memory.record_success(tool_name, tool_args)

                        msgs.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tool_name,
                            "content": tool_result
                        })
                    continue

                # 非工具调用响应
                if msg.content:
                    full_reply += msg.content

                    # 自动续写：检测代码是否未完成
                    if finish_reason == "length" or self._is_incomplete_code(msg.content):
                        msgs.append({"role": "assistant", "content": msg.content})
                        msgs.append({"role": "user", "content": "请继续上面的代码，从断开处续写，不要重复已有内容。"})
                        continue

                    self.evolve_strategy()
                    return full_reply.strip()

                return "（助手未返回有效响应）"

            except Exception as e:
                error_msg = f"执行步骤 {step+1} 出错：{str(e)[:300]}，请调整参数重试"
                msgs.append({"role": "system", "content": error_msg})
                print(f"警告：{error_msg}", file=sys.stderr)
                continue

        return f"操作超时（已尝试 {self.max_steps} 步），请稍后重试。"

# -------------------------- 对外接口函数 --------------------------
def ccr_chat(
    model_id: str,
    msg_list: List[Dict[str, Any]],
    placeholder,
    file_contexts: Optional[List[Dict[str, str]]] = None
) -> Tuple[bool, str, str]:
    """
    CCR 智能体聊天接口
    :param model_id: 模型ID
    :param msg_list: 消息列表
    :param placeholder: 进度占位符（用于显示思考中状态）
    :param file_contexts: 上传的文件上下文列表
    :return: (是否成功, 回复内容, 错误信息)
    """
    try:
        # 复制消息列表避免修改原数据
        final_messages = [dict(m) for m in msg_list]
        
        # 追加上传文件内容到用户最后一条消息
        if file_contexts and isinstance(file_contexts, list):
            file_text = ["已上传文件内容："]
            for idx, file_info in enumerate(file_contexts, 1):
                file_name = file_info.get("name", f"未知文件{idx}")
                file_content = file_info.get("text", "")[:10000]  # 限制文件内容长度
                file_text.append(f"\n[文件{idx}] {file_name}:\n{file_content}...")
            
            # 找到最后一条用户消息并追加内容
            for msg in reversed(final_messages):
                if msg.get("role") == "user":
                    msg["content"] = "\n\n".join(file_text) + "\n\n" + msg.get("content", "")
                    break
        
        # 显示思考状态
        if hasattr(placeholder, "markdown"):
            placeholder.markdown("CCR Hermes 智能体思考中...")
        
        # 初始化智能体并执行
        agent = CCRAgent(model_id)
        reply = agent.run(final_messages)
        
        if reply:
            return True, reply, ""
        return False, "（CCR 代理未能生成回复）", ""
    
    except Exception as e:
        error_msg = f"CCR 代理执行错误：{str(e)[:200]}"
        print(f"错误：{error_msg}", file=sys.stderr)
        return False, error_msg, str(e)

# -------------------------- CCR HTTP 代理服务（Claude Code → CCR → OpenClaw） --------------------------
# 加载路由配置
_CCR_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config-router_bak.json")

def _load_ccr_config() -> Dict[str, Any]:
    """加载 CCR 路由配置"""
    default_cfg = {
        "server": {"port": 29999, "host": "127.0.0.1"},
        "routing": {
            "defaultProvider": "openclaw",
            "providers": {
                "openclaw": {
                    "type": "openai",
                    "endpoint": "http://127.0.0.1:19999/v1"
                }
            }
        }
    }
    if os.path.exists(_CCR_CONFIG_PATH):
        try:
            with open(_CCR_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default_cfg


def _get_gateway_endpoint() -> str:
    """从配置中获取 OpenClaw 网关地址"""
    cfg = _load_ccr_config()
    return cfg.get("routing", {}).get("providers", {}).get("openclaw", {}).get("endpoint", "http://127.0.0.1:19999/v1")


def _get_ccr_server_config() -> Tuple[str, int]:
    """获取 CCR 服务监听地址和端口"""
    cfg = _load_ccr_config()
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    port = cfg.get("server", {}).get("port", 29999)
    return host, port


def start_ccr_server() -> None:
    """启动 CCR HTTP 代理服务（FastAPI），作为 Claude Code 与 OpenClaw 网关之间的中转"""
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, StreamingResponse
        from fastapi.middleware.cors import CORSMiddleware
        import uvicorn
    except ImportError:
        print("错误：CCR 服务需要 fastapi 和 uvicorn，请执行 pip install fastapi uvicorn", file=sys.stderr)
        return

    ccr_app = FastAPI(title="CCR Proxy", version="1.0")
    ccr_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    gateway_url = _get_gateway_endpoint()
    host, port = _get_ccr_server_config()

    @ccr_app.get("/")
    async def ccr_root():
        return {"service": "CCR Proxy", "gateway": gateway_url, "status": "running"}

    @ccr_app.get("/health")
    async def ccr_health():
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{gateway_url.replace('/v1', '')}/health")
                return {"status": "healthy", "gateway": gateway_url, "gateway_status": r.status_code}
        except Exception:
            return {"status": "degraded", "gateway": gateway_url}

    @ccr_app.get("/v1/models")
    async def ccr_list_models():
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{gateway_url}/models")
                return JSONResponse(content=r.json())
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": {"message": f"CCR proxy error: {str(e)}"}})

    @ccr_app.post("/v1/chat/completions")
    async def ccr_chat_completions(request: Request):
        """Claude Code 发送请求 → CCR 转发 → OpenClaw 网关 → 模型池轮询 → 返回结果"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": {"message": "invalid json"}})

        # 获取请求头（传递 Authorization）
        headers = {"Content-Type": "application/json"}
        auth = request.headers.get("Authorization", "")
        if auth:
            headers["Authorization"] = auth

        # 转发到 OpenClaw 网关
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                stream = body.get("stream", False)
                resp = await c.post(
                    f"{gateway_url}/chat/completions",
                    json=body,
                    headers=headers
                )

                if stream:
                    return StreamingResponse(resp.aiter_bytes(), media_type="text/event-stream")

                return JSONResponse(content=resp.json())

        except httpx.TimeoutException:
            return JSONResponse(status_code=504, content={"error": {"message": "CCR proxy timeout: gateway timeout"}})
        except httpx.ConnectError:
            return JSONResponse(status_code=502, content={"error": {"message": "CCR proxy error: gateway unreachable"}})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": {"message": f"CCR proxy error: {str(e)[:200]}"}})

    print(f"CCR Proxy 启动: http://{host}:{port}")
    print(f"转发目标: {gateway_url}")
    uvicorn.run(ccr_app, host=host, port=port, log_level="info")


# -------------------------- 测试代码（可选） --------------------------
if __name__ == "__main__":
    import sys as _sys
    # 启动 CCR HTTP 代理服务
    start_ccr_server()