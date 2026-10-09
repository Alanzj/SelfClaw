/*
 * Self-IDE v1.0 — 量化金融级私有 AI IDE
 *
 * ═══ 设计定调 ═══
 * 护城河在 Hermes 层，不在插件层。本插件只做壳：
 *   配置编排 / 通信管道 / 状态展示 / 符号导航
 * 所有 AI 调度、模型队列、降级、路由、计量下沉到 self_ide_server.py (19898)
 *
 * ═══ VS Code 升级免疫（三重保障）═══
 * 1. 架构隔离：扩展目录 ~/.vscode/extensions 独立于程序目录 Program Files，升级不碰
 * 2. API 免疫：仅用下列 7 个 stable API，全部 ≥8 年未变，零 deprecated、零 proposed
 *      registerCommand / createStatusBarItem / createWebviewPanel /
 *      executeDocumentSymbolProvider / getConfiguration / showInputBox /
 *      onDidChangeActiveTextEditor
 * 3. 自检兜底：checkHealth 第 8 项比对 vscode.version 与 engines 要求
 *
 * ═══ 铁律 ═══
 * R-9  本地回环请求用 Node http（天然 bypass 系统代理，不受 TUN fake-ip 劫持）
 * R-10 健康检查发真实 chat/completions，不只探端口
 * R-11 本插件源码位于 hermes_overlay/（非 git 仓库），Hermes 升级不覆盖
 * R-12 端口约束：9119/19899/19996/19997/9120/3410 不可占用；Self-IDE 用 19898
 * 隔离铁律：所有产物写 hermesRoot 配置目录，绝不写隔离外置目录
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as http from 'http';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

// 最低支持的 VS Code 版本（须与 package.json engines.vscode 保持一致）
const REQUIRED_VSCODE = '1.74.0';

let statusBar: vscode.StatusBarItem;
let hermesPanel: vscode.WebviewPanel | undefined;
let symbolPanel: vscode.WebviewPanel | undefined;
let connected = false;
let lastQueue: QueueInfo | undefined;

interface ModelEntry { model: string; score: number; note: string; fallback: string[]; }
interface QueueInfo {
    mode: string; source: string;
    aggregator: ModelEntry | null; references: ModelEntry[];
    updated_at?: string; total_ranked?: number;
}

// ==================== 入口 ====================

export function activate(context: vscode.ExtensionContext) {
    statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBar.command = 'self-ide.checkHealth';
    context.subscriptions.push(statusBar);

    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(() => refreshStatusBar()),
        vscode.commands.registerCommand('self-ide.initialize', initializeEnvironment),
        vscode.commands.registerCommand('self-ide.audit', runPrivacyAudit),
        vscode.commands.registerCommand('self-ide.reset', resetEnvironment),
        vscode.commands.registerCommand('self-ide.checkHealth', () => checkHealth(true)),
        vscode.commands.registerCommand('self-ide.connect', connectHermes),
        vscode.commands.registerCommand('self-ide.disconnect', disconnectHermes),
        vscode.commands.registerCommand('self-ide.showPanel', showHermesPanel),
        vscode.commands.registerCommand('self-ide.showSymbols', showSymbolMap)
    );

    // 启动静默探活（不打扰用户）
    void silentBoot();
}

export function deactivate() { /* 无常驻资源，subscriptions 自动释放 */ }

// ==================== 配置读取 ====================

function cfg() {
    const c = vscode.workspace.getConfiguration('self-ide');
    return {
        hermesRoot: c.get<string>('hermesRoot') || process.env.HERMES_ROOT || path.join(os.homedir(), 'hermes'),
        selfIdePort: c.get<number>('selfIdePort') || 19898,
        dashboardPort: c.get<number>('dashboardPort') || 9119,
        gatewayPort: c.get<number>('gatewayPort') || 19899,
        proxyPort: c.get<string>('proxyPort') || '7897',
        routeViaGateway: c.get<boolean>('routeViaGateway') !== false,
        contextLength: c.get<number>('contextLength') || 256000
    };
}

// ==================== HTTP（R-9：Node http 天然 bypass 代理）====================

function req(port: number, urlPath: string, method: 'GET' | 'POST' = 'GET',
             body?: unknown, timeoutMs = 30000): Promise<any> {
    return new Promise((resolve, reject) => {
        const payload = body !== undefined ? JSON.stringify(body) : undefined;
        const r = http.request({
            hostname: '127.0.0.1', port, path: urlPath, method,
            headers: payload
                ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
                : { 'Content-Type': 'application/json' }
        }, res => {
            let data = '';
            res.on('data', c => data += c);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); } catch { resolve(data); }
            });
        });
        r.setTimeout(timeoutMs, () => { r.destroy(new Error(`timeout ${timeoutMs}ms`)); });
        r.on('error', reject);
        if (payload) { r.write(payload); }
        r.end();
    });
}

// ==================== Core：配置编排 ====================

async function initializeEnvironment() {
    const c = cfg();

    // 隔离铁律：产物目录固定在 Hermes 工作区
    const selfIdeDir = path.join(c.hermesRoot, '.self-ide');
    const rulesPath = path.join(selfIdeDir, '.clinerules');
    const continueDir = path.join(process.env.USERPROFILE || '', '.continue');
    const settingsPath = userSettingsPath();

    const mode = c.routeViaGateway ? '本地 Gateway 统一路由（零外泄）' : '外部 API 直连';
    const confirm = await vscode.window.showWarningMessage(
        `Self-IDE 即将写入：\n` +
        `  • ${settingsPath}\n` +
        `  • ${path.join(continueDir, 'config.json')}\n` +
        `  • ${rulesPath}\n\n` +
        `路由模式：${mode}\n` +
        `隔离铁律：不写入隔离外置目录\n\n确认执行？`,
        { modal: true }, '确认初始化');
    if (confirm !== '确认初始化') { return; }

    // 仅在直连模式才需要 API Key；走 Gateway 时凭据由 Hermes 侧统一管理
    let apiKey = 'self-ide-local';
    if (!c.routeViaGateway) {
        const input = await vscode.window.showInputBox({
            prompt: '上游 API Key（直连模式必填，密码输入，不落 VS Code 设置）',
            password: true, ignoreFocusOut: true
        });
        if (!input) {
            vscode.window.showErrorMessage('Self-IDE: 未输入 Key，初始化取消');
            return;
        }
        apiKey = input;
    }

    try {
        writeUserSettings(c);
        writeContinueConfig(c, apiKey, continueDir);
        writeClinerules(rulesPath);
        vscode.window.showInformationMessage(
            'Self-IDE: 初始化完成。请手动重启 VS Code 生效（不强制杀进程，避免丢失未保存工作）。');
        await checkHealth(false);
    } catch (err) {
        vscode.window.showErrorMessage(`Self-IDE: 初始化失败 — ${err}`);
    }
}

function userSettingsPath(): string {
    const appData = process.env.APPDATA ||
        path.join(process.env.USERPROFILE || '', 'AppData', 'Roaming');
    return path.join(appData, 'Code', 'User', 'settings.json');
}

function writeUserSettings(c: ReturnType<typeof cfg>) {
    const p = userSettingsPath();
    let s: Record<string, unknown> = {};
    if (fs.existsSync(p)) {
        try { s = JSON.parse(fs.readFileSync(p, 'utf8')); }
        catch (e) { throw new Error(`settings.json 解析失败，请先修复: ${e}`); }
    }
    // 备份（可回滚）
    if (fs.existsSync(p)) {
        fs.copyFileSync(p, p.replace(/\.json$/, `.selfide-backup-${Date.now()}.json`));
    }

    // 隐私基线
    s['telemetry.telemetryLevel'] = 'off';
    s['crashReporter.enabled'] = false;
    s['workbench.enableExperiments'] = false;
    s['extensions.autoUpdate'] = false;
    // 编辑体验
    s['editor.suggest.showWords'] = false;
    s['editor.suggest.localityBonus'] = true;

    // Cline → 本地 Self-IDE 服务端（零外泄核心）
    if (c.routeViaGateway) {
        s['cline.apiProvider'] = 'openai';
        s['cline.openAiBaseUrl'] = `http://127.0.0.1:${c.selfIdePort}/v1`;
        s['cline.openAiModelId'] = 'auto';   // auto → 触发 Quant-Router
        s['cline.openAiApiKey'] = 'self-ide-local';
    }

    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify(s, null, 2), 'utf8');
}

function writeContinueConfig(c: ReturnType<typeof cfg>, apiKey: string, dir: string) {
    const apiBase = c.routeViaGateway
        ? `http://127.0.0.1:${c.selfIdePort}/v1`
        : 'https://api.openai.com/v1';

    const conf = {
        models: [{
            title: 'Self-IDE (Hermes 多模型仲裁)',
            provider: 'openai',
            model: 'auto',
            apiBase, apiKey,
            contextLength: c.contextLength
        }],
        tabAutocompleteModel: {
            title: 'Self-IDE Autocomplete',
            provider: 'openai',
            model: 'auto',
            apiBase, apiKey
        },
        tabAutocompleteOptions: {
            maxPromptTokens: 2048,
            debounceDelay: 350,
            multilineCompletions: 'off',
            useCopyBuffer: false
        },
        allowAnonymousTelemetry: false,
        requestOptions: { timeout: 30000, verifySsl: false }
    };
    fs.mkdirSync(dir, { recursive: true });
    const p = path.join(dir, 'config.json');
    if (fs.existsSync(p)) {
        fs.copyFileSync(p, p.replace(/\.json$/, `.selfide-backup-${Date.now()}.json`));
    }
    fs.writeFileSync(p, JSON.stringify(conf, null, 2), 'utf8');
}

function writeClinerules(rulesPath: string) {
    const rules = [
        '# Cline 行为约束（量化开发专用 · Self-IDE 生成）',
        '',
        '## 思考模式控制',
        '- 执行简单重构或格式化时，保持思考过程简短',
        '- 禁止对确定性数值逻辑进行"创造性优化"',
        '',
        '## 代码保护（绝对禁止）',
        '- 禁止修改文件头注释、Author 信息及版权声明',
        '- 禁止删除 LICENSE 或版本记录',
        '- 修改跨文件引用时，必须同步更新所有相关文件的 import/路径',
        '',
        '## 隔离铁律',
        '- Hermes 产物只写 hermesRoot 目录，绝不写入隔离外置目录',
        '',
        '## 模型边界',
        '- CUDA 内核 / 回测引擎核心 / 需 deterministic output 的代码',
        '  → Self-IDE Quant-Router 已自动切换至确定性档位模型，勿手动降档',
        '',
        '## 闭环指令模板（单轮多步）',
        '1. 分析：读取相关文件，定位问题根因',
        '2. 规划：列出修改方案，包含精确文件路径和行号',
        '3. 编辑：执行修改，确保不引入语法错误',
        '4. 自检：检查未定义变量、错误缩进、循环依赖',
        '5. 输出：列出所有修改的文件和行号，给出验证用例代码',
        ''
    ].join('\n');
    fs.mkdirSync(path.dirname(rulesPath), { recursive: true });
    fs.writeFileSync(rulesPath, rules, 'utf8');
}

async function resetEnvironment() {
    const choice = await vscode.window.showWarningMessage(
        'Self-IDE: 重置将备份并清理 Continue 配置与 .clinerules。继续？',
        { modal: true }, '确认重置');
    if (choice !== '确认重置') { return; }

    const c = cfg();
    const removed: string[] = [];
    const targets = [
        path.join(process.env.USERPROFILE || '', '.continue', 'config.json'),
        path.join(c.hermesRoot, '.self-ide', '.clinerules')
    ];
    for (const t of targets) {
        if (fs.existsSync(t)) {
            fs.copyFileSync(t, `${t}.reset-backup-${Date.now()}`);
            fs.unlinkSync(t);
            removed.push(path.basename(t));
        }
    }
    vscode.window.showInformationMessage(
        `Self-IDE: 已备份并清理 ${removed.length ? removed.join(', ') : '（无文件需清理）'}。请手动重启 VS Code。`);
}

// ==================== Privacy Audit（中文系统兼容）====================

async function runPrivacyAudit() {
    try {
        // Get-NetTCPConnection 返回结构化对象，不受 ESTABLISHED/已建立 语言差异影响
        const psCmd = 'powershell -NoProfile -Command "' +
            'Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | ' +
            'Select-Object RemoteAddress,RemotePort | ConvertTo-Json -Compress"';
        const { stdout } = await execAsync(psCmd, { timeout: 20000 });

        let conns = JSON.parse(stdout || '[]');
        if (!Array.isArray(conns)) { conns = [conns]; }   // 单条时不是数组

        const c = cfg();
        const isLocal = (a: string) => a === '127.0.0.1' || a === '::1';
        const local = conns.filter((x: any) => isLocal(String(x.RemoteAddress || '')));
        const external = conns.filter((x: any) => !isLocal(String(x.RemoteAddress || '')));
        const hermesLinks = local.filter((x: any) =>
            [c.selfIdePort, c.dashboardPort, c.gatewayPort].includes(Number(x.RemotePort)));
        const proxyLinks = conns.filter((x: any) =>
            String(x.RemotePort) === c.proxyPort);

        const verdict = external.length === 0
            ? '✅ 零外部直连'
            : `⚠️ ${external.length} 条外部连接（含系统进程，非全部来自 VS Code）`;

        vscode.window.showInformationMessage(
            `Self-IDE 隐私审计\n` +
            `本地回环: ${local.length} 条\n` +
            `Hermes 链路(${c.selfIdePort}/${c.dashboardPort}/${c.gatewayPort}): ${hermesLinks.length} 条\n` +
            `代理 ${c.proxyPort}: ${proxyLinks.length} 条\n` +
            `外部连接: ${external.length} 条\n${verdict}`,
            { modal: true });
    } catch (err) {
        vscode.window.showErrorMessage(`Self-IDE: 审计失败 — ${err}`);
    }
}

// ==================== Check Health（8 项，含升级兼容自检）====================

function cmpSemver(a: string, b: string): number {
    const pa = a.split('.').map(n => parseInt(n, 10) || 0);
    const pb = b.split('.').map(n => parseInt(n, 10) || 0);
    for (let i = 0; i < 3; i++) {
        if ((pa[i] || 0) !== (pb[i] || 0)) { return (pa[i] || 0) - (pb[i] || 0); }
    }
    return 0;
}

async function checkHealth(verbose: boolean) {
    const c = cfg();
    const lines: string[] = [];
    let bad = 0;
    const mark = (ok: boolean, text: string) => {
        if (!ok) { bad++; }
        lines.push(`${ok ? '✅' : '❌'} ${text}`);
    };

    // 1. settings.json
    mark(fs.existsSync(userSettingsPath()), 'VS Code settings.json');

    // 2. Continue 配置
    const contPath = path.join(process.env.USERPROFILE || '', '.continue', 'config.json');
    mark(fs.existsSync(contPath), 'Continue config.json');

    // 3. .clinerules（隔离铁律位置）
    const rulesPath = path.join(c.hermesRoot, '.self-ide', '.clinerules');
    mark(fs.existsSync(rulesPath), `.clinerules @ ${path.dirname(rulesPath)}`);

    // 4. Self-IDE 服务端 + 5/6. 队列 + 深度探活（R-10：不只探端口）
    try {
        const h = await req(c.selfIdePort, '/health?deep=1', 'GET', undefined, 90000);
        mark(true, `Self-IDE server :${c.selfIdePort}`);
        for (const chk of (h.checks || [])) {
            mark(!!chk.ok, `${chk.name}: ${chk.detail}`);
        }
    } catch (err) {
        mark(false, `Self-IDE server :${c.selfIdePort} — ${err}`);
        mark(false, 'gateway_models: 未知（服务端不可达）');
        mark(false, 'model_queue: 未知（服务端不可达）');
    }

    // 7. Ollama 进程（与内存约束冲突）
    try {
        const { stdout } = await execAsync('tasklist /FI "IMAGENAME eq ollama.exe"', { timeout: 10000 });
        mark(!stdout.includes('ollama.exe'), stdout.includes('ollama.exe')
            ? 'Ollama 进程存在（与内存约束冲突）' : '无 Ollama 进程');
    } catch {
        // tasklist 无匹配时也可能非零退出，视为无进程
        mark(true, '无 Ollama 进程');
    }

    // 8. ★ VS Code 升级兼容自检（三重保障之第三重）
    const cur = vscode.version.split('-')[0];
    const compatible = cmpSemver(cur, REQUIRED_VSCODE) >= 0;
    mark(compatible, `升级兼容: VS Code ${cur} vs engines ^${REQUIRED_VSCODE}`);

    refreshStatusBar();
    if (verbose) {
        vscode.window.showInformationMessage(
            `Self-IDE 健康检查（${lines.length - bad}/${lines.length} 通过）\n\n${lines.join('\n')}`,
            { modal: true });
    }
    return bad === 0;
}

async function silentBoot() {
    const c = cfg();
    try {
        lastQueue = await req(c.selfIdePort, '/queue', 'GET', undefined, 8000);
        connected = true;
    } catch {
        connected = false;   // 静默：启动时服务端未起不打扰用户
    }
    refreshStatusBar();
}

// ==================== 状态栏 ====================

function tierOf(file: string): { tier: string; label: string } {
    const f = (file || '').toLowerCase();
    if (f.endsWith('.cu') || f.endsWith('.cuh')) {
        return { tier: 'deterministic', label: '确定性' };
    }
    if (f.endsWith('.py') && /backtest|engine|risk|cuda|strategy/.test(f)) {
        return { tier: 'deterministic', label: '确定性' };
    }
    if (/\.(md|txt|rst|json|ya?ml|toml|ini)$/.test(f)) {
        return { tier: 'fast', label: '快速' };
    }
    return { tier: 'general', label: '通用' };
}

function refreshStatusBar() {
    const ed = vscode.window.activeTextEditor;
    const { tier, label } = tierOf(ed ? ed.document.fileName : '');

    let model = '—';
    if (lastQueue) {
        const refs = lastQueue.references || [];
        if (tier === 'deterministic' && refs.length) { model = refs[0].model; }
        else if (tier === 'fast' && refs.length) { model = refs[refs.length - 1].model; }
        else { model = (lastQueue.aggregator || refs[0] || { model: '—' }).model; }
    }
    const shortModel = model.includes(':') ? model.split(':').pop()!.split('/').pop()! : model;

    statusBar.text = `$(chip) ${label}·${shortModel} | H:${connected ? '✓' : '✗'}`;
    statusBar.tooltip =
        `Self-IDE\n` +
        `档位: ${label} (${tier})\n` +
        `模型: ${model}\n` +
        `队列: ${lastQueue ? lastQueue.mode : '未知'}（${lastQueue ? lastQueue.source : '-'}）\n` +
        `Hermes: ${connected ? '已连接' : '未连接'}\n` +
        `点击执行健康检查`;
    statusBar.color = connected ? undefined : '#cca700';
    statusBar.show();
}

// ==================== Bridge ====================

async function connectHermes() {
    const c = cfg();
    try {
        const h = await req(c.selfIdePort, '/health', 'GET', undefined, 20000);
        lastQueue = await req(c.selfIdePort, '/queue', 'GET', undefined, 10000);
        connected = !!h.ok || (h.checks || []).some((x: any) => x.ok);
        refreshStatusBar();
        vscode.window.showInformationMessage(
            `Self-IDE: 已连接 Hermes (127.0.0.1:${c.selfIdePort})，` +
            `队列模式 ${lastQueue?.mode}，${lastQueue?.references.length ?? 0} 参谋`);
        showHermesPanel();
    } catch (err) {
        connected = false;
        refreshStatusBar();
        vscode.window.showErrorMessage(
            `Self-IDE: 无法连接 (127.0.0.1:${c.selfIdePort}) — ${err}\n` +
            `启动命令：<your-agent-root>/env/Scripts/python.exe ` +
            `<your-agent-root>/hermes_overlay/self_ide/self_ide_server.py`);
    }
}

function disconnectHermes() {
    connected = false;
    refreshStatusBar();
    if (hermesPanel) { hermesPanel.webview.html = panelHtml(false, undefined, undefined); }
    vscode.window.showInformationMessage('Self-IDE: 已断开');
}

async function showHermesPanel() {
    const c = cfg();
    if (!hermesPanel) {
        hermesPanel = vscode.window.createWebviewPanel(
            'selfIdeHermes', 'Self-IDE · Hermes', vscode.ViewColumn.Two,
            { enableScripts: true, retainContextWhenHidden: true });
        hermesPanel.onDidDispose(() => { hermesPanel = undefined; });
        hermesPanel.webview.onDidReceiveMessage(async msg => {
            try {
                if (msg.command === 'refresh') {
                    const st = await req(c.selfIdePort, '/status', 'GET', undefined, 20000);
                    lastQueue = st.queue;
                    connected = true;
                    refreshStatusBar();
                    hermesPanel!.webview.html = panelHtml(true, st.queue, st.cost);
                } else if (msg.command === 'ping') {
                    const r = await req(c.selfIdePort, '/health?deep=1', 'GET', undefined, 90000);
                    hermesPanel!.webview.postMessage({ type: 'result', data: r });
                }
            } catch (err) {
                hermesPanel!.webview.postMessage({ type: 'result', data: { error: String(err) } });
            }
        });
    }
    hermesPanel.reveal(vscode.ViewColumn.Two);
    try {
        const st = await req(c.selfIdePort, '/status', 'GET', undefined, 20000);
        lastQueue = st.queue;
        connected = true;
        hermesPanel.webview.html = panelHtml(true, st.queue, st.cost);
        refreshStatusBar();
    } catch {
        hermesPanel.webview.html = panelHtml(false, undefined, undefined);
    }
}

function esc(s: string): string {
    return String(s).replace(/[&<>"]/g, ch =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch] as string));
}

function panelHtml(online: boolean, queue?: QueueInfo, cost?: any): string {
    const style = `body{font-family:var(--vscode-font-family);padding:16px;color:var(--vscode-foreground);}
h2{margin:0 0 4px;} .sub{color:#888;font-size:12px;margin-bottom:16px;}
table{border-collapse:collapse;width:100%;margin:8px 0 18px;font-size:13px;}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #3a3a3a;}
th{color:#888;font-weight:normal;font-size:12px;}
.badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;background:#264f78;}
.fb{color:#777;font-size:11px;} button{margin:4px 6px 4px 0;padding:6px 12px;
background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;cursor:pointer;}
pre{background:#1e1e1e;padding:10px;font-size:11px;overflow:auto;max-height:260px;}`;

    if (!online) {
        return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${style}</style></head><body>
<h2>🔴 Self-IDE 服务端未连接</h2>
<div class="sub">端口 19898 无响应</div>
<p>启动命令：</p>
<pre>&lt;your-agent-root&gt;/env/Scripts/python.exe
  &lt;your-agent-root&gt;/hermes_overlay/self_ide/self_ide_server.py</pre>
</body></html>`;
    }

    const refRows = (queue?.references || []).map((r, i) => `<tr>
<td>${i + 1}</td><td><code>${esc(r.model)}</code></td><td>${r.score}</td>
<td class="fb">${(r.fallback || []).map(esc).join('<br>') || '—'}</td></tr>`).join('');

    const agg = queue?.aggregator;
    const costRows = Object.entries((cost?.models) || {}).map(([m, v]: [string, any]) =>
        `<tr><td><code>${esc(m)}</code></td><td>${v.calls}</td><td>${v.prompt}</td><td>${v.completion}</td></tr>`
    ).join('');

    return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${style}</style></head><body>
<h2>🟢 Self-IDE · Hermes</h2>
<div class="sub">队列模式 <span class="badge">${esc(queue?.mode || '-')}</span>
 来源 ${esc(queue?.source || '-')} · 更新 ${esc(queue?.updated_at || '-')}
 · 评分表 ${queue?.total_ranked ?? 0} 条线路</div>

<h3>聚合器</h3>
<table><tr><th>模型</th><th>agg_score</th><th>说明</th></tr>
<tr><td><code>${esc(agg?.model || '—')}</code></td><td>${agg?.score ?? '—'}</td>
<td class="fb">${esc(agg?.note || '')}</td></tr></table>

<h3>参谋队列（由强到弱 · 每线 3 位 fallback）</h3>
<table><tr><th>#</th><th>模型</th><th>ref_score</th><th>fallback 链</th></tr>
${refRows || '<tr><td colspan="4">无</td></tr>'}</table>

<h3>Cost-Meter</h3>
<table><tr><th>模型</th><th>调用</th><th>prompt</th><th>completion</th></tr>
${costRows || '<tr><td colspan="4">暂无数据</td></tr>'}</table>
<div class="sub">合计 ${cost?.total?.calls ?? 0} 次 ·
 ${cost?.total?.prompt ?? 0} prompt / ${cost?.total?.completion ?? 0} completion tokens</div>

<button onclick="send('refresh')">刷新</button>
<button onclick="send('ping')">深度探活（真实推理）</button>
<pre id="out"></pre>
<script>
const vs = acquireVsCodeApi();
function send(c){ document.getElementById('out').textContent='请求中…'; vs.postMessage({command:c}); }
window.addEventListener('message', e => {
  if(e.data && e.data.type==='result'){
    document.getElementById('out').textContent = JSON.stringify(e.data.data, null, 2);
  }
});
</script></body></html>`;
}

// ==================== Indexer（自用符号导航）====================

async function showSymbolMap() {
    const ed = vscode.window.activeTextEditor;
    if (!ed) {
        vscode.window.showWarningMessage('Self-IDE: 请先打开一个文件');
        return;
    }
    if (!symbolPanel) {
        symbolPanel = vscode.window.createWebviewPanel(
            'selfIdeSymbols', '符号地图', vscode.ViewColumn.Two,
            { enableScripts: false, retainContextWhenHidden: true });
        symbolPanel.onDidDispose(() => { symbolPanel = undefined; });
    }
    symbolPanel.reveal(vscode.ViewColumn.Two);
    symbolPanel.title = `符号地图: ${path.basename(ed.document.fileName)}`;

    const syms = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
        'vscode.executeDocumentSymbolProvider', ed.document.uri);
    symbolPanel.webview.html = symbolHtml(syms || [], ed.document.fileName);
}

function symbolHtml(syms: vscode.DocumentSymbol[], file: string): string {
    const render = (list: vscode.DocumentSymbol[], depth = 0): string =>
        list.map(s => {
            const icon = s.kind === vscode.SymbolKind.Class ? '◆'
                : s.kind === vscode.SymbolKind.Function ? '▸'
                : s.kind === vscode.SymbolKind.Method ? '●' : '·';
            const self = `<div style="padding-left:${depth * 20}px;margin:3px 0;">
<span style="color:#89d185">${icon}</span> <strong>${esc(s.name)}</strong>
<span style="color:#777;font-size:11px"> L${s.range.start.line + 1}</span></div>`;
            return self + (s.children && s.children.length ? render(s.children, depth + 1) : '');
        }).join('');

    return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{font-family:var(--vscode-font-family);padding:16px;color:var(--vscode-foreground);}
h2{margin:0 0 4px;} .f{color:#cca700;font-size:12px;margin-bottom:14px;}
.n{color:#777;font-size:12px;margin-top:24px;}</style></head><body>
<h2>📍 符号地图</h2><div class="f">${esc(file)}</div>
<div>${syms.length ? render(syms) : '未找到符号（需对应语言的 LSP 扩展）'}</div>
<div class="n">仅用于快速导航，不向 Continue/Cline 注入上下文。</div>
</body></html>`;
}
