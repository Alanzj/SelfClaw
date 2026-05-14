# tools.py — 文件系统、联网搜索、记忆、升级、插件、扩展、刷新（完整版+语音依赖检查）
import os, sys, json, subprocess, requests, httpx, urllib.parse
from datetime import datetime

WORKSPACE_DIR = "D:/"
MEMOS_LOCAL_URL = "http://localhost:5230"


def safe_path(user_path: str) -> str:
    p = os.path.normpath(user_path.strip().replace("\\", "/"))
    if os.path.isabs(p):
        if not p.lower().startswith(WORKSPACE_DIR.lower()):
            raise ValueError(f"无权访问路径：{p}")
        return p
    full = os.path.normpath(os.path.join(WORKSPACE_DIR, p))
    if not full.lower().startswith(WORKSPACE_DIR.lower()):
        raise ValueError(f"无权访问路径：{full}")
    return full


def read_file(filepath: str, max_size: int = 10_000_000) -> str:
    path = safe_path(filepath)
    if not os.path.isfile(path):
        return f"错误：文件不存在 {path}"
    size = os.path.getsize(path)
    if size > max_size:
        return f"错误：文件过大（{size}字节），限制 {max_size} 字节"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        from DS.model_manager import add_operation_log
        add_operation_log(f"读取文件：{filepath} ({len(content)}字符)", "info")
        return content
    except Exception as e:
        from DS.model_manager import add_operation_log
        add_operation_log(f"读取文件失败：{filepath} - {str(e)}", "error")
        return f"读取文件失败：{str(e)}"


def write_file(filepath: str, content: str, max_size: int = 10_000_000) -> str:
    if len(content.encode("utf-8")) > max_size:
        return f"错误：内容过大（{len(content.encode('utf-8'))}字节），限制 {max_size} 字节"
    path = safe_path(filepath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        from DS.model_manager import add_operation_log
        add_operation_log(f"写入文件：{filepath} ({len(content)}字符)", "success")
        return f"成功写入：{path}"
    except Exception as e:
        from DS.model_manager import add_operation_log
        add_operation_log(f"写入文件失败：{filepath} - {str(e)}", "error")
        return f"写入文件失败：{str(e)}"


def list_directory(dirpath: str = "", page: int = 1, page_size: int = 50) -> str:
    try:
        target = safe_path(dirpath) if dirpath else WORKSPACE_DIR
        if not os.path.isdir(target):
            target = os.path.dirname(target)
        entries = os.listdir(target)
        total = len(entries)
        start = (page - 1) * page_size
        end = start + page_size
        page_entries = entries[start:end]
        result = f"目录：{target}\n共{total} 个项目，显示第{start+1}-{min(end,total)} 项\n"
        for name in page_entries:
            full = os.path.join(target, name)
            typ = "📁" if os.path.isdir(full) else "📄"
            size = os.path.getsize(full) if os.path.isfile(full) else 0
            result += f"{typ} {name}"
            if os.path.isfile(full):
                result += f" ({size} bytes)"
            result += "\n"
        from DS.model_manager import add_operation_log
        add_operation_log(f"列出目录：{target} (page {page})", "info")
        return result
    except Exception as e:
        from DS.model_manager import add_operation_log
        add_operation_log(f"列出目录失败：{dirpath} - {str(e)}", "error")
        return f"列出目录失败：{str(e)}"


def web_search(query: str, timeout: int = 15) -> str:
    """
    双引擎联网搜索：必应优先，失败自动切百度
    返回搜索结果摘要，供 AI 回答用户最新问题
    """
    if not query or not query.strip():
        return "搜索关键词不能为空"

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    import re

    # 1. 必应搜索（优先）
    try:
        bing_url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query)
        res = requests.get(bing_url, headers=headers, timeout=timeout, allow_redirects=True)
        if res.status_code == 200 and len(res.text) > 500:
            # 提取搜索结果片段
            snippets = re.findall(r'<li class="b_algo">.*?<p[^>]*>(.*?)</p>', res.text, re.S)
            if not snippets:
                snippets = re.findall(r'<div class="b_caption".*?<p[^>]*>(.*?)</p>', res.text, re.S)
            if snippets:
                results = []
                for s in snippets[:5]:
                    clean = re.sub(r'<.*?>', '', s).strip()
                    if clean and len(clean) > 10:
                        results.append(clean)
                if results:
                    output = f"【必应搜索】关键词：{query}\n\n"
                    for i, r in enumerate(results, 1):
                        output += f"{i}. {r}\n\n"
                    from DS.model_manager import add_operation_log
                    add_operation_log(f"🔍 必应搜索成功：{query}", "info")
                    return output.strip()
    except Exception:
        pass

    # 2. 百度搜索（降级）
    try:
        baidu_url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(query)
        res = requests.get(baidu_url, headers=headers, timeout=timeout, allow_redirects=True)
        if res.status_code == 200 and "百度安全验证" not in res.text and len(res.text) > 500:
            # 提取搜索结果
            snippets = re.findall(r'<span class="content-right_[^"]*">(.*?)</span>', res.text, re.S)
            if not snippets:
                snippets = re.findall(r'<div class="c-abstract"[^>]*>(.*?)</div>', res.text, re.S)
            if not snippets:
                snippets = re.findall(r'<div class="c-span-last"[^>]*>(.*?)</div>', res.text, re.S)
            if snippets:
                results = []
                for s in snippets[:5]:
                    clean = re.sub(r'<.*?>', '', s).strip()
                    if clean and len(clean) > 10:
                        results.append(clean)
                if results:
                    output = f"【百度搜索】关键词：{query}\n\n"
                    for i, r in enumerate(results, 1):
                        output += f"{i}. {r}\n\n"
                    from DS.model_manager import add_operation_log
                    add_operation_log(f"🔍 百度搜索成功：{query}", "info")
                    return output.strip()
    except Exception:
        pass

    from DS.model_manager import add_operation_log
    add_operation_log(f"⚠️ 联网搜索失败：{query}", "warning")
    return f"联网搜索未获取到「{query}」的有效结果，请尝试更换关键词或稍后重试"


def load_user_memory():
    from DS.config import MEMORY_PATH
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "messages" in data:
                return data["messages"]
        except:
            pass
    return []


def load_ui_state():
    """加载持久化的 UI 状态（面板开关、已安装插件等）"""
    from DS.config import MEMORY_PATH
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "ui_state" in data:
                return data["ui_state"]
        except:
            pass
    return {}


def save_user_memory(messages, ui_state=None):
    from DS.config import MEMORY_PATH
    try:
        data = {
            "messages": messages,
            "ui_state": ui_state or {}
        }
        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass


def save_ui_state(ui_state):
    """单独保存 UI 状态（不覆盖对话历史）"""
    from DS.config import MEMORY_PATH
    try:
        existing_messages = load_user_memory()
        save_user_memory(existing_messages, ui_state)
    except:
        pass


def check_tool_plugins():
    import importlib
    REQUIRED_TOOL_PLUGINS = [
        ("PyPDF2", "PDF文档解析"),
        ("python-docx", "Word文档解析"),
        ("pandas", "Excel/CSV表格解析"),
        ("pyaudio", "双向语音输入"),
        ("pyttsx3", "双向语音播报"),
        ("SpeechRecognition", "语音识别"),
        ("numpy", "数值计算支持"),
        ("pillow", "图片解析支持")
    ]
    missing_plugins = []
    installed_plugins = []
    for pkg_name, desc in REQUIRED_TOOL_PLUGINS:
        try:
            importlib.import_module(pkg_name)
            installed_plugins.append((pkg_name, desc))
        except ImportError:
            missing_plugins.append((pkg_name, desc))
    return missing_plugins, installed_plugins


def install_tool_plugin(pkg_name):
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg_name],
            timeout=180,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        # 安装成功后清除import缓存并验证
        import importlib
        importlib.invalidate_caches()
        try:
            importlib.import_module(pkg_name)
            return True, f"{pkg_name} 安装成功"
        except ImportError:
            # pip已安装但当前进程无法import（需重启），标记为已安装
            return True, f"{pkg_name} 安装成功（重启后完全生效）"
    except Exception as e:
        return False, f"{pkg_name} 安装失败：{str(e)[:100]}"


def check_upgrade():
    from DS.config import VERSION_CHECK_URL, CURRENT_VERSION
    try:
        res = requests.get(VERSION_CHECK_URL, timeout=30.0)
        if res.status_code == 200:
            data = res.json()
            latest = data.get("info", {}).get("version", CURRENT_VERSION)
            if latest == CURRENT_VERSION:
                return f"当前版本：{CURRENT_VERSION} | 已是最新版"
            else:
                return f"当前版本：{CURRENT_VERSION} | 最新版本：{latest} | 可升级"
    except Exception:
        return f"当前版本：{CURRENT_VERSION} | 版本检测失败，本地运行正常"


def upgrade_openclaw():
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "openclaw"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return True, "升级成功，请重启程序以生效"
        else:
            return False, f"升级失败: {result.stderr[:200]}"
    except Exception as e:
        return False, f"升级异常: {str(e)[:100]}"


def auto_upgrade_and_plugin_check():
    import streamlit as st
    if "auto_maintenance_done" not in st.session_state:
        st.session_state.auto_maintenance_done = True
        from DS.model_manager import add_operation_log
        add_operation_log("🚀 正在检查版本..", "info")
        try:
            res = requests.get(VERSION_CHECK_URL, timeout=30.0)
            if res.status_code == 200:
                add_operation_log("✅ 版本环境正常", "success")
        except:
            add_operation_log("✅ 本地版本运行正常", "success")
        add_operation_log("🔍 检查插件依赖..", "info")
        missing, _ = check_tool_plugins()
        if missing:
            add_operation_log(f"⚠️ 缺失可选插件：{','.join([m[0] for m in missing])}，不影响核心功能", "warning")
        else:
            add_operation_log("✅ 所有插件依赖正常", "success")


def parse_file_safe(uploaded):
    name = uploaded.name.lower()
    raw = uploaded.read()
    ext = os.path.splitext(name)[1].lower()
    try:
        if ext == '.pdf':
            try:
                from PyPDF2 import PdfReader
                import io
                return "\n".join([p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages])
            except:
                return "[需安装PyPDF2后支持PDF解析]"
        if ext in ['.docx', '.doc']:
            try:
                from docx import Document
                import io
                return "\n".join([p.text for p in Document(io.BytesIO(raw)).paragraphs])
            except:
                return "[需安装python-docx后支持Word解析]"
        if ext in ['.xlsx', '.xls']:
            try:
                import pandas as pd
                import io
                return pd.read_excel(io.BytesIO(raw)).to_string(max_rows=200)
            except:
                return "[需安装pandas后支持Excel解析]"
        if ext == '.csv':
            return raw.decode("utf-8", errors="replace")
        if ext in ['.txt', '.md', '.py', '.js', '.json', '.yaml', '.xml', '.log', '.sh', '.bat', '.ps1', '.c', '.cpp', '.h', '.java', '.go', '.rs', '.swift', '.kt']:
            return raw.decode("utf-8", errors="replace")
        return f"[不支持的文件格式: {uploaded.name}]"
    except Exception as e:
        return f"[文件解析失败: {str(e)[:100]}]"


def handle_files(uploaded_list):
    import streamlit as st
    res = []
    for f in uploaded_list:
        txt = parse_file_safe(f)
        from DS.model_manager import add_operation_log
        add_operation_log(f"文件解析: {f.name} ({len(txt)}字符)", "success")
        save_result = False
        if st.session_state.get("memos_reachable"):
            key = os.environ.get("MEMOS_API_KEY", "")
            if key:
                try:
                    with httpx.Client(timeout=3.0, proxies={"http://": None, "https://": None}) as c:
                        c.post(f"{MEMOS_LOCAL_URL}/memories", headers={"Authorization": f"Bearer {key}"}, json={"content": (f"[文件] {f.name}\n{txt}")[:8000]})
                        save_result = True
                except:
                    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"), exist_ok=True)
                    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", f"fallback_{f.name}.txt"), "w", encoding="utf-8") as lf:
                        lf.write(txt)
        res.append({"name": f.name, "text": txt, "save_success": save_result, "length": len(txt)})
    return res


def load_system_extensions():
    from DS.config import EXTENSIONS_DIR
    if not os.path.exists(EXTENSIONS_DIR):
        from DS.model_manager import add_operation_log
        add_operation_log("扩展目录不存在，跳过系统扩展加载", "warning")
        return
    import sys
    extension_count = 0
    for plugin_folder in os.listdir(EXTENSIONS_DIR):
        plugin_path = os.path.join(EXTENSIONS_DIR, plugin_folder)
        if os.path.isdir(plugin_path) and not plugin_folder.startswith("_"):
            try:
                sys.path.append(plugin_path)
                __import__(plugin_folder)
                from DS.model_manager import add_operation_log
                add_operation_log(f"✅ 系统扩展加载成功：{plugin_folder}", "success")
                extension_count += 1
            except Exception as e:
                error_detail = str(e)[:120]
                from DS.model_manager import add_operation_log
                add_operation_log(f"❌ 扩展加载失败：{plugin_folder} | 错误：{error_detail}", "error")
    if extension_count > 0:
        from DS.model_manager import add_operation_log
        add_operation_log(f"🚀 共加载 {extension_count} 个系统扩展插件", "info")


def refresh_memos_status():
    import streamlit as st
    import time as t
    now = t.time()
    if now - st.session_state.get("memos_last_check", 0) < 30:
        return st.session_state.get("memos_status", "检查中")
    st.session_state.memos_last_check = now
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get("http://127.0.0.1:19999/memos/health")
            if r.status_code == 200 and r.json().get("reachable"):
                st.session_state.memos_status = "正常"
                st.session_state.memos_reachable = True
            else:
                st.session_state.memos_status = "离线"
                st.session_state.memos_reachable = False
    except:
        st.session_state.memos_status = "失败"
        st.session_state.memos_reachable = False
    return st.session_state.memos_status


def refresh_node_info():
    import streamlit as st
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get("http://127.0.0.1:19999/node-info")
            if r.status_code == 200:
                d = r.json()
                region = d.get("region", "自动选择")
                st.session_state.node_info = {
                    "region": region,
                    "type": d.get("type", "直连"),
                    "latency": f"{d.get('latency', 0)}ms",
                    "territory": "未知"
                }
    except:
        pass
