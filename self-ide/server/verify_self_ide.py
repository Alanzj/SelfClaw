#!/usr/bin/env python3
"""verify_self_ide.py — Self-IDE 端到端回归验证（含 VS Code 升级模拟）

用法：
  python verify_self_ide.py            # 全量验证
  python verify_self_ide.py --quick    # 跳过真实推理（快速巡检，供 watchdog 调用）

退出码：0=全通过  1=有失败项
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", str(Path.home() / "hermes")))
# [2026-09-24 fix-pluginpath] 根因：插件源码已从 hermes_overlay/self_ide/plugin 迁至仓库根 self-ide-vscode-plugin/，
#   本常量未跟迁 → --quick 巡检 2 项 [Errno 2] 假红（14/16）。仅改常量，检查逻辑未动。
# ⚠ 经老板批准（2026-09-24 三项裁定 ①），严禁擅自
PLUGIN_DIR = HERMES_ROOT / "self-ide-vscode-plugin"
SERVER_PY = HERMES_ROOT / "hermes_overlay" / "self_ide" / "self_ide_server.py"
VSCODE_CLI = r"C:\Program Files\Microsoft VS Code\bin\code.cmd"
EXT_DIR = Path(os.environ.get("USERPROFILE", "")) / ".vscode" / "extensions"
SELF_IDE_PORT = 19898

# R-9：本地回环强制 bypass 代理（教训 #107/#111）
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

QUICK = "--quick" in sys.argv
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    return ok


def get(url, timeout=30):
    with OPENER.open(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


# ── 1. 升级免疫：目录物理隔离 ──────────────────────────────
prog = Path(r"C:\Program Files\Microsoft VS Code")
check("升级免疫·目录隔离",
      EXT_DIR.exists() and prog.exists() and str(prog) not in str(EXT_DIR),
      f"扩展={EXT_DIR} 独立于程序目录")

# ── 2. 升级免疫：Self-IDE 代码在非 git 仓库目录（铁律 #158）──
try:
    r = subprocess.run(["git", "check-ignore", "-q", str(SERVER_PY)],
                       cwd=str(HERMES_ROOT), capture_output=True, timeout=20)
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch",
                              "hermes_overlay/self_ide/self_ide_server.py"],
                             cwd=str(HERMES_ROOT), capture_output=True, timeout=20)
    check("升级免疫·非仓库目录", tracked.returncode != 0,
          "self_ide_server.py 未被 git 跟踪，升级不覆盖")
except (OSError, subprocess.SubprocessError) as e:
    check("升级免疫·非仓库目录", False, str(e))

# ── 3. 升级免疫：API 白名单（零 deprecated / 零 proposed）──
ALLOWED = {"registerCommand", "createStatusBarItem", "createWebviewPanel",
           "executeCommand", "executeDocumentSymbolProvider",
           "getConfiguration", "showInputBox",
           "onDidChangeActiveTextEditor", "showInformationMessage",
           "showWarningMessage", "showErrorMessage", "activeTextEditor",
           "version", "ViewColumn", "StatusBarAlignment", "SymbolKind",
           "DocumentSymbol", "StatusBarItem", "WebviewPanel",
           "TextEditor", "ExtensionContext", "workspace",
           "window", "commands", "Uri"}
try:
    src = (PLUGIN_DIR / "src" / "extension.ts").read_text("utf-8")
    used = set(re.findall(r"vscode\.(?:window\.|workspace\.|commands\.)?(\w+)", src))
    unknown = used - ALLOWED
    # proposed API 只可能声明在 package.json，不在源码注释里（避免注释误报）
    pkg_raw = (PLUGIN_DIR / "package.json").read_text("utf-8")
    has_proposed = "enabledApiProposals" in pkg_raw or "enableProposedApi" in pkg_raw
    check("升级免疫·API 白名单", not unknown and not has_proposed,
          f"{len(used)} 个 API 全部 stable" if not unknown else f"未知 API: {unknown}")
except OSError as e:
    check("升级免疫·API 白名单", False, str(e))

# ── 4. 升级模拟：当前 VS Code 版本 vs engines 声明 ──────────
try:
    out = subprocess.run([VSCODE_CLI, "--version"], capture_output=True,
                         text=True, timeout=60).stdout
    cur = out.strip().splitlines()[0]
    pkg = json.loads((PLUGIN_DIR / "package.json").read_text("utf-8"))
    req = pkg["engines"]["vscode"].lstrip("^~")

    def sv(s):
        return [int(x) for x in (s.split("-")[0].split(".") + ["0", "0"])[:3]]

    ok = sv(cur) >= sv(req)
    # 模拟未来大版本升级（+10 个 minor）
    future = f"{sv(cur)[0]}.{sv(cur)[1] + 10}.0"
    future_ok = sv(future) >= sv(req)
    check("升级模拟·版本兼容", ok and future_ok,
          f"当前 {cur} ≥ ^{req}；模拟升级到 {future} 仍兼容")
except (OSError, subprocess.SubprocessError, KeyError, ValueError, IndexError) as e:
    check("升级模拟·版本兼容", False, str(e))

# ── 5. 扩展已安装 ──────────────────────────────────────────
try:
    exts = subprocess.run([VSCODE_CLI, "--list-extensions"],
                          capture_output=True, text=True, timeout=60).stdout
    check("扩展已安装", "hermes-agent.self-ide" in exts, "hermes-agent.self-ide")
except (OSError, subprocess.SubprocessError) as e:
    check("扩展已安装", False, str(e))

# ── 6. 隔离铁律：无产物写入隔离外置目录（ISOLATION_GUARD_DIR） ──
myai = Path(os.environ.get("ISOLATION_GUARD_DIR", str(Path.home() / "isolated-models")))
leaks = []
if myai.exists():
    for pat in (".self-ide", "self_ide*", "self-ide*"):
        leaks += [str(p) for p in myai.glob(pat)]
check("隔离铁律·零污染隔离目录", not leaks, "无 Self-IDE 产物" if not leaks else str(leaks))

# ── 7. 端口铁律：未占用保留端口 ────────────────────────────
RESERVED = {9119: "Dashboard", 19899: "SelfClaw", 19996: "Feishu",
            19997: "Supervisor", 9120: "ConnTest", 3410: "Electron"}
check("端口铁律·无冲突", SELF_IDE_PORT not in RESERVED,
      f"Self-IDE={SELF_IDE_PORT}，保留端口 {sorted(RESERVED)} 未占用")

# ── 8. 服务端存活 + 深度探活（R-10：不只探端口）───────────
try:
    url = f"http://127.0.0.1:{SELF_IDE_PORT}/health" + ("" if QUICK else "?deep=1")
    status, h = get(url, timeout=90)
    check(f"服务端 :{SELF_IDE_PORT}", status == 200, f"HTTP {status}")
    for c in h.get("checks", []):
        check(f"  └ {c['name']}", c["ok"], c["detail"])
except (OSError, ValueError) as e:
    check(f"服务端 :{SELF_IDE_PORT}", False, str(e))

# ── 9. 双轨队列 ────────────────────────────────────────────
try:
    _, q = get(f"http://127.0.0.1:{SELF_IDE_PORT}/queue", timeout=20)
    refs = q.get("references") or []
    all_have_fb = all(r.get("fallback") for r in refs)
    check("双轨队列", bool(refs) and q.get("aggregator") is not None,
          f"mode={q['mode']} agg={q['aggregator']['model']} refs={len(refs)}")
    check("  └ fallback 链齐备", all_have_fb,
          f"每线 {len(refs[0]['fallback']) if refs else 0} 位备链")
    # 聚合器兜底非空（2026-08-14：红灯漏网项 —— 聚合器在 reference_chains 无 key
    # 时须由 moa_state.aggregator_fallback 提供兜底；此前 fallback 为空却不报，
    # 导致 19898 /health model_queue 一直 false）
    agg_fb = (q.get("aggregator") or {}).get("fallback") or []
    check("  └ 聚合器兜底非空", bool(agg_fb),
          f"agg={q['aggregator']['model']} fallback={len(agg_fb)} 位"
          + (f" → {' | '.join(agg_fb[:3])}" if agg_fb else " ⚠️ 空链（watchdog 会红灯）"))
except (OSError, ValueError, KeyError, TypeError) as e:
    check("双轨队列", False, str(e))

# ── 10. Quant-Router 分流 ─────────────────────────────────
CASES = [("engine/backtest.py", "deterministic"), ("k/m.cu", "deterministic"),
         ("README.md", "fast"), ("src/a.ts", "autocomplete"), ("src/a.js", "autocomplete")]
try:
    ok = True
    detail = []
    for f, want in CASES:
        req_ = urllib.request.Request(
            f"http://127.0.0.1:{SELF_IDE_PORT}/route", method="POST",
            data=json.dumps({"file": f}).encode(),
            headers={"Content-Type": "application/json"})
        with OPENER.open(req_, timeout=20) as r:
            d = json.loads(r.read().decode())
        ok &= (d["tier"] == want)
        detail.append(f"{f}→{d['tier']}")
    check("Quant-Router 分流", ok, " | ".join(detail))
except (OSError, ValueError, KeyError) as e:
    check("Quant-Router 分流", False, str(e))

# ── 11. Cost-Meter ────────────────────────────────────────
try:
    _, c = get(f"http://127.0.0.1:{SELF_IDE_PORT}/cost", timeout=15)
    t = c.get("total", {})
    check("Cost-Meter", "total" in c,
          f"{t.get('calls', 0)} 次 / {t.get('prompt', 0)}+{t.get('completion', 0)} tokens")
except (OSError, ValueError) as e:
    check("Cost-Meter", False, str(e))

# ── 汇总 ──────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"\n{'=' * 62}\nSelf-IDE 回归验证: {passed}/{total} 通过"
      f"{'（--quick 模式，跳过真实推理）' if QUICK else ''}\n{'=' * 62}")
sys.exit(0 if passed == total else 1)
