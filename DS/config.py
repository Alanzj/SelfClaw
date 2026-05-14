# config.py — 全局路径、常量、颜色、配置读写
import os, json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "openclaw.json")
MEMORY_PATH = os.path.join(BASE_DIR, "user_memory.json")
BACKUP_DIR = os.path.join(BASE_DIR, "Backup")
KEYS_ENV_PATH = os.path.join(BASE_DIR, "keys.env")
EXTENSIONS_DIR = os.path.join(BASE_DIR, ".openclaw", "extensions")
GATEWAY = "http://127.0.0.1:19999/v1"
CURRENT_VERSION = "v36.0"
VERSION_CHECK_URL = "https://pypi.org/pypi/streamlit/json"

SAFE_MODEL_WHITELIST = [
    "deepseek:v4-flash-paid",
    "siliconflow:glm-4-9b",
    "nvidiaD:step-3.5-flash"
]

PERMANENT_ERROR_KEYWORDS = [
    "model not found", "does not exist", "invalid API key",
    "quota exhausted", "payment required", "401", "403", "404", "429"
]
TEMPORARY_ERROR_KEYWORDS = [
    "rate limit", "timeout", "500", "502", "503", "504"
]

# 天青色标题 / 青灰色正文
COLOR_TITLE = "#4EC9C0"
COLOR_TEXT = "#5A9E99"
COLOR_WARM = "#D4B86A"
MAX_FILE_CONTENT_LENGTH = 10000


def load_config():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"gateway": {}, "providers": {}, "models_pool": [], "plugins": {}}


def save_config(cfg):
    try:
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR, exist_ok=True)
        if os.path.exists(CONFIG_PATH):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(CONFIG_PATH, "r", encoding="utf-8") as src, open(os.path.join(BACKUP_DIR, f"openclaw_backup_{ts}.json"), "w", encoding="utf-8") as dst:
                dst.write(src.read())
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_config_force():
    import streamlit as st
    st.session_state._config = load_config()


def get_config():
    cfg = load_config()
    import streamlit as st
    st.session_state._config = cfg
    return cfg
