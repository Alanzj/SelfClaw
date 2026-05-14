# main.py —— OpenClaw 模块化入口（侧边栏强制展开版）
import os, sys, time, json, streamlit as st, html
from openai import OpenAI
import httpx, requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from CCR.ccr import ccr_chat
from DS.config import *
from DS.tools import *
from DS.model_manager import *
from DS.chat_engine import (
    chat, try_fallback_chat, is_error_response, tools, execute_tool
)
from DS.ui import (
    get_theme_css, render_header, render_sidebar, render_model_manager, render_dialogue
)

sys.stdout.reconfigure(encoding='utf-8')
st.set_page_config(page_title="SelfClaw · 聚合智能系统", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")

if "initialized" not in st.session_state:
    st.session_state.initialized = True
    load_config_force()
    memory = load_user_memory()
    if memory:
        st.session_state.messages = memory
    # 加载持久化 UI 状态
    _ui_state = load_ui_state()
    st.session_state._persisted_ui_state = _ui_state

for var, default in [
    ("status", "就绪"), ("messages", []), ("display_start_idx", 0),
    ("model_mode", "auto"), ("selected_model", ""), ("current_used_model", ""),
    ("uploaded_files", []), ("operation_logs", []), ("last_log_id", 0),
    ("current_file_contexts", []), ("live_log_messages", ["[系统] OpenClaw v36.0 启动完成"]),
    ("node_info", {"region": "自动选择", "type": "直连", "latency": "0ms", "territory": "未知"}),
    ("show_model_manager", False), ("theme", "dark"), ("show_deploy_menu", False),
    ("memos_reachable", False), ("memos_status", "检查中"), ("memos_last_check", 0),
    ("fallback_round", 0), ("editing_model", None), ("editing_model_data", {}),
    ("show_help", False), ("last_health_check", 0),
    ("degrade_path", []), ("permanent_blacklist", {}), ("temporary_cooldown", {}),
    ("cooldown_until", 0.0), ("pending_user_message", ""), ("editing_msg_id", None),
    ("collected_msgs", []), ("show_collect_panel", False), ("show_plugin_panel", False),
    ("show_version_panel", False), ("show_file_panel", False),
    ("selected_msg_ids", set()),
    ("wechat_bound", False),
    ("installed_plugins_persist", set()),
]:
    if var not in st.session_state:
        st.session_state[var] = default

# 从持久化状态恢复面板开关和已安装插件
_persisted = st.session_state.get("_persisted_ui_state", {})
if _persisted.get("show_version_panel"):
    st.session_state.show_version_panel = True
if _persisted.get("show_plugin_panel"):
    st.session_state.show_plugin_panel = True
if _persisted.get("installed_plugins"):
    st.session_state.just_installed_plugins = set(_persisted["installed_plugins"])
    st.session_state.installed_plugins_persist = set(_persisted["installed_plugins"])
# 恢复 display_start_idx（新开对话的隐藏偏移量）
if _persisted.get("display_start_idx") is not None:
    st.session_state.display_start_idx = _persisted["display_start_idx"]

if not st.session_state.selected_model:
    auto = get_auto_models()
    if auto:
        st.session_state.selected_model = auto[0]["id"]
        st.session_state.current_used_model = st.session_state.selected_model

def main():
    # 安全调用可能不存在的函数
    try:
        load_system_extensions()
    except Exception as e:
        pass
    try:
        auto_upgrade_and_plugin_check()
    except Exception:
        pass

    try:
        memos_stat = refresh_memos_status()
    except Exception:
        st.session_state.memos_status = "未连接"

    try:
        refresh_node_info()
    except Exception:
        pass

    cur_theme = st.session_state.theme
    st.markdown(get_theme_css(), unsafe_allow_html=True)

    # 强制展开侧边栏的 JavaScript（解决被折叠后无法展开的问题）
    st.markdown("""
    <script>
    (function() {
        // 强制展开侧边栏
        function forceExpandSidebar() {
            var sidebar = document.querySelector('section[data-testid="stSidebar"]');
            if (sidebar) {
                sidebar.style.display = 'block';
                sidebar.style.visibility = 'visible';
                sidebar.style.opacity = '1';
                sidebar.style.width = '250px';
                sidebar.style.minWidth = '250px';
                sidebar.style.maxWidth = '250px';
                sidebar.style.transform = 'none';
                sidebar.style.marginLeft = '0';
                sidebar.style.position = 'relative';
                sidebar.style.left = '0';
            }
            // 尝试点击展开按钮
            var allButtons = document.querySelectorAll('button');
            for (var i = 0; i < allButtons.length; i++) {
                var label = allButtons[i].getAttribute('aria-label') || '';
                if (label.toLowerCase().indexOf('expand') !== -1 || label.toLowerCase().indexOf('sidebar') !== -1) {
                    allButtons[i].click();
                    break;
                }
            }
        }
        // 页面加载后执行
        if (document.readyState === 'complete') {
            setTimeout(forceExpandSidebar, 500);
        } else {
            window.addEventListener('load', function() {
                setTimeout(forceExpandSidebar, 500);
            });
        }
        // 也立即尝试一次
        setTimeout(forceExpandSidebar, 100);
    })();
    </script>
    """, unsafe_allow_html=True)

    render_header()
    render_sidebar()

    if st.session_state.show_model_manager:
        render_model_manager()

    if st.session_state.show_deploy_menu:
        st.markdown("""<div class="deploy-menu">
            <div class="deploy-menu-title">Deploy</div>
            <a href="https://vercel.com/new" target="_blank">Vercel</a>
            <a href="https://render.com" target="_blank">Render</a>
            <a href="https://railway.app" target="_blank">Railway</a>
            <a href="https://huggingface.co/spaces" target="_blank">HuggingFace</a>
        </div>""", unsafe_allow_html=True)

    render_dialogue()

    prompt = st.chat_input("输入问题 (Shift+Enter 换行)")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.pending_user_message = prompt
        save_user_memory(st.session_state.messages)
        st.rerun()

    if st.session_state.get("pending_user_message"):
        user_msg = st.session_state.pending_user_message
        st.session_state.pending_user_message = None
        file_ctx = st.session_state.current_file_contexts if st.session_state.current_file_contexts else None
        if st.session_state.model_mode == "auto":
            ok, resp, _ = try_fallback_chat(user_msg, file_ctx)
        else:
            ph = st.empty()
            ok, resp, _ = ccr_chat(st.session_state.selected_model, st.session_state.messages, ph, file_ctx)
            ph.empty()
        st.session_state.messages.append({"role": "assistant", "content": resp if ok else f"❌ {resp}"})
        save_user_memory(st.session_state.messages)
        st.rerun()

    st.markdown("""
    <style>
    /* 冷却倒计时样式 */
    .cooldown-bar { margin-top: 8px; }
    </style>
    """, unsafe_allow_html=True)

    # 用 st.components.v1.html 注入 JS（st.markdown 会过滤 script 标签）
    import streamlit.components.v1 as components
    components.html("""
    <script>
    // ===== 冷却倒计时 =====
    function updateCooldowns() {
        var now = Date.now()/1000;
        var side = parent.document.getElementById('cooldown-timer');
        if (side) {
            var until = parseFloat(side.getAttribute('data-until'));
            var left = Math.max(0, Math.ceil(until - now));
            if (left > 0) side.innerText = '⏳ 全网冷却中：剩余 ' + left + ' 秒';
            else side.innerText = '';
        }
        var bar = parent.document.getElementById('cooldown-bar');
        if (bar) {
            var until = parseFloat(bar.getAttribute('data-until'));
            var left = Math.max(0, Math.ceil(until - now));
            if (left > 0) bar.innerText = '⏳ 全网冷却中：剩余 ' + left + ' 秒';
            else bar.innerText = '';
        }
    }
    setInterval(updateCooldowns, 1000);

    // ===== 核心样式注入 =====
    function applyStyles() {
        var doc = parent.document;

        // 1. 顶部标题字体缩小15%
        var ht = doc.querySelector('.header-title');
        if (ht) ht.style.fontSize = '16px';

        // 2. Theme/Deploy 按钮间距
        var da = doc.querySelector('.header-deploy-area');
        if (da) da.style.gap = '9px';

        // 3. 控制面板图标扩大20%
        var sb = doc.querySelector('section[data-testid="stSidebar"]');
        if (sb) {
            sb.querySelectorAll('.control-btn-style button').forEach(function(b) {
                b.style.width='41px'; b.style.height='41px';
                b.style.fontSize='37px'; b.style.display='flex';
                b.style.alignItems='center'; b.style.justifyContent='center';
            });
            // 4. 语音按钮对齐
            sb.querySelectorAll('.voice-control-wrapper button').forEach(function(b) {
                b.style.marginTop='12px'; b.style.verticalAlign='bottom';
            });
            // 5. 文件上传文字颜色
            var up = sb.querySelector('[data-testid="stFileUploader"]');
            if (up) up.querySelectorAll('*').forEach(function(e){ e.style.color='#5F9EA0'; });
        }

        // 6. 对话标题栏
        var tb = doc.querySelector('.st-key-dialogue_title_bar');
        if (tb) {
            tb.style.top='75px'; tb.style.height='39px';
            // 7. 删除按钮
            var db = tb.querySelector('button');
            if (db) { db.style.marginRight='10px'; db.style.fontSize='12px'; }
        }

        // 8. 消息间距
        doc.querySelectorAll('.stChatMessage').forEach(function(m){ m.style.marginBottom='2px'; });

        // 9. 输入框
        var ci = doc.querySelector('[data-testid="stChatInput"]');
        if (ci) ci.style.marginTop='40px';
        var ta = doc.querySelector('textarea[aria-label="Chat input"]');
        if (ta) ta.style.marginTop='40px';

        // 10. 关闭管理面板按钮
        var mc = doc.querySelector('.mgr-close-panel');
        if (mc) {
            var cb = mc.querySelector('button');
            if (cb) {
                cb.style.background='#B8860B'; cb.style.color='#fff';
                cb.style.border='1px solid red'; cb.style.fontSize='11px';
                cb.style.transform='scale(0.73)'; cb.style.transformOrigin='left center';
            }
        }

        // 11. 模型管理标题间距
        var mt = doc.querySelector('.model-main-title');
        if (mt) { mt.style.marginTop='2px'; mt.style.marginBottom='3px'; }
        doc.querySelectorAll('.model-sub-title').forEach(function(e){
            e.style.marginTop='2px'; e.style.marginBottom='3px';
        });

        // 12. 模型列表行间距+居中
        doc.querySelectorAll('.model-list-row').forEach(function(r){
            r.style.marginBottom='0px'; r.style.padding='1px 0';
            r.style.display='flex'; r.style.alignItems='center'; r.style.minHeight='10px';
        });
    }

    // 延迟 + Observer 双保险
    setTimeout(applyStyles, 300);
    setTimeout(applyStyles, 1000);
    setTimeout(applyStyles, 2000);
    setTimeout(applyStyles, 5000);

    var obs = new MutationObserver(function(){ setTimeout(applyStyles, 100); });
    obs.observe(parent.document.body, {childList:true, subtree:true});
    </script>
    """, height=0)

if __name__ == "__main__":
    os.environ['STREAMLET_SERVER_HEADLESS'] = "true"
    main()