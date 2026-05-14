# ui.py — OpenClaw AI WebUI 全面修复版（CSS权重强化 + 按钮结构修正 + Base64传参）
import os, html as html_module, time, json, base64
import pyperclip
import streamlit as st
from datetime import datetime
from DS.config import (
    COLOR_TITLE, COLOR_TEXT, COLOR_WARM, GATEWAY, EXTENSIONS_DIR,
    load_config_force
)
from DS.tools import (
    handle_files, list_directory, write_file,
    check_upgrade, upgrade_openclaw, check_tool_plugins, install_tool_plugin,
    save_user_memory, save_ui_state
)
from DS.model_manager import (
    add_operation_log, get_models_pool, get_auto_models, get_current_use_model_info,
    is_current_in_top3, get_territory_by_provider, simplify_model_name,
    add_model_to_config, delete_model_from_config,
    update_model_in_config, reorder_models_in_config
)

try:
    from CCR.ccr import speech_to_text, text_to_speech, stop_speech
    VOICE_AVAILABLE = True
except Exception:
    VOICE_AVAILABLE = False


def _detect_code_language(code_text):
    """自动检测代码语言"""
    t = code_text.strip().lower()
    if any(k in t for k in ['import ', 'def ', 'class ', 'print(', '__init__']):
        return 'Python'
    if any(k in t for k in ['function ', 'const ', 'let ', 'var ', 'console.log', '=>']):
        return 'JavaScript'
    if any(k in t for k in ['#!/bin/bash', '#!/bin/sh', 'echo ', 'sudo ', 'apt ', 'pip ']):
        return 'Bash'
    if any(k in t for k in ['<html', '<div', '<span', 'class="', "class='"]):
        return 'HTML'
    if any(k in t for k in ['{', '}', '":', 'json']):
        return 'JSON'
    if any(k in t for k in ['@echo', 'pause', 'netstat', 'taskkill']):
        return 'Batch'
    return 'Text'


def render_message_content(text):
    """将消息内容分离为文本块和代码块，各自独立容器渲染"""
    if not text:
        return ''

    import re
    import html as html_mod

    # 先处理 markdown 代码块 ```lang\n...\n```
    parts = re.split(r'```(\w*)\n(.*?)```', text, flags=re.DOTALL)

    result_html = ''
    i = 0
    while i < len(parts):
        if i % 3 == 0:
            # 普通文本段
            segment = parts[i].strip()
            if segment:
                escaped = html_mod.escape(segment)
                # 简单 markdown：**粗体**、*斜体*、`行内代码`
                escaped = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', escaped)
                escaped = re.sub(r'\*(.*?)\*', r'<em>\1</em>', escaped)
                escaped = re.sub(r'`([^`]+)`', r'<code style="background:rgba(78,201,192,0.1);padding:1px 4px;border-radius:3px;font-size:13px;">\1</code>', escaped)
                # 换行处理
                escaped = escaped.replace('\n', '<br/>')
                result_html += f'<div class="oc-text-block">{escaped}</div>'
        elif i % 3 == 2:
            # 代码段
            lang = parts[i - 1] if i > 1 else ''
            code = parts[i]
            if code.strip():
                if not lang:
                    lang = _detect_code_language(code)
                escaped_code = html_mod.escape(code.rstrip())
                import uuid
                code_id = 'code_' + uuid.uuid4().hex[:8]
                result_html += f'''<div class="oc-code-block">
                    <div class="oc-code-header">
                        <span>{lang}</span>
                        <button class="oc-code-copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('{code_id}').textContent).then(()=>this.textContent='✅ 已复制')">📋 复制</button>
                    </div>
                    <div class="oc-code-content"><pre><code id="{code_id}">{escaped_code}</code></pre></div>
                </div>'''
        i += 1

    if not result_html:
        escaped = html_mod.escape(text)
        escaped = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', escaped)
        escaped = re.sub(r'\*(.*?)\*', r'<em>\1</em>', escaped)
        escaped = re.sub(r'`([^`]+)`', r'<code style="background:rgba(78,201,192,0.1);padding:1px 4px;border-radius:3px;font-size:13px;">\1</code>', escaped)
        escaped = escaped.replace('\n', '<br/>')
        result_html = f'<div class="oc-text-block">{escaped}</div>'

    return result_html


def get_theme_css():
    theme = st.session_state.theme
    TITLE = "#4EC9C0"
    TEXT = "#5A9E99"
    WARM = "#D4B86A"
    if theme == "vscode-gray":
        BG = "#1e1e1e"; B2 = "#252526"; CARD = "rgba(45,45,45,0.95)"
        BORDER = "rgba(78,201,192,0.15)"; BHOVER = "rgba(78,201,192,0.4)"
    elif theme == "light":
        BG = "#f5f7fa"; B2 = "#ffffff"; CARD = "rgba(255,255,255,0.98)"
        BORDER = "rgba(78,201,192,0.15)"; BHOVER = "rgba(78,201,192,0.4)"
        TITLE = "#2563eb"; TEXT = "#1e293b"; WARM = "#e67e22"
    else:
        BG = "#0f111a"; B2 = "#1a1d2e"; CARD = "rgba(30,34,53,0.95)"
        BORDER = "rgba(78,201,192,0.15)"; BHOVER = "rgba(78,201,192,0.4)"

    css = """<style>
    /* ========== 全局基础 ========== */
    * {{ margin:0; padding:0; box-sizing:border-box; font-family:'Microsoft YaHei','Segoe UI Emoji','Apple Color Emoji','Noto Color Emoji',sans-serif; }}
    html,body,.stApp,[data-testid="stAppViewContainer"] {{ background:{BG}!important; color:{TEXT}!important; }}

    /* ========== 顶部栏 fixed 置顶 ========== */
    .custom-header-bar {{
        position: fixed !important;
        top: 0 !important; left: 0 !important; right: 0 !important;
        height: 73px !important;
        background: {B2} !important;
        backdrop-filter: blur(12px) !important;
        border-bottom: 1px solid {BORDER} !important;
        z-index: 999999 !important;
        display: flex !important;
        align-items: center !important;
        padding: 0 20px !important;
        box-shadow: 0 2px 10px rgba(0,0,0,0.15) !important;
    }}
    .header-title {{ color: {TITLE} !important; font-size: 16px !important; font-weight: 800 !important; white-space: nowrap !important; }}
    .status-nav {{ display: flex; gap: 5px; flex: 1; justify-content: flex-end; margin-right: 12px; overflow: hidden; }}
    .status-item {{ display: inline-flex; gap: 3px; font-size: 12px; padding: 3px 8px; border-radius: 5px; background: transparent; border: 1px solid {BORDER}; align-items: center; white-space: nowrap; color: {WARM}; }}
    .status-label {{ color: {TITLE}; font-size: 11px; font-weight: bold; }}
    .status-value {{ color: {WARM}; font-size: 12px; font-weight: bold; }}
    .header-deploy-area {{ display: flex; gap: 8px; flex-shrink: 0; }}
    .deploy-btn, .theme-btn {{ background: transparent !important; color: {TITLE} !important; border: 1px solid {BORDER} !important; border-radius: 5px !important; padding: 4px 12px !important; font-size: 17px !important; font-weight: bold !important; cursor: pointer !important; font-family: 'Microsoft YaHei', sans-serif !important; }}
    .deploy-btn:hover, .theme-btn:hover {{ background: rgba(78,201,192,0.15) !important; }}

    /* ========== 侧边栏强制显示 & 标题 ========== */
    section[data-testid="stSidebar"] {{
        background: {B2} !important;
        min-width: 265px !important;
        max-width: 265px !important;
        width: 265px !important;
        border-right: 1px solid {BORDER} !important;
        padding-top: 58px !important;
        z-index: 101 !important;
        display: block !important;
        visibility: visible !important;
        opacity: 1 !important;
        transform: none !important;
        position: relative !important;
        left: 0 !important;
        margin-left: 0 !important;
        flex-shrink: 0 !important;
        overflow: visible !important;
    }}
    /* ========== 侧边栏主标题：已移至顶部栏，此处不再显示 ========== */
    .sidebar-main-title {{ display: none !important; }}
    .sidebar-title {{ color: {TITLE} !important; font-size: 14px !important; font-weight: 700 !important; margin: 10px 0 5px 0 !important; border-bottom: 1px solid {BORDER} !important; padding-bottom: 2px !important; }}

    /* ========== 部署/主题按钮：白色18px不加粗渐变绿，间距缩小10% ========== */
    section[data-testid="stSidebar"] .sidebar-top-btns-wrapper {{
        display: flex !important;
        gap: 6px !important;
        margin-bottom: 10px !important;
    }}
    section[data-testid="stSidebar"] .sidebar-top-btns-wrapper > div {{ width: 100% !important; }}
    /* 直接命中按钮容器类（Streamlit生成） */
    section[data-testid="stSidebar"] .st-key-deploy_btn button,
    section[data-testid="stSidebar"] .st-key-theme_toggle_btn button,
    section[data-testid="stSidebar"] .st-key-deploy_btn button > div,
    section[data-testid="stSidebar"] .st-key-theme_toggle_btn button > div,
    /* 兜底：侧边栏顶部按钮区域的所有按钮 */
    section[data-testid="stSidebar"] .sidebar-top-btns-wrapper button {{
        background: linear-gradient(135deg, #4EC9C0, #059669) !important;
        color: #ffffff !important;
        font-size: 18px !important;
        font-weight: normal !important;
        border: none !important;
        border-radius: 6px !important;
        padding: 0 !important;
        margin: 0 !important;
        width: 100% !important;
        text-align: center !important;
        white-space: nowrap !important;
        min-height: unset !important;
        height: 34px !important;
        line-height: 34px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        font-family: 'Microsoft YaHei', sans-serif !important;
    }}
    /* 按钮内的文字 */
    section[data-testid="stSidebar"] .st-key-deploy_btn button p,
    section[data-testid="stSidebar"] .st-key-theme_toggle_btn button p,
    section[data-testid="stSidebar"] .sidebar-top-btns-wrapper button p {{
        color: #ffffff !important;
        font-size: 18px !important;
        font-weight: normal !important;
    }}
    section[data-testid="stSidebar"] .st-key-deploy_btn button:hover,
    section[data-testid="stSidebar"] .st-key-theme_toggle_btn button:hover,
    section[data-testid="stSidebar"] .sidebar-top-btns-wrapper button:hover {{
        filter: brightness(1.08) !important;
    }}

    /* ========== 运行模式 radio ========== */
    section[data-testid="stSidebar"] div[role="radiogroup"] {{
        gap: 40px !important;
        align-items: center !important;
        padding: 0 !important;
        margin: 0 !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] > label {{
        display: inline-flex !important;
        align-items: center !important;
        gap: 4px !important;
        margin: 0 !important;
        padding: 0 !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label span,
    section[data-testid="stSidebar"] div[role="radiogroup"] label p {{
        font-size: 12px !important;
        color: {WARM} !important;
        letter-spacing: 1px !important;
        line-height: 1 !important;
        margin: 0 !important;
        padding: 0 !important;
        display: inline !important;
        white-space: nowrap !important;
    }}

    /* ========== 模型选择下拉框：11px青灰色右对齐30px高红色框线 ========== */
    section[data-testid="stSidebar"] div[data-testid="stSelectbox"] > div > div,
    section[data-testid="stSidebar"] div[data-testid="stSelectbox"] [data-baseweb="select"],
    section[data-testid="stSidebar"] div[data-testid="stSelectbox"] .st-bb,
    section[data-testid="stSidebar"] div[data-testid="stSelectbox"] .st-at {{
        min-height: 36px !important;
        height: 36px !important;
        font-size: 10px !important;
        color: #5F9EA0 !important;
        background: {B2} !important;
        border: 1px solid #e06c75 !important;
        border-radius: 4px !important;
        text-align: right !important;
    }}
    section[data-testid="stSidebar"] div[data-testid="stSelectbox"] span {{
        font-size: 10px !important;
        color: #5F9EA0 !important;
    }}
    /* 下拉展开菜单 */
    section[data-testid="stSidebar"] div[data-testid="stSelectbox"] [role="listbox"] div {{
        font-size: 10px !important;
        color: #5F9EA0 !important;
        text-align: right !important;
    }}

    /* ========== 健康检查：11px暗黄色 ========== */
    section[data-testid="stSidebar"] .health-info,
    section[data-testid="stSidebar"] .health-info div,
    section[data-testid="stSidebar"] .health-info span,
    section[data-testid="stSidebar"] .health-info p {{
        font-size: 10px !important;
        color: {WARM} !important;
        line-height: 1.5 !important;
    }}

    /* ========== 控制面板图标调大20%（49px） ========== */
    section[data-testid="stSidebar"] .control-btn-style [data-testid="stButton"] > button,
    section[data-testid="stSidebar"] .control-btn-style button,
    section[data-testid="stSidebar"] .control-btn-style button p {{
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        width: 49px !important;
        height: 49px !important;
        color: {TEXT} !important;
        font-size: 44px !important;
        padding: 2px !important;
        margin: 0 !important;
        line-height: 1 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
    }}
    section[data-testid="stSidebar"] .control-btn-style [data-testid="stButton"] > button:hover {{
        background: transparent !important;
        border: none !important;
        color: {TITLE} !important;
        filter: brightness(1.3) !important;
        transform: none !important;
    }}

    /* ========== 语音控制：自适应一行，字体缩小，垂直对齐 ========== */
    section[data-testid="stSidebar"] .voice-control-wrapper [data-testid="stButton"] > button {{
        font-size: 11px !important;
        padding: 4px 6px !important;
        white-space: nowrap !important;
        margin-top: 16px !important;
        vertical-align: bottom !important;
        display: inline-flex !important;
        align-items: flex-end !important;
    }}
    section[data-testid="stSidebar"] .voice-control-wrapper .stCheckbox {{
        margin-top: 16px !important;
        display: inline-flex !important;
        align-items: flex-end !important;
    }}
    section[data-testid="stSidebar"] .voice-control-wrapper .stCheckbox label p,
    section[data-testid="stSidebar"] .voice-control-wrapper .stCheckbox label span {{
        font-size: 10px !important;
        color: {WARM} !important;
        white-space: nowrap !important;
    }}
    section[data-testid="stSidebar"] .voice-control-wrapper .stCheckbox {{ margin: 0 !important; margin-top: 16px !important; }}

    /* ========== 文件上传：青灰色，去除白框背景，全覆盖 ========== */
    section[data-testid="stSidebar"] [data-testid="stFileUploader"],
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] > div,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] > div > div,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] > div > div > div {{
        background: transparent !important;
        border: 1px solid {BORDER} !important;
        border-radius: 6px !important;
    }}
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] span,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] button,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] p,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] small,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] label,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] div,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] a,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] * {{
        font-size: 12px !important;
        color: #5F9EA0 !important;
    }}
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] button {{
        background: transparent !important;
        border: 1px solid {BORDER} !important;
        color: #5F9EA0 !important;
        border-radius: 4px !important;
    }}
    /* 拖拽区域虚线框内文字也改为青灰色 */
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"],
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] * {{
        color: #5F9EA0 !important;
    }}

    /* ========== 侧边栏全局内容字体（按钮除外） ========== */
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span:not([data-testid="stChatMessageAvatar"]):not(.stTooltipIcon),
    section[data-testid="stSidebar"] div:not(.sidebar-title):not(.sidebar-main-title),
    section[data-testid="stSidebar"] label {{
        font-size: 12px !important;
        color: {WARM} !important;
    }}
    section[data-testid="stSidebar"] button p,
    section[data-testid="stSidebar"] button {{
        color: {TITLE} !important;
    }}

    /* ========== 右侧对话标题行 fixed 置顶（在 header 下方） ========== */
    .st-key-dialogue_title_bar {{
        position: fixed !important;
        top: 85px !important;
        left: 265px !important;
        right: 0 !important;
        height: 43px !important;
        z-index: 99999 !important;
        background: {BG} !important;
        border-bottom: 1px solid {BORDER} !important;
        padding: 0 12px !important;
        display: flex !important;
        align-items: center !important;
        box-sizing: border-box !important;
    }}
    .st-key-dialogue_title_bar .stColumn {{
        display: flex !important;
        align-items: center !important;
        height: 43px !important;
        overflow: hidden !important;
    }}
    /* 走马灯容器 */
    .st-key-dialogue_title_bar .marquee-container {{
        width: 100% !important;
        overflow: hidden !important;
        white-space: nowrap !important;
        display: flex !important;
        align-items: center !important;
        height: 43px !important;
        position: relative !important;
        padding-left: 5px !important;
    }}
    /* 删除选中按钮：右移10px + 字体图标缩小15% */
    .st-key-dialogue_title_bar [data-testid="stButton"] > button,
    .st-key-dialogue_title_bar button {{
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        color: {TITLE} !important;
        font-size: 12px !important;
        font-weight: bold !important;
        padding: 2px 6px !important;
        min-height: unset !important;
        height: auto !important;
        line-height: 1 !important;
        width: auto !important;
        margin-right: 20px !important;
        white-space: nowrap !important;
    }}
    .st-key-dialogue_title_bar [data-testid="stButton"] > button:hover,
    .dialogue-title-bar-wrapper [data-testid="stButton"] > button:hover {{
        background: rgba(78,201,192,0.1) !important;
    }}

    /* ========== 主内容区：顶部留出固定栏空间（73px header + 43px title bar + 10px buffer） ========== */
    .main .block-container,
    [data-testid="stAppViewContainer"] .main .block-container {{
        padding-top: 126px !important;
        padding-left: 275px !important;
        padding-right: 20px !important;
        background: {BG} !important;
    }}

    /* ========== 消息区域（顶部留出标题行空间 32px + 间距，底部留出输入框空间） ========== */
    .chat-scroll-area {{
        max-height: calc(100vh - 300px) !important;
        overflow-y: auto !important;
        padding-right: 8px !important;
        padding-top: 18px !important;
        padding-bottom: 60px !important;
    }}
    .stChatMessage {{
        position: relative !important;
        border: 1px solid {BORDER} !important;
        border-radius: 10px !important;
        padding: 10px 14px !important;
        margin-bottom: 1px !important;
        background: {CARD} !important;
        margin-right: 0 !important;
    }}
    .stChatMessage + .stChatMessage {{
        margin-top: -4px !important;
    }}
    .stChatMessage p, .stChatMessage span:not([data-testid="stChatMessageAvatar"]), .stChatMessage div, .stChatMessage li, .stChatMessage code {{
        color: {TEXT} !important;
        font-size: 14px !important;
    }}
    .stChatMessage h1,.stChatMessage h2,.stChatMessage h3,.stChatMessage h4,.stChatMessage h5,.stChatMessage h6, .stChatMessage strong {{
        color: {TITLE} !important;
        font-size: 15px !important;
    }}
    .stChatMessage pre {{
        background: rgba(0,0,0,0.2) !important;
        border-radius: 6px !important;
        padding: 8px !important;
    }}
    .stChatMessage pre code {{
        font-size: 13px !important;
        color: {TEXT} !important;
        background: transparent !important;
    }}
    .stChatMessage[data-testid="user"] {{
        background: linear-gradient(135deg,rgba(78,201,192,0.05),rgba(4,120,87,0.05)) !important;
        margin-left: 40px !important;
        margin-right: 0 !important;
    }}
    .stChatMessage[data-testid="assistant"] {{
        background: transparent !important;
        border-left: 3px solid {TITLE} !important;
        margin-left: 0 !important;
        margin-right: 0 !important;
    }}
    [data-testid="stChatMessageAvatar"] {{
        font-family: "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", emoji !important;
    }}

    /* ========== 复制/修改按钮：固定于对话框下沿5px处 ========== */
    .msg-action-bar {{
        display: flex !important;
        flex-direction: row !important;
        gap: 8px !important;
        align-items: center !important;
        justify-content: flex-end !important;
        height: 24px !important;
        margin-top: 5px !important;
        padding: 0 4px !important;
        position: relative !important;
    }}
    .msg-action-bar .oc-action-btn {{
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        color: {TITLE} !important;
        font-size: 14px !important;
        width: 24px !important;
        height: 24px !important;
        padding: 0 !important;
        margin: 0 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        cursor: pointer !important;
        line-height: 1 !important;
        opacity: 0.7 !important;
        transition: opacity 0.2s !important;
        font-family: 'Segoe UI Symbol', sans-serif !important;
        text-decoration: none !important;
        outline: none !important;
        appearance: none !important;
        -webkit-appearance: none !important;
    }}
    .msg-action-bar .oc-action-btn:hover {{
        opacity: 1 !important;
        filter: brightness(1.3) !important;
        background: rgba(78,201,192,0.1) !important;
        border: 1px solid {BORDER} !important;
        border-radius: 4px !important;
    }}
    /* 让 stChatMessage 相对定位，按钮绝对定位到下沿 */
    .stChatMessage {{
        position: relative !important;
    }}
    .stChatMessage + div:has(.msg-action-bar) {{
        position: absolute !important;
        bottom: 5px !important;
        right: 14px !important;
        margin: 0 !important;
    }}

    /* ========== 输入框外框透明 + 内框下移10px ========== */
    [data-testid="stChatInput"] {{
        border-top: 1px solid {BORDER} !important;
        margin-top: 45px !important;
        background: transparent !important;
        position: relative !important;
        z-index: 99998 !important;
    }}
    [data-testid="stChatInput"] > div,
    [data-testid="stChatInput"] > div > div,
    .stChatInputContainer,
    .stChatInputContainer > div,
    .stChatInputContainer > div > div {{
        background-color: transparent !important;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }}
    /* 内框 textarea：高度增加50%，带滚动条 */
    .stChatInput textarea,
    [data-testid="stChatInput"] textarea,
    textarea[aria-label="Chat input"] {{
        font-size: 18px !important;
        color: #5F9EA0 !important;
        font-weight: 400 !important;
        border: 1px solid #e06c75 !important;
        border-radius: 4px !important;
        box-shadow: none !important;
        background: transparent !important;
        min-height: 76px !important;
        max-height: 200px !important;
        overflow-y: auto !important;
        margin-top: 40px !important;
    }}
    textarea[aria-label="Chat input"]::placeholder {{
        color: rgba(90,158,153,0.5) !important;
        font-size: 14px !important;
    }}
    textarea[aria-label="Chat input"]:focus {{
        outline: none !important;
        border-color: #e06c75 !important;
    }}

    /* ========== 全局控件 ========== */
    button[kind="secondary"] {{
        background: transparent !important;
        color: {TITLE} !important;
        border: 1px solid {BORDER} !important;
        font-size: 11px !important;
        border-radius: 6px !important;
    }}
    button[kind="secondary"]:hover {{
        filter: brightness(1.3) !important;
        background: rgba(78,201,192,0.1) !important;
    }}
    button[kind="formSubmit"] {{
        background: white !important;
        color: {TITLE} !important;
        border: 1px solid {BORDER} !important;
        font-size: 14px !important;
        font-weight: bold !important;
        border-radius: 6px !important;
    }}
    label[data-testid="stWidgetLabel"] p {{
        font-size: 14px !important;
        color: {TEXT} !important;
        font-weight: bold !important;
    }}
    div[data-testid="stTextInput"] input,
    div[data-testid="stTextInput"] > div > div > input {{
        min-height: 38px !important;
        background: {B2} !important;
        color: {TEXT} !important;
        border: 1px solid {BORDER} !important;
        font-size: 14px !important;
        border-radius: 4px !important;
    }}
    .stExpander summary {{
        background: transparent !important;
        border: 1px solid {BORDER} !important;
        border-radius: 6px !important;
    }}
    .stExpander summary p {{
        font-size: 12px !important;
        color: {TITLE} !important;
    }}

    /* ========== 隐藏原生标题栏文字 ========== */
    header[data-testid="stHeader"] > div:first-child > div:first-child {{ display: none !important; }}
    header[data-testid="stHeader"] {{
        background: transparent !important;
        border-bottom: none !important;
        box-shadow: none !important;
        height: 0 !important;
        min-height: 0 !important;
    }}

    /* ========== 管理面板区域 ========== */
    .model-main-title {{ font-size: 20px !important; color: {TITLE} !important; }}
    .model-sub-title {{ font-size: 14px !important; color: {TITLE} !important; }}
    /* 关闭管理面板：暗黄色，字体缩小5%，宽度缩小10%，红色框线 */
    .mgr-close-panel [data-testid="stButton"] > button,
    .mgr-close-panel button[kind="secondary"],
    .mgr-close-panel button {{
        background: #B8860B !important;
        color: #ffffff !important;
        border: 1px solid red !important;
        font-size: 10px !important;
        letter-spacing: 0.8px !important;
        margin-bottom: 3px !important;
        border-radius: 6px !important;
        width: auto !important;
        min-width: unset !important;
        max-width: 90% !important;
        padding: 3px 7px !important;
        transform: scale(0.9) !important;
        transform-origin: left center !important;
    }}
    /* 模型列表：行间距缩小60%，内容垂直居中，图标缩小20% */
    .model-list-row {{
        display: flex !important;
        align-items: center !important;
        min-height: 10px !important;
        line-height: 1 !important;
        margin-bottom: 0px !important;
        margin-top: 0px !important;
        padding: 0px 0 !important;
    }}
    .model-list-row div,
    .model-list-row code {{
        padding: 0 !important;
        margin: 0 !important;
        font-size: 11px !important;
        font-family: Arial, sans-serif !important;
        color: #5F9EA0 !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        line-height: 1 !important;
        font-weight: normal !important;
        display: flex !important;
        align-items: center !important;
    }}
    .model-list-row code {{
        background: transparent !important;
        border: none !important;
    }}
    /* 操作图标缩小20%，垂直居中 */
    .model-action-btn-compact {{
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        min-height: 11px !important;
    }}
    .model-action-btn-compact [data-testid="stButton"] > button,
    .model-action-btn-compact button[kind="secondary"],
    .model-action-btn-compact button {{
        width: 8px !important;
        height: 9px !important;
        background: transparent !important;
        border: none !important;
        color: {TITLE} !important;
        font-size: 5px !important;
        padding: 0 !important;
        border-radius: 3px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        line-height: 1 !important;
        margin: 0 !important;
    }}
    .model-action-btn-compact [data-testid="stButton"] > button:hover {{
        filter: brightness(1.3) !important;
        background: rgba(78,201,192,0.1) !important;
        border: 1px solid {BORDER} !important;
    }}
    /* 模型管理面板：关闭/管理/新增三者垂直行间距缩小30% */
    .mgr-close-panel {{ margin-bottom: 3px !important; }}
    .model-main-title {{ margin-top: 2px !important; margin-bottom: 3px !important; }}
    .model-sub-title {{ margin-top: 2px !important; margin-bottom: 3px !important; }}
    /* 其余区域行间距缩小30% */
    .mgr-form-row {{ margin-bottom: 4px !important; }}
    .mgr-section {{ margin-bottom: 8px !important; }}
    /* Streamlit stColumn 间距压缩 */
    .mgr-close-panel [data-testid="stHorizontalBlock"],
    .model-list-row + [data-testid="stHorizontalBlock"] {{
        gap: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }}

    /* ========== 代码块独立容器样式 ========== */
    .oc-code-block {{
        background: rgba(0,0,0,0.35) !important;
        border-radius: 6px !important;
        padding: 0 !important;
        margin: 6px 0 !important;
        border: 1px solid rgba(78,201,192,0.1) !important;
        overflow: hidden !important;
    }}
    .oc-code-header {{
        display: flex !important;
        justify-content: space-between !important;
        align-items: center !important;
        padding: 4px 10px !important;
        background: rgba(78,201,192,0.08) !important;
        border-bottom: 1px solid rgba(78,201,192,0.1) !important;
        font-size: 11px !important;
        color: {TITLE} !important;
    }}
    .oc-code-copy-btn {{
        background: transparent !important;
        border: 1px solid {BORDER} !important;
        color: {TITLE} !important;
        font-size: 11px !important;
        padding: 1px 8px !important;
        border-radius: 4px !important;
        cursor: pointer !important;
    }}
    .oc-code-copy-btn:hover {{
        background: rgba(78,201,192,0.15) !important;
    }}
    .oc-code-content {{
        padding: 8px 10px !important;
        overflow-x: auto !important;
    }}
    .oc-code-content pre {{
        margin: 0 !important;
        padding: 0 !important;
        background: transparent !important;
    }}
    .oc-code-content code {{
        font-family: 'Consolas','Monaco','Courier New',monospace !important;
        font-size: 13px !important;
        color: {TEXT} !important;
        background: transparent !important;
        white-space: pre !important;
        line-height: 1.5 !important;
    }}
    /* 普通文本气泡 */
    .oc-text-block {{
        padding: 2px 0 !important;
        line-height: 1.6 !important;
    }}

    /* ========== 日志 / 系统信息 ========== */
    .log-content {{ font-size: 12px !important; color: {WARM} !important; }}
    .system-info-panel div {{ font-size: 12px !important; color: {WARM} !important; line-height: 1.8 !important; }}


    /* ===== 输入框背景彻底透明（终极穿透版） ===== */
    [data-testid="stChatInput"],
    [data-testid="stChatInput"] > div,
    [data-testid="stChatInput"] > div > div,
    [data-testid="stChatInput"] div[class*="css"],
    [data-testid="stChatInput"] div[class*="stChatInput"],
    .stChatInputContainer,
    .stChatInputContainer > div,
    .stChatInputContainer div[class*="css"],
    /* Streamlit 1.28+ internal containers */
    [data-testid="stChatInput"] div[data-baseweb],
    [data-testid="stChatInput"] div[data-baseweb] > div,
    [data-testid="stChatInput"] div[data-baseweb] div,
    [data-testid="stChatInput"] div[role="textbox"] {{
        background-color: transparent !important;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }}
    /* Force transparent on the big white bottom area */
    .stApp > div > div > div:last-child,
    .main > div > div:last-child,
    [data-testid="stAppViewContainer"] > div:last-child {{
        background: transparent !important;
    }}
    [data-testid="stChatInput"] textarea,
    [data-testid="stChatInput"] input,
    [data-testid="stChatInput"] div[role="textbox"] {{
        background-color: transparent !important;
        color: white !important;
    }}


    /* ===== 文件上传区域青灰色字体 ===== */
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] *,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] span,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] button,
    section[data-testid="stSidebar"] [data-testid="stFileUploader"] div {{
        color: #5F9EA0 !important;
    }}

    /* ========== 输入框白色底部区域彻底透明 ========== */
    .stBottom,
    .stBottom > div,
    .st-emotion-cache-128upt6,
    .st-emotion-cache-1p2n2i4,
    section[data-testid="stAppViewContainer"] .stBottom,
    /* Streamlit chat input outer container */
    .stChatInput,
    .stChatInput > div:first-child {{
        background: transparent !important;
        background-color: transparent !important;
    }}

    /* ========== 输入框文字颜色：青灰色 ========== */
    .st-key-input_area textarea,
    .st-key-input_area input,
    .st-key-input_area [data-testid="stTextArea"] textarea,
    [data-testid="stChatInput"] textarea {{
        color: #5F9EA0 !important;
        font-size: 14px !important;
    }}
    /* ========== 输入框文字颜色：青灰色（已合并到上方规则） ========== */
    ::-webkit-scrollbar {{ width: 6px !important; }}
    ::-webkit-scrollbar-thumb {{ background: {BHOVER} !important; border-radius: 3px !important; }}

    /* ========== 走马灯 ========== */
    .marquee-content {{
        display: inline-block !important;
        white-space: nowrap !important;
        color: {WARM} !important;
        font-size: 11px !important;
        min-width: max-content !important;
        animation: oc_marquee 60s linear infinite;
    }}
    @keyframes oc_marquee {{
        0%   {{ transform: translateX(0); }}
        100% {{ transform: translateX(-50%); }}
    }}
    </style>""".format(
        B2=B2, BG=BG, BHOVER=BHOVER, BORDER=BORDER, CARD=CARD, TEXT=TEXT, TITLE=TITLE, WARM=WARM)
    return css


def render_header():
    cur_id = st.session_state.get("current_used_model", "")
    cur_disp = simplify_model_name(cur_id) if cur_id else "未选择"
    sys_stat = st.session_state.status
    current_data = get_current_use_model_info()
    territory = get_territory_by_provider(current_data.get("provider", "")) if current_data else "未知"
    node_disp = f"{st.session_state.node_info['region']} {st.session_state.node_info.get('type','')}"
    memos_stat = st.session_state.memos_status

    st.markdown(f"""<div class="custom-header-bar">
        <span class="header-title">SelfClaw · 聚合智能系统</span>
        <div class="status-nav">
            <div class="status-item"><span class="status-label">模式</span><span class="status-value">{"自动" if st.session_state.model_mode=="auto" else "手动"}</span></div>
            <div class="status-item"><span class="status-label">模型</span><span class="status-value">{cur_disp[:20]}</span></div>
            <div class="status-item"><span class="status-label">节点</span><span class="status-value">{node_disp}</span></div>
            <div class="status-item"><span class="status-label">延迟</span><span class="status-value">{st.session_state.node_info["latency"]}</span></div>
            <div class="status-item"><span class="status-label">属地</span><span class="status-value">{territory}</span></div>
            <div class="status-item"><span class="status-label">状态</span><span class="status-value">{sys_stat}</span></div>
            <div class="status-item"><span class="status-label">MemOS</span><span class="status-value">{memos_stat}</span></div>
        </div>
        <div class="header-deploy-area">
            <button class="theme-btn" title="请使用左侧边栏切换主题">Theme</button>
            <button class="deploy-btn" title="请使用左侧边栏部署">Deploy</button>
        </div>
    </div>""", unsafe_allow_html=True)


def render_sidebar():
    with st.sidebar:
        # 侧边栏主标题已移至顶部栏，此处不再渲染

        # 2. 部署和主题按钮（渐变绿背景，白色18px加粗）
        st.markdown('<div class="sidebar-top-btns-wrapper">', unsafe_allow_html=True)
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            st.button("🚀 部 署", use_container_width=True, key="deploy_btn",
                      on_click=lambda: setattr(st.session_state, "show_deploy_menu", not st.session_state.show_deploy_menu))
        with col_btn2:
            theme_icons = {"dark": "🌙", "vscode-gray": "🪨", "light": "☀️"}
            theme_names = {"dark": "深 色", "vscode-gray": "柔 灰", "light": "浅 色"}
            cur_theme = st.session_state.theme
            if st.button(f"{theme_icons[cur_theme]} {theme_names[cur_theme]}", use_container_width=True, key="theme_toggle_btn"):
                themes = ["dark", "vscode-gray", "light"]
                idx = themes.index(st.session_state.theme)
                st.session_state.theme = themes[(idx + 1) % 3]
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="sidebar-title">运 行 模 式</div>', unsafe_allow_html=True)
        mode = st.radio("运行模式", ["自   动", "手   动"], horizontal=True, index=0 if st.session_state.model_mode == 'auto' else 1, key="mode_radio", label_visibility="collapsed")
        new_mode = "auto" if mode == "自   动" else "manual"
        if new_mode != st.session_state.model_mode:
            st.session_state.model_mode = new_mode
            st.session_state.fallback_round = 0
            st.session_state.cooldown_until = 0.0
            all_m = get_models_pool()
            st.session_state.selected_model = get_auto_models()[0]["id"] if new_mode == "auto" and get_auto_models() else (all_m[0]["id"] if all_m else "")
            st.session_state.current_used_model = st.session_state.selected_model

        st.markdown('<div class="sidebar-title">模 型 选 择</div>', unsafe_allow_html=True)
        all_models = get_models_pool()
        available = [m for m in all_models if m.get("type") == "auto"] if st.session_state.model_mode == "auto" else all_models
        if available:
            labels = [m["label"] for m in available]
            ids = [m["id"] for m in available]
            try:
                cur_idx = ids.index(st.session_state.selected_model)
            except:
                cur_idx = 0
            lab = st.selectbox("模型选择", labels, index=cur_idx, key="model_select", label_visibility="collapsed")
            st.session_state.selected_model = ids[labels.index(lab)]
            if st.session_state.model_mode == "manual":
                st.session_state.current_used_model = st.session_state.selected_model
        else:
            st.warning("无可用模型")

        st.markdown('<div class="sidebar-title">健 康 检 查</div>', unsafe_allow_html=True)
        gstatus = "🟢 正常(最优)" if is_current_in_top3() else ("🟡 已降级" if get_current_use_model_info() else "🔴 全部异常")
        top_status = "✅ 在头部最优线路" if is_current_in_top3() else "⚠️ 已降级，后台自动巡检"
        degrade_str = " → ".join(st.session_state.degrade_path[-3:]) if st.session_state.degrade_path else "无降级"
        st.markdown(f'<div class="health-info">全局状态：{gstatus}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="health-info">线路状态：{top_status}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="health-info">降级路径：{degrade_str}</div>', unsafe_allow_html=True)
        cooldown_until = st.session_state.get("cooldown_until", 0)
        now_ts = time.time()
        if cooldown_until > now_ts and st.session_state.model_mode == "auto":
            sec_left = int(cooldown_until - now_ts)
            cooldown_text = f"⏳ 全网冷却中：剩余 {sec_left} 秒"
        else:
            cooldown_text = ""
        st.markdown(f'<div class="health-info" id="cooldown-timer" data-until="{cooldown_until}">{cooldown_text}</div>', unsafe_allow_html=True)

        st.markdown('<div class="sidebar-title">控 制 面 板</div>', unsafe_allow_html=True)
        st.markdown('<div class="control-btn-style">', unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns(5, gap="small")
        with c1:
            if st.button("🔄", help="刷新配置"):
                load_config_force()
                add_operation_log("🔄 配置已刷新", "info")
                st.rerun()
        with c2:
            if st.button("🧹", help="新开对话（保留历史）"):
                st.session_state.display_start_idx = len(st.session_state.messages)
                # 持久化 display_start_idx，重启后仍隐藏旧对话
                save_ui_state({
                    "show_version_panel": st.session_state.get("show_version_panel", False),
                    "show_plugin_panel": st.session_state.get("show_plugin_panel", False),
                    "installed_plugins": list(st.session_state.get("just_installed_plugins", set())),
                    "display_start_idx": st.session_state.display_start_idx
                })
                add_operation_log("🗑 对话显示已清空，历史保留", "info")
                st.rerun()
        with c3:
            if st.button("⚙️", help="模型管理"):
                st.session_state.show_model_manager = True
        with c4:
            if st.button("🔧", help="升级管理"):
                st.session_state.show_version_panel = not st.session_state.get("show_version_panel", False)
                save_ui_state({"show_version_panel": st.session_state.show_version_panel,
                               "show_plugin_panel": st.session_state.get("show_plugin_panel", False),
                               "installed_plugins": list(st.session_state.get("just_installed_plugins", set()))})
                info = check_upgrade()
                add_operation_log(info, "info")
        with c5:
            if st.button("🧩", help="插件管理"):
                st.session_state.show_plugin_panel = not st.session_state.get("show_plugin_panel", False)
                save_ui_state({"show_version_panel": st.session_state.get("show_version_panel", False),
                               "show_plugin_panel": st.session_state.show_plugin_panel,
                               "installed_plugins": list(st.session_state.get("just_installed_plugins", set()))})
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="sidebar-title">语 音 控 制</div>', unsafe_allow_html=True)
        if "voice_enabled" not in st.session_state:
            st.session_state.voice_enabled = False
        if "tts_enabled" not in st.session_state:
            st.session_state.tts_enabled = False

        # 语音控制：自适应一行显示
        st.markdown('<div class="voice-control-wrapper">', unsafe_allow_html=True)
        v1, v2, v3, v4 = st.columns([1.2, 1.2, 1, 1])
        with v1:
            if st.button("🎤 录音", use_container_width=True, key="start_record"):
                if VOICE_AVAILABLE:
                    text = speech_to_text()
                    if text:
                        st.session_state.pending_user_message = text
                        st.toast(f"录音识别：{text}", icon="🎤")
                    else:
                        st.toast("未识别到语音内容", icon="⚠️")
                else:
                    st.toast("语音模块未就绪", icon="🎤")
        with v2:
            if st.button("⏹️ 停止", use_container_width=True, key="stop_tts"):
                if VOICE_AVAILABLE:
                    stop_speech()
                    st.toast("语音播报已停止", icon="🎤")
        with v3:
            st.checkbox("语音输入", key="voice_enabled")
        with v4:
            st.checkbox("语音播报", key="tts_enabled")
        st.markdown('</div>', unsafe_allow_html=True)

        # 升级管理
        if st.session_state.get("show_version_panel", False):
            st.markdown('<div class="sidebar-title">升 级 管 理</div>', unsafe_allow_html=True)
            version_info = check_upgrade()
            st.markdown('<div class="health-info">' + version_info + '</div>', unsafe_allow_html=True)
            if "可升级" in version_info:
                st.info("检测到新版本，点击下方按钮升级")
                if st.button("⬆️ 立 即 升 级", use_container_width=True, key="do_upgrade_btn"):
                    st.toast("开始升级...", icon="⏳")
                    with st.spinner("正在升级 OpenClaw，请稍候..."):
                        try:
                            ok, msg = upgrade_openclaw()
                            if ok:
                                st.success(msg)
                                st.toast(msg, icon="✅")
                                add_operation_log(f"✅ 升级成功: {msg}", "success")
                            else:
                                st.error(msg)
                                st.toast(msg, icon="❌")
                                add_operation_log(f"❌ 升级失败: {msg}", "error")
                        except Exception as e:
                            err = f"升级异常: {str(e)}"
                            st.error(err)
                            st.toast(err, icon="❌")
                            add_operation_log(err, "error")
            else:
                st.success("当前已是最新版本")

        # 插件管理：安装后强制刷新
        if st.session_state.get("show_plugin_panel", False):
            st.markdown('<div class="sidebar-title">插 件 管 理</div>', unsafe_allow_html=True)
            st.markdown('<div class="health-info">🔌 系统扩展插件</div>', unsafe_allow_html=True)
            if os.path.exists(EXTENSIONS_DIR):
                extension_list = [f for f in os.listdir(EXTENSIONS_DIR) if os.path.isdir(os.path.join(EXTENSIONS_DIR, f)) and not f.startswith("_")]
                if extension_list:
                    for ext_name in extension_list:
                        st.markdown(f'<div class="health-info">📦 {ext_name}</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="health-info">暂无安装系统扩展</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="health-info">扩展目录不存在</div>', unsafe_allow_html=True)
            st.divider()
            missing_plugins, installed_plugins = check_tool_plugins()
            # 过滤掉本次会话中已安装的插件 + 持久化已安装的插件
            just_installed = st.session_state.get("just_installed_plugins", set()) | st.session_state.get("installed_plugins_persist", set())
            if just_installed:
                # 先把持久化/已安装的从 missing 移到 installed
                still_missing = []
                for p, d in missing_plugins:
                    if p in just_installed:
                        installed_plugins.append((p, d))
                    else:
                        still_missing.append((p, d))
                missing_plugins = still_missing
            if missing_plugins:
                st.markdown('<div class="health-info">⚠️ 缺失必备工具插件</div>', unsafe_allow_html=True)
                for pkg_name, desc in missing_plugins:
                    col_pkg, col_btn = st.columns([2, 1])
                    col_pkg.markdown(f'<div class="health-info">{pkg_name}<br/><span style="font-size:11px;">{desc}</span></div>', unsafe_allow_html=True)
                    if col_btn.button("安 装", key="install_" + pkg_name, use_container_width=True):
                        with st.spinner("正在安装 " + pkg_name + "..."):
                            try:
                                success, msg = install_tool_plugin(pkg_name)
                                if success:
                                    # 标记为已安装（防止import缓存导致仍显示缺失）
                                    if "just_installed_plugins" not in st.session_state:
                                        st.session_state.just_installed_plugins = set()
                                    st.session_state.just_installed_plugins.add(pkg_name)
                                    # 持久化已安装插件列表
                                    save_ui_state({
                                        "show_version_panel": st.session_state.get("show_version_panel", False),
                                        "show_plugin_panel": st.session_state.get("show_plugin_panel", False),
                                        "installed_plugins": list(st.session_state.just_installed_plugins)
                                    })
                                    st.toast(msg, icon="✅")
                                    add_operation_log(f"✅ 插件安装成功: {pkg_name}", "success")
                                else:
                                    st.toast(msg, icon="❌")
                                    add_operation_log(f"❌ 插件安装失败: {pkg_name} - {msg}", "error")
                            except Exception as e:
                                st.toast(f"安装异常: {str(e)}", icon="❌")
                        st.rerun()
            if installed_plugins:
                st.markdown('<div class="health-info">✅ 已安装工具插件</div>', unsafe_allow_html=True)
                for pkg_name, desc in installed_plugins:
                    st.markdown(f'<div class="health-info">{pkg_name} | {desc}</div>', unsafe_allow_html=True)

        st.markdown('<div class="sidebar-title">文 件 上 传</div>', unsafe_allow_html=True)
        files = st.file_uploader("选择文件", accept_multiple_files=True, label_visibility="collapsed", key="chat_upload")
        if files:
            st.session_state.uploaded_files = files
            cu1, cu2 = st.columns(2)
            with cu1:
                if st.button("解 析", use_container_width=True):
                    with st.spinner():
                        st.session_state.current_file_contexts = handle_files(files)
                    st.success("解析完成")
            with cu2:
                if st.button("清 空 文 件", use_container_width=True):
                    st.session_state.uploaded_files = []
                    st.rerun()

        st.markdown('<div class="sidebar-title">系 统 信 息</div>', unsafe_allow_html=True)
        model_count = len(get_models_pool())
        perm_count = len(st.session_state.get("permanent_blacklist", {}))
        temp_count = len(st.session_state.get("temporary_cooldown", {}))
        territory = get_territory_by_provider(get_current_use_model_info().get("provider", "")) if get_current_use_model_info() else "未知"
        sys_html = '<div class="system-info-panel">'
        sys_html += '<div>网关: ' + GATEWAY + '</div>'
        sys_html += '<div>模式: ' + st.session_state.model_mode + '</div>'
        sys_html += '<div>模型数: ' + str(model_count) + '</div>'
        sys_html += '<div>属地: ' + territory + '</div>'
        sys_html += '<div>MemOS: ' + st.session_state.memos_status + '</div>'
        sys_html += '<div>永久故障: ' + str(perm_count) + ' 个</div>'
        sys_html += '<div>临时冷却: ' + str(temp_count) + ' 个</div>'
        sys_html += '</div>'
        st.markdown(sys_html, unsafe_allow_html=True)

        st.markdown('<div class="sidebar-title">操 作 日 志</div>', unsafe_allow_html=True)
        with st.expander("展 开 日 志", expanded=False):
            if st.session_state.operation_logs:
                logs_html = ""
                for log in reversed(st.session_state.operation_logs):
                    logs_html += '<span class="log-content">[' + log["time"] + '] ' + log["message"] + '</span><br/>'
                st.markdown(logs_html, unsafe_allow_html=True)
            else:
                st.markdown('<span class="log-content">暂无日志</span>', unsafe_allow_html=True)

        # 微信管理：绑定/解绑（始终显示两个按钮）
        st.markdown('<div class="sidebar-title">微 信 管 理</div>', unsafe_allow_html=True)
        if "wechat_bound" not in st.session_state:
            st.session_state.wechat_bound = False
        if "show_qr" not in st.session_state:
            st.session_state.show_qr = False
        wechat_bound = st.session_state.get("wechat_bound", False)
        
        # 始终并排显示绑定/解绑两个按钮（均不设disabled，始终可点击）
        wx_col1, wx_col2 = st.columns(2)
        with wx_col1:
            if st.button("绑 定", key="wechat_bind", use_container_width=True):
                if not wechat_bound:
                    st.session_state.show_qr = True
                    st.rerun()
                else:
                    st.toast("微信已绑定，无需重复绑定", icon="ℹ️")
        with wx_col2:
            if st.button("解 绑", key="wechat_unbind", use_container_width=True):
                if wechat_bound:
                    st.session_state.confirm_unbind = True
                    st.rerun()
                else:
                    st.toast("未绑定微信，无需解绑", icon="ℹ️")
        
        # 绑定流程：显示二维码
        if not wechat_bound and st.session_state.show_qr:
            st.image("https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=WECHAT_BIND", width=150)
            st.markdown('<div class="health-info">请使用微信扫描二维码绑定</div>', unsafe_allow_html=True)
            if st.button("确 认 绑 定", key="confirm_bind", use_container_width=True):
                st.session_state.wechat_bound = True
                st.session_state.show_qr = False
                st.toast("微信绑定成功", icon="✅")
                st.rerun()
        
        # 已绑定状态显示
        if wechat_bound:
            st.markdown('<div class="health-info">✅ 已绑定微信</div>', unsafe_allow_html=True)
        
        # 解绑确认弹窗
        if st.session_state.get("confirm_unbind", False):
            st.markdown('<div style="color:#e06c75; font-size:12px;">⚠️ 确认要解绑微信吗？</div>', unsafe_allow_html=True)
            c_unbind_y, c_unbind_n = st.columns(2)
            with c_unbind_y:
                if st.button("确 认 解 绑", key="confirm_unbind_yes", use_container_width=True):
                    st.session_state.wechat_bound = False
                    st.session_state.show_qr = False
                    st.session_state.confirm_unbind = False
                    st.toast("微信已解绑", icon="✅")
                    st.rerun()
            with c_unbind_n:
                if st.button("取 消", key="confirm_unbind_no", use_container_width=True):
                    st.session_state.confirm_unbind = False
                    st.rerun()


def render_model_manager():
    if not st.session_state.show_model_manager:
        return
    st.markdown('<div class="mgr-close-panel">', unsafe_allow_html=True)
    st.button("关 闭 管 理 面 板", key="close_mgr_btn", on_click=lambda: setattr(st.session_state, 'show_model_manager', False))
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="model-main-title">模 型 管 理</h1>', unsafe_allow_html=True)
    models = get_models_pool()
    show_edit = st.session_state.get("editing_model") is not None
    with st.form("model_form", clear_on_submit=True):
        title = "✏️ 编 辑 模 型" if show_edit else "➕ 新 增 模 型"
        st.markdown(f'<h2 class="model-sub-title">{title}</h2>', unsafe_allow_html=True)
        col1, col2, col3 = st.columns(3)
        edit_data = st.session_state.get("editing_model_data", {}) if show_edit else {}
        with col1:
            nl = st.text_input("显示标签", value=edit_data.get("label", ""))
            ni = st.text_input("模型ID", value=edit_data.get("id", ""))
        with col2:
            na = st.text_input("模型实际名称", value=edit_data.get("actual", ""))
            nurl = st.text_input("Base URL", value=edit_data.get("base_url", GATEWAY))
        with col3:
            np = st.text_input("提供商", value=edit_data.get("provider", ""))
            nt = st.selectbox("运行类型", ["auto", "manual"], index=0 if edit_data.get("type", "auto") == "auto" else 1)
        if st.form_submit_button("💾 保 存"):
            if nl and ni and na and np:
                nm = {"id": ni, "label": nl, "actual": na, "provider": np, "base_url": nurl, "type": nt}
                if show_edit:
                    ok, msg = update_model_in_config(st.session_state.editing_model["id"], nm)
                    st.session_state.editing_model = None
                else:
                    nm["order"] = len(models) + 1
                    ok, msg = add_model_to_config(nm)
                st.success(msg) if ok else st.error(msg)
                st.rerun()

    st.markdown('<h2 class="model-sub-title">模 型 列 表</h2>', unsafe_allow_html=True)
    headers = ["标签", "模型ID", "模型名称", "提供商", "删除", "上移", "下移", "编辑"]
    col_widths = [0.26, 0.20, 0.24, 0.09, 0.06, 0.06, 0.06, 0.06]
    cols_h = st.columns(col_widths)
    for i, h in enumerate(headers):
        with cols_h[i]:
            st.markdown(f'<span style="color:#4EC9C0;font-weight:bold;font-size:13px;display:block;padding:1px 0;">{h}</span>', unsafe_allow_html=True)
    for idx, m in enumerate(models):
        cols = st.columns(col_widths)
        with cols[0]:
            st.markdown(f'<div class="model-list-row"><div>{m["label"]}</div></div>', unsafe_allow_html=True)
        with cols[1]:
            st.markdown(f'<div class="model-list-row"><code>{m["id"]}</code></div>', unsafe_allow_html=True)
        with cols[2]:
            st.markdown(f'<div class="model-list-row"><div>{m.get("actual","")}</div></div>', unsafe_allow_html=True)
        with cols[3]:
            st.markdown(f'<div class="model-list-row"><div>{m.get("provider","")}</div></div>', unsafe_allow_html=True)
        with cols[4]:
            st.markdown('<div class="model-action-btn-compact">', unsafe_allow_html=True)
            if st.button("🗑", key=f"del_{m['id']}_{idx}"):
                delete_model_from_config(m["id"])
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        with cols[5]:
            st.markdown('<div class="model-action-btn-compact">', unsafe_allow_html=True)
            if st.button("⬆️", key=f"up_{m['id']}_{idx}", disabled=(idx == 0)):
                reorder_models_in_config(m["id"], "up")
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        with cols[6]:
            st.markdown('<div class="model-action-btn-compact">', unsafe_allow_html=True)
            if st.button("⬇️", key=f"down_{m['id']}_{idx}", disabled=(idx == len(models)-1)):
                reorder_models_in_config(m["id"], "down")
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        with cols[7]:
            st.markdown('<div class="model-action-btn-compact">', unsafe_allow_html=True)
            if st.button("✏️", key=f"edit_{m['id']}_{idx}"):
                st.session_state.editing_model = m
                st.session_state.editing_model_data = m.copy()
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)


def render_dialogue():
    marquee_parts = []
    if st.session_state.operation_logs:
        for log in st.session_state.operation_logs[-3:]:
            marquee_parts.append(f"{log['time']} {log['message']}")
    perm_blacklist = st.session_state.get("permanent_blacklist", {})
    if perm_blacklist:
        for model_id, reason in list(perm_blacklist.items())[:2]:
            marquee_parts.append(f"⚠️ {simplify_model_name(model_id)} 永久故障: {reason}")
    version_info = check_upgrade()
    if "可升级" in version_info:
        marquee_parts.append("🆙 OpenClaw 可升级")
    latest_log_msg = " ｜ ".join(marquee_parts) if marquee_parts else "[系统] 就绪"

    if "selected_msg_ids" not in st.session_state:
        st.session_state.selected_msg_ids = set()

    # 对话标题栏：用 st.container(key=...) 包装，确保 Streamlit 组件嵌套在内
    with st.container(key="dialogue_title_bar"):
        c1, c2, c3 = st.columns([1, 4, 3])
        with c1:
            st.markdown('<span style="color:#4EC9C0; font-size:18px; font-weight:bold; white-space:nowrap;">💬 对　话</span>', unsafe_allow_html=True)
        with c2:
            escaped_msg = html_module.escape(latest_log_msg)
            marquee_text = (escaped_msg + "&nbsp;&nbsp;&nbsp;&nbsp;") * 4
            st.markdown(f"""
            <div class="marquee-container">
                <span class="marquee-content">{marquee_text}</span>
            </div>
            """, unsafe_allow_html=True)
        with c3:
            if st.button("🗑删除选中", key="del_selected", use_container_width=True):
                selected = sorted(list(st.session_state.selected_msg_ids), reverse=True)
                for idx in selected:
                    if idx < len(st.session_state.messages):
                        del st.session_state.messages[idx]
                st.session_state.selected_msg_ids = set()
                # 重置 display_start_idx 避免索引越界
                st.session_state.display_start_idx = min(
                    st.session_state.get("display_start_idx", 0),
                    len(st.session_state.messages)
                )
                # 持久化删除操作到 user_memory.json
                save_user_memory(st.session_state.messages)
                # 同步持久化 display_start_idx
                save_ui_state({
                    "show_version_panel": st.session_state.get("show_version_panel", False),
                    "show_plugin_panel": st.session_state.get("show_plugin_panel", False),
                    "installed_plugins": list(st.session_state.get("just_installed_plugins", set())),
                    "display_start_idx": st.session_state.display_start_idx
                })
                st.rerun()

    st.markdown('<div class="chat-scroll-area">', unsafe_allow_html=True)
    visible_messages = st.session_state.messages[st.session_state.get("display_start_idx", 0):]

    for msg_idx, m in enumerate(visible_messages, start=st.session_state.get("display_start_idx", 0)):
        check_col, msg_col = st.columns([0.05, 0.95])
        with check_col:
            is_selected = msg_idx in st.session_state.selected_msg_ids
            if st.checkbox("选择", value=is_selected, key=f"chk_{msg_idx}", label_visibility="collapsed"):
                st.session_state.selected_msg_ids.add(msg_idx)
            else:
                st.session_state.selected_msg_ids.discard(msg_idx)

        with msg_col:
            with st.chat_message(m["role"]):
                if st.session_state.get("editing_msg_id") == msg_idx and m["role"] == "user":
                    edit_content = st.text_area("编辑消息", value=m["content"], key=f"edit_{msg_idx}", label_visibility="collapsed")
                    col_save, col_cancel = st.columns(2)
                    with col_save:
                        if st.button("直接发送", key=f"save_{msg_idx}"):
                            st.session_state.messages[msg_idx]["content"] = st.session_state[f"edit_{msg_idx}"]
                            st.session_state.pending_user_message = st.session_state[f"edit_{msg_idx}"]
                            st.session_state.editing_msg_id = None
                            st.rerun()
                    with col_cancel:
                        if st.button("❌ 取消", key=f"cancel_{msg_idx}"):
                            st.session_state.editing_msg_id = None
                            st.rerun()
                else:
                    st.markdown(render_message_content(m["content"]), unsafe_allow_html=True)
                    if st.session_state.get("tts_enabled") and m["role"] == "assistant":
                        try:
                            from CCR.ccr import text_to_speech
                            text_to_speech(m["content"])
                        except:
                            pass

            # ===== 操作按钮：紧跟消息底部5px，编辑模式下隐藏 =====
            if not (st.session_state.get("editing_msg_id") == msg_idx and m["role"] == "user"):
                st.markdown('<div style="height:5px;"></div>', unsafe_allow_html=True)
                if m["role"] == "user":
                    ac1, ac2, ac3 = st.columns([0.9, 0.05, 0.05])
                    with ac2:
                        if st.button("📋", key=f"copy_{msg_idx}", help="复制此消息"):
                            try:
                                pyperclip.copy(m["content"])
                                st.toast("已复制到剪贴板", icon="✅")
                            except Exception as e:
                                st.toast(f"复制失败: {str(e)}", icon="❌")
                    with ac3:
                        if st.button("✎", key=f"editbtn_{msg_idx}", help="修改此消息"):
                            st.session_state.editing_msg_id = msg_idx
                            st.rerun()
                else:
                    ac1, ac2 = st.columns([0.95, 0.05])
                    with ac2:
                        if st.button("📋", key=f"copy_{msg_idx}", help="复制此消息"):
                            try:
                                pyperclip.copy(m["content"])
                                st.toast("已复制到剪贴板", icon="✅")
                            except Exception as e:
                                st.toast(f"复制失败: {str(e)}", icon="❌")

    st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state.model_mode == "auto":
        cooldown_until = st.session_state.get("cooldown_until", 0)
        now_ts = time.time()
        if cooldown_until > now_ts:
            sec_left = int(cooldown_until - now_ts)
            cooldown_bar = f"⏳ 全网冷却中：剩余 {sec_left} 秒"
        else:
            cooldown_bar = ""
        st.markdown(f'<div class="cooldown-bar" id="cooldown-bar" data-until="{cooldown_until}">{cooldown_bar}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="cooldown-bar" id="cooldown-bar" data-until="0"></div>', unsafe_allow_html=True)
