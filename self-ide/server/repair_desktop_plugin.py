#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Self-IDE 桌面插件后端配置修复（registry auto_repair 用）。

唯一职责：幂等地把 'self-ide' 写回 config.yaml plugins.enabled。
不碰其他任何配置键（与 enforce_config.py 职责分离）。

修复后仍需重启 gateway 才能重新挂载 /api/plugins/self-ide 路由
（挂载仅发生在 gateway 启动时）——此点记录在 known_failures 描述中。
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERMES_HOME = Path(__file__).resolve().parent.parent.parent / ".hermes"
CONFIG = HERMES_HOME / "config.yaml"

try:
    import yaml
except ImportError:
    print("FAIL: pyyaml 不可用")
    sys.exit(1)

cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
plugins = cfg.setdefault("plugins", {})
enabled = plugins.setdefault("enabled", [])

if "self-ide" in enabled:
    print("OK: plugins.enabled 已含 self-ide，无需修复")
    sys.exit(0)

# 备份后写回（与 enforce_config.py 同风格）
backup = CONFIG.with_suffix(f".yaml.bak.desktop_plugin_{datetime.now():%Y%m%d_%H%M%S}")
shutil.copy2(CONFIG, backup)

enabled.append("self-ide")
CONFIG.write_text(
    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=1000),
    encoding="utf-8",
)
print(f"OK: 已将 self-ide 写回 plugins.enabled（备份: {backup.name}）；重启 gateway 后生效")
sys.exit(0)
