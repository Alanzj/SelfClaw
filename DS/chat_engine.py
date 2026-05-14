# chat_engine.py — 单次对话、降级轮询（改进版）、错误判断
import json, time
from openai import OpenAI
import httpx
from DS.config import GATEWAY, MAX_FILE_CONTENT_LENGTH, COLOR_TEXT, COLOR_WARM
from DS.model_manager import (
    get_model_info, simplify_model_name, add_to_blacklist,
    get_available_models, add_operation_log
)
from DS.tools import read_file, write_file, list_directory, web_search

tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径的文件内容，返回文本",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径（相对或绝对，限定在工作区D盘内）"}
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将文本写入指定路径的文件，自动创建目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "文件路径"},
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
            "description": "列出目录下的文件和子目录，支持分页",
            "parameters": {
                "type": "object",
                "properties": {
                    "dirpath": {"type": "string", "description": "目录路径，留空默认根目录"},
                    "page": {"type": "integer", "description": "页码，从1开始"},
                    "page_size": {"type": "integer", "description": "每页数量，默认50"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "通用联网搜索，获取最新信息、新闻、知识、数据、天气、事件等，使用百度必应双引擎",
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


def execute_tool(tool_name, arguments):
    if tool_name == "read_file":
        return read_file(arguments.get("filepath", ""))
    elif tool_name == "write_file":
        return write_file(arguments.get("filepath", ""), arguments.get("content", ""))
    elif tool_name == "list_directory":
        return list_directory(
            arguments.get("dirpath", ""),
            arguments.get("page", 1),
            arguments.get("page_size", 50)
        )
    elif tool_name == "web_search":
        return web_search(arguments.get("query", ""), timeout=30.0)
    else:
        return f"未知工具：{tool_name}"


def is_error_response(text):
    if not text or not text.strip():
        return True
    t = text.strip().lower()
    error_prefix = ["error", "failed", "错误", "失败", "401", "403", "404", "500", "502", "503"]
    for p in error_prefix:
        if t.startswith(p):
            return True
    return False


def classify_error(err_msg):
    from DS.config import PERMANENT_ERROR_KEYWORDS
    for kw in PERMANENT_ERROR_KEYWORDS:
        if kw.lower() in str(err_msg).lower():
            return "permanent"
    return "temporary"


def chat(model_id, msg_list, placeholder, file_contexts=None):
    import streamlit as st
    model_info = get_model_info(model_id)
    if not model_info:
        err = f"模型不存在：{model_id}"
        placeholder.markdown(f'<div style="color:{COLOR_WARM};text-align:center;">{err}</div>', unsafe_allow_html=True)
        return False, err, ""

    actual_model = model_info.get("actual", model_info["id"])
    model_label = simplify_model_name(model_id)

    try:
        with httpx.Client(timeout=3.0) as c:
            if c.get("http://127.0.0.1:19999/health").status_code != 200:
                err = "网关未运行"
                placeholder.markdown(f'<div style="color:{COLOR_WARM};text-align:center;">{err}</div>', unsafe_allow_html=True)
                st.session_state.status = "就绪"
                return False, err, ""
    except:
        err = "网关连接失败"
        placeholder.markdown(f'<div style="color:{COLOR_WARM};text-align:center;">{err}</div>', unsafe_allow_html=True)
        st.session_state.status = "就绪"
        return False, err, ""

    final = msg_list.copy()
    if not final or final[0].get("role") != "system":
        final.insert(0, {
            "role": "system",
            "content": "你是一个强大的人工智能助手，可以直接调用工具执行文件操作、目录浏览和联网搜索，无需要求用户手动操作。"
        })

    if file_contexts:
        txt = "已上传文件内容:\n"
        for i, fi in enumerate(file_contexts, 1):
            ft = fi['text']
            if len(ft) > MAX_FILE_CONTENT_LENGTH:
                txt += f"\n[文件{i}] {fi['name']}:\n{ft[:MAX_FILE_CONTENT_LENGTH]}..."
            else:
                txt += f"\n[文件{i}] {fi['name']}:\n{ft}"
        if final and final[-1]["role"] == "user":
            final[-1]["content"] = txt + "\n\n" + final[-1]["content"]

    add_operation_log(f"尝试模型：{model_label}", "info")
    placeholder.markdown(f'<div style="color:{COLOR_TEXT};">尝试：{model_label}</div>', unsafe_allow_html=True)

    client = OpenAI(base_url=GATEWAY, api_key="claw", timeout=120.0, max_retries=0)
    st.session_state.status = "忙碌"
    reply = ""
    tool_results = []

    try:
        resp = client.chat.completions.create(
            model=actual_model,
            messages=final,
            tools=tools,
            tool_choice="auto",
            stream=False,
            timeout=120.0
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                res = execute_tool(tc.function.name, args)
                tool_results.append(res)
            final.append({"role": "assistant", "content": None, "tool_calls": msg.tool_calls})
            for tc, res in zip(msg.tool_calls, tool_results):
                final.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": res
                })
            resp2 = client.chat.completions.create(
                model=actual_model,
                messages=final,
                stream=True,
                timeout=120.0
            )
            reply = ""
            for chunk in resp2:
                if chunk.choices[0].delta.content:
                    reply += chunk.choices[0].delta.content
                    placeholder.markdown(f'<div style="color:{COLOR_TEXT};">{reply}</div>', unsafe_allow_html=True)
            ok = True
        elif msg.content:
            reply = msg.content
            placeholder.markdown(f'<div style="color:{COLOR_TEXT};">{reply}</div>', unsafe_allow_html=True)
            ok = True
        else:
            ok = False
    except Exception as e:
        err = str(e)[:200]
        add_to_blacklist(model_id, classify_error(err))
        placeholder.markdown(f'<div style="color:{COLOR_WARM};">{model_label} 失败：{err}</div>', unsafe_allow_html=True)
        ok = False
    finally:
        st.session_state.status = "就绪"

    if ok and reply and not is_error_response(reply):
        add_operation_log(f"✅ 调用成功：{model_label}", "success")
        st.session_state.current_used_model = model_id
        return True, reply, ""
    return False, reply, ""


def try_fallback_chat(user_msg, file_ctx):
    import streamlit as st
    from DS.config import load_config_force
    now = time.time()

    if st.session_state.cooldown_until > now:
        remaining = int(st.session_state.cooldown_until - now)
        err = f"冷却中：所有模型全线崩溃，{remaining}秒后重试"
        add_operation_log(f"⏳ {err}", "warning")
        return False, err, None

    load_config_force()
    models = get_available_models()
    add_operation_log(f"【调度】可用模型数量：{len(models)}", "info")
    if not models:
        err = "无可用模型，请检查配置或黑名单"
        add_operation_log(f"❌ {err}", "error")
        return False, err, None

    st.session_state.degrade_path = []
    temporary_error_models = []  # 仅收集临时错误模型，供第二轮重试

    # ─── 第一轮：遍历所有可用模型，每个模型只试一次 ───
    for idx, m in enumerate(models):
        mid = m["id"]
        label = simplify_model_name(mid)
        add_operation_log(f"🚀 尝试第 {idx+1}/{len(models)} 个模型：{label}", "info")
        ph = st.empty()
        ok, resp, _ = chat(
            mid,
            st.session_state.messages + [{"role": "user", "content": user_msg}],
            ph,
            file_ctx
        )
        ph.empty()
        if ok and not is_error_response(resp):
            add_operation_log(f"✅ 调用成功: {label}", "success")
            st.session_state.cooldown_until = 0.0
            st.session_state.degrade_path = []
            return True, resp, mid
        # 判断错误类型：chat() 内部已调用 add_to_blacklist
        # 此处额外收集临时错误模型，供第二轮重试
        if mid not in st.session_state.get("permanent_blacklist", {}):
            temporary_error_models.append(m)
        add_operation_log(f"⏭️ 模型失败，切换下一个：{label}", "warning")
        st.session_state.degrade_path.append(label)

    # ─── 第二轮：仅遍历临时错误模型（永久错误已被黑名单跳过） ───
    add_operation_log(f"🔄 第一轮全部失败，第二轮重试 {len(temporary_error_models)} 个临时错误模型...", "info")
    if not temporary_error_models:
        err = "所有模型均为永久故障，无临时模型可重试"
        add_operation_log(f"❌ {err}", "error")
        st.session_state.cooldown_until = now + 180
        st.session_state.degrade_path = []
        return False, "所有模型永久故障，进入 3 分钟冷却", None

    for idx, m in enumerate(temporary_error_models):
        mid = m["id"]
        label = simplify_model_name(mid)
        add_operation_log(f"🔄 第二轮尝试 {idx+1}/{len(temporary_error_models)}：{label}", "info")
        ph = st.empty()
        ok, resp, _ = chat(
            mid,
            st.session_state.messages + [{"role": "user", "content": user_msg}],
            ph,
            file_ctx
        )
        ph.empty()
        if ok and not is_error_response(resp):
            add_operation_log(f"✅ 第二轮调用成功 {label}", "success")
            st.session_state.cooldown_until = 0.0
            st.session_state.degrade_path = []
            return True, resp, mid
        add_operation_log(f"⏭️ 第二轮失败：{label}", "warning")
        st.session_state.degrade_path.append(label)

    # ─── 两轮全部失败 → 全网冷却 180 秒 ───
    add_operation_log("❌ 两轮全部失败 → 冷却 3 分钟（180秒）", "error")
    st.session_state.cooldown_until = now + 180
    st.session_state.degrade_path = []
    return False, "所有模型失败，进入 3 分钟冷却", None
