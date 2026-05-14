# gateway.py - OpenClaw Gateway
import os, json, re, logging, time, random, asyncio, yaml
from urllib.parse import urlparse
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

try:
    from DS.tools import web_search
except Exception:
    web_search = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("gateway")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "openclaw.json")
KEYS_ENV_PATH = os.path.join(BASE_DIR, "keys.env")
CUTECLOUD_CONFIG_PATH = os.path.join(BASE_DIR, "cutecloud", "config.yaml")

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

def is_domestic_provider(provider_name):
    cfg = load_config()
    prov = cfg.get("providers", {}).get(provider_name)
    if not prov:
        return False
    base_url = prov.get("baseUrl", "")
    domain = urlparse(base_url).hostname or ""
    if not domain:
        return False
    rules, err = load_rules()
    if err or not rules:
        dd = {"api.siliconflow.cn", "api.deepseek.com", "api.zenmux.ai", "dashscope.aliyun.com", "open.bigmodel.cn", "integrate.api.nvidia.com"}
        ds = {"siliconflow.cn", "deepseek.com", "zenmux.ai", "aliyun.com", "aliyuncs.com", "bigmodel.cn", "nvidia.cn", "xiaomimimo.com"}
        if domain in dd:
            return True
        for suffix in ds:
            if domain.endswith(suffix) or domain == suffix:
                return True
        return False
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
            return policy == "DIRECT"
    return False

if os.path.exists(KEYS_ENV_PATH):
    with open(KEYS_ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k] = v

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"gateway": "", "providers": {}, "models_pool": []}

def resolve_env(text):
    if not text:
        return text
    return re.sub(r'\$\{([^}]+)\}', lambda m: os.environ.get(m.group(1), ""), text)

MEMOS_API_URL = os.getenv("MEMOS_API_URL", "http://127.0.0.1:5230")
MEMOS_API_KEY = os.getenv("MEMOS_API_KEY", "")

async def save_to_memos(title: str, content: str):
    if not MEMOS_API_KEY:
        return
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(
                f"{MEMOS_API_URL}/api/v1/memo",
                headers={"Authorization": f"Bearer {MEMOS_API_KEY}"},
                json={"content": f"**{title}**\n\n{content}"}
            )
    except Exception:
        pass

class FileSystemTools:
    @staticmethod
    def list_directory(path: str) -> str:
        if not path.startswith("D:"):
            return "Error: D: drive only"
        try:
            items = os.listdir(path)
            result = [f"Directory listing for {path}:"]
            for item in items:
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
                    result.append(f"[DIR] {item}/")
                else:
                    result.append(f"[FILE] {item}")
            return "\n".join(result)
        except Exception as e:
            return f"List directory failed: {str(e)}"

    @staticmethod
    def read_file(path: str) -> str:
        if not path.startswith("D:"):
            return "Error: D: drive only"
        if not os.path.exists(path):
            return "Error: file not found"
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            return f"File content ({path}):\n---\n{content}"
        except Exception as e:
            return f"Read file failed: {str(e)}"

    @staticmethod
    def write_file(path: str, content: str) -> str:
        if not path.startswith("D:"):
            return "Error: D: drive only"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote file: {path}"
        except Exception as e:
            return f"Write file failed: {str(e)}"

tools = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List all files and folders in the specified directory. Path must start with D:",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list, e.g. D:\\"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of the specified file. Path must start with D:",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write/modify file contents. Path must start with D:",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "General web search for latest information, news, knowledge, data, etc. Uses Baidu/Bing search",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"]
            }
        }
    }
]

app = FastAPI(title="OpenClaw Gateway", version="36.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root():
    cfg = load_config()
    return {"service": "OpenClaw Gateway", "models": len(cfg.get("models_pool", []))}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/v1/models")
async def list_models():
    cfg = load_config()
    return {"object": "list", "data": [{"id": m["id"], "owned_by": m["provider"]} for m in cfg.get("models_pool", [])]}

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except:
        return JSONResponse(status_code=400, content={"error": {"message": "invalid json"}})

    model_id = body.get("model")
    messages = body.get("messages")
    stream = body.get("stream", False)

    if not model_id or not messages:
        return JSONResponse(status_code=400, content={"error": {"message": "missing model or messages"}})

    cfg = load_config()
    model_info = next((m for m in cfg.get("models_pool", []) if m["id"] == model_id), None)
    if not model_info:
        return JSONResponse(status_code=404, content={"error": {"message": f"Model not found: {model_id}"}})

    provider_name = model_info["provider"]
    actual_model = model_info["actual"]
    provider = cfg.get("providers", {}).get(provider_name)
    if not provider:
        return JSONResponse(status_code=500, content={"error": {"message": "Provider not configured"}})

    api_key = resolve_env(provider.get("apiKey", ""))
    base_url = resolve_env(provider.get("baseUrl", ""))
    proxy = provider.get("proxy", "")
    timeout = 8.0 if not is_domestic_provider(provider_name) else 5.0
    client_kwargs = {"timeout": timeout}

    if proxy:
        client_kwargs["proxy"] = proxy
        client_kwargs["trust_env"] = False
    else:
        client_kwargs["trust_env"] = True

    request_body = {
        "model": actual_model,
        "messages": messages,
        "stream": stream,
    }
    if not stream:
        request_body["tools"] = tools
        request_body["tool_choice"] = "auto"

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=request_body,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            )
            if resp.status_code == 404:
                return JSONResponse(status_code=404, content={"error": {"message": f"Model not found: {model_id}"}})
            if resp.status_code == 401:
                return JSONResponse(status_code=401, content={"error": {"message": f"Invalid API key: {model_id}"}})
            if resp.status_code == 402:
                return JSONResponse(status_code=402, content={"error": {"message": f"Insufficient balance: {model_id}"}})
            if resp.status_code == 403:
                return JSONResponse(status_code=403, content={"error": {"message": f"Access forbidden: {model_id}"}})
            if resp.status_code == 429:
                return JSONResponse(status_code=429, content={"error": {"message": f"Rate limited: {model_id}"}})
            if resp.status_code >= 500:
                return JSONResponse(status_code=503, content={"error": {"message": f"Service error: {model_id}"}})
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")

            if stream:
                return StreamingResponse(resp.aiter_bytes(), media_type="text/event-stream")

            response_data = resp.json()
            choice = response_data["choices"][0] if "choices" in response_data and len(response_data["choices"]) > 0 else None
            if choice and "message" in choice and "tool_calls" in choice["message"]:
                tool_calls = choice["message"]["tool_calls"]
                tool_messages = []
                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    func_args = json.loads(tool_call["function"]["arguments"])
                    if func_name == "list_directory":
                        result = FileSystemTools.list_directory(func_args["path"])
                    elif func_name == "read_file":
                        result = FileSystemTools.read_file(func_args["path"])
                    elif func_name == "write_file":
                        result = FileSystemTools.write_file(func_args["path"], func_args["content"])
                    elif func_name == "web_search":
                        if web_search:
                            result = web_search(func_args.get("query", ""), timeout=30.0)
                        else:
                            result = "web_search tool not available"
                    else:
                        result = "Unknown tool call"
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": func_name,
                        "content": result
                    })
                    asyncio.create_task(save_to_memos(f"Tool execution {func_name}", f"Model: {model_id}\nArgs: {func_args}\nResult: {result}"))
                messages.extend(tool_messages)
                final_resp = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    json={"model": actual_model, "messages": messages, "stream": False},
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                )
                if final_resp.status_code != 200:
                    raise Exception("Model request failed after tool call")
                response_data = final_resp.json()
            asyncio.create_task(save_to_memos(f"Chat log {model_id}", "\n".join([f"{m.get('role')}: {m.get('content', '')}" for m in messages[-4:]])))
            return JSONResponse(content=response_data)

    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"error": {"message": f"Model timeout: {model_id}"}})
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"error": {"message": f"Model timeout: {model_id}"}})
    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": {"message": f"Connection failed: {model_id}"}})
    except httpx.NetworkError:
        return JSONResponse(status_code=502, content={"error": {"message": f"Network error: {model_id}"}})
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "does not exist" in msg:
            return JSONResponse(status_code=404, content={"error": {"message": f"Model not found: {model_id}"}})
        if "quota" in msg or "balance" in msg or "payment" in msg:
            return JSONResponse(status_code=402, content={"error": {"message": f"Insufficient balance: {model_id}"}})
        if "api key" in msg or "authentication" in msg:
            return JSONResponse(status_code=401, content={"error": {"message": f"Invalid API key: {model_id}"}})
        if "rate limit" in msg or "too many" in msg:
            return JSONResponse(status_code=429, content={"error": {"message": f"Rate limited: {model_id}"}})
        logger.error(f"Request failed: {model_id} | {str(e)[:180]}")
        return JSONResponse(status_code=500, content={"error": {"message": f"Request failed: {model_id}"}})

@app.get("/memos/health")
async def memos_health():
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            res = await c.get(MEMOS_API_URL)
            return {"reachable": res.status_code < 500, "url": MEMOS_API_URL}
    except:
        return {"reachable": False, "url": MEMOS_API_URL}

if __name__ == "__main__":
    cfg = load_config()
    print("OpenClaw Gateway started")
    print("Listening on: 127.0.0.1:19999")
    rules, _ = load_rules()
    print(f"Loaded config.yaml rules: {len(rules) if rules else 0}")
    uvicorn.run(app, host="127.0.0.1", port=19999, log_level="warning")
