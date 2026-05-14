# model_manager.py — 模型增删改查、可用列表、黑名单/冷却、日志、属地判断
import os, time, yaml
from urllib.parse import urlparse
from datetime import datetime
from DS.config import (
    get_config, save_config, load_config_force,
    SAFE_MODEL_WHITELIST, PERMANENT_ERROR_KEYWORDS, TEMPORARY_ERROR_KEYWORDS
)

CUTECLOUD_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cutecloud", "config.yaml"
)


def load_rules():
    try:
        with open(CUTECLOUD_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        raw_rules = config.get("rules", [])
        rules = []
        for entry in raw_rules:
            if not isinstance(entry, str):
                continue
            parts = entry.split(",")
            if len(parts) < 3:
                continue
            rule_type = parts[0].strip()
            value = parts[1].strip()
            policy = parts[2].strip()
            rules.append((rule_type, value, policy))
        return rules, None
    except Exception as e:
        return None, str(e)


def get_territory_by_provider(provider):
    cfg = get_config()
    prov = cfg.get("providers", {}).get(provider)
    if not prov:
        return "未知"
    base_url = prov.get("baseUrl", "")
    domain = urlparse(base_url).hostname or ""
    if not domain:
        return "未知"

    rules, err = load_rules()
    if err or not rules:
        dd = {"api.siliconflow.cn", "api.deepseek.com", "api.zenmux.ai", "dashscope.aliyun.com", "open.bigmodel.cn", "integrate.api.nvidia.com"}
        ds = {"siliconflow.cn", "deepseek.com", "zenmux.ai", "aliyun.com", "aliyuncs.com", "bigmodel.cn", "nvidia.cn", "xiaomimimo.com"}
        if domain in dd:
            return "境内"
        for suffix in ds:
            if domain.endswith(suffix) or domain == suffix:
                return "境内"
        return "境外"

    for rule_type, value, policy in rules:
        matched = False
        if rule_type == "DOMAIN":
            if domain == value:
                matched = True
        elif rule_type == "DOMAIN-SUFFIX":
            if domain.endswith(value) or domain == value:
                matched = True
        elif rule_type == "DOMAIN-KEYWORD":
            if value in domain:
                matched = True
        if matched:
            return "境内" if policy == "DIRECT" else "境外"
    return "境外"


def add_operation_log(msg, tp="info"):
    import streamlit as st
    try:
        log_id = st.session_state.last_log_id + 1
        st.session_state.last_log_id = log_id
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state.operation_logs.append({"id": log_id, "time": ts, "type": tp, "message": str(msg)})
        if "调用成功" in msg or "永久故障" in msg or "额度不足" in msg or "模型不存在" in msg or "密钥无效" in msg or "全部模型失败" in msg or "可升级" in msg:
            display_msg = f"[{ts}] {msg}"
            st.session_state.live_log_messages.append(display_msg)
            if len(st.session_state.live_log_messages) > 3:
                st.session_state.live_log_messages.pop(0)
    except:
        pass


def get_models_pool():
    models = get_config().get("models_pool", [])
    sorted_models = sorted(models, key=lambda x: x.get("order", 999))
    for i, m in enumerate(sorted_models):
        m["order"] = i + 1
    return sorted_models


def get_auto_models():
    return [m for m in get_models_pool() if m.get("type") == "auto"]


def get_current_use_model_info():
    cfg = get_config()
    all_models = cfg.get("models_pool", [])
    import streamlit as st
    current_mid = st.session_state.get("current_used_model", st.session_state.selected_model)
    for item in all_models:
        if item.get("id") == current_mid:
            return item
    return None


def is_current_in_top3():
    auto_list = get_auto_models()
    if len(auto_list) < 3:
        return False
    top3_ids = [m["id"] for m in auto_list[:3]]
    import streamlit as st
    current_id = st.session_state.get("current_used_model", "")
    return current_id in top3_ids


def simplify_model_name(mid):
    for m in get_models_pool():
        if m["id"] == mid:
            return m["label"]
    if ":" in mid:
        parts = mid.split(":", 1)
        return parts[0] + ":" + parts[-1].replace(":free", "")
    return mid


def get_model_info(mid):
    for m in get_models_pool():
        if m["id"] == mid:
            return m
    return None


def add_model_to_config(m):
    cfg = get_config()
    models = cfg.get("models_pool", [])
    if any(mm["id"] == m["id"] for mm in models):
        return False, "ID存在"
    if not m.get("order"):
        m["order"] = max([mm.get("order", 0) for mm in models], default=0) + 1
    models.append(m)
    cfg["models_pool"] = models
    if save_config(cfg):
        load_config_force()
        return True, "添加成功"
    return False, "保存失败"


def delete_model_from_config(mid):
    cfg = get_config()
    models = cfg.get("models_pool", [])
    new_models = [mm for mm in models if mm["id"] != mid]
    if len(new_models) == len(models):
        return False, "未找到"
    cfg["models_pool"] = new_models
    if save_config(cfg):
        load_config_force()
        return True, "删除成功"
    return False


def update_model_in_config(mid, new_data):
    cfg = get_config()
    models = cfg.get("models_pool", [])
    for i, mm in enumerate(models):
        if mm["id"] == mid:
            models[i].update(new_data)
            break
    else:
        return False, "未找到"
    if save_config(cfg):
        load_config_force()
        return True, "更新成功"
    return False


def reorder_models_in_config(mid, direction):
    cfg = get_config()
    models = cfg.get("models_pool", [])
    sm = sorted(models, key=lambda x: x.get("order", 999))
    idx = next((i for i, mm in enumerate(sm) if mm["id"] == mid), -1)
    if idx == -1:
        return False, "未找到"
    if direction == "up" and idx > 0:
        sm[idx], sm[idx - 1] = sm[idx - 1], sm[idx]
    elif direction == "down" and idx < len(sm) - 1:
        sm[idx], sm[idx + 1] = sm[idx + 1], sm[idx]
    else:
        return False, "无法移动"
    for i, mm in enumerate(sm):
        mm["order"] = i + 1
    cfg["models_pool"] = sm
    if save_config(cfg):
        load_config_force()
        return True, "顺序调整成功"
    return False


def add_to_blacklist(model_id, error_type):
    import streamlit as st
    if model_id in SAFE_MODEL_WHITELIST:
        return
    now = time.time()
    if "permanent_blacklist" not in st.session_state:
        st.session_state.permanent_blacklist = {}
    if "temporary_cooldown" not in st.session_state:
        st.session_state.temporary_cooldown = {}
    if error_type == "permanent":
        st.session_state.permanent_blacklist[model_id] = now + 86400
        add_operation_log(f"[永久故障] {simplify_model_name(model_id)} | 24小时跳过", "error")
    else:
        st.session_state.temporary_cooldown[model_id] = now + 60
        add_operation_log(f"[临时冷却] {simplify_model_name(model_id)} | 60s", "warning")


def is_model_available(model_id):
    import streamlit as st
    now = time.time()
    if model_id in SAFE_MODEL_WHITELIST:
        return True
    if model_id in st.session_state.get("permanent_blacklist", {}):
        if st.session_state.permanent_blacklist[model_id] > now:
            return False
        else:
            del st.session_state.permanent_blacklist[model_id]
    if model_id in st.session_state.get("temporary_cooldown", {}):
        if st.session_state.temporary_cooldown[model_id] > now:
            return False
        else:
            del st.session_state.temporary_cooldown[model_id]
    return True


def get_available_models():
    auto_models = get_auto_models()
    auto_ids = {m["id"] for m in auto_models}
    all_models = get_models_pool()
    for mid in SAFE_MODEL_WHITELIST:
        if mid not in auto_ids:
            model = next((m for m in all_models if m["id"] == mid), None)
            if model:
                auto_models.append(model)
                auto_ids.add(mid)
    available = [m for m in auto_models if is_model_available(m["id"])]
    whitelist_models = [m for m in available if m["id"] in SAFE_MODEL_WHITELIST]
    other_models = [m for m in available if m["id"] not in SAFE_MODEL_WHITELIST]
    whitelist_models.sort(key=lambda x: SAFE_MODEL_WHITELIST.index(x["id"]))
    return whitelist_models + other_models
