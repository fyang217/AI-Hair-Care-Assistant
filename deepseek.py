"""DeepSeek Chat Completions transport. Never exposes API headers or response bodies in errors."""
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False, interpolate=False)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

class DeepSeekError(Exception):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def complete(system, data, *, max_tokens=700, timeout=15, usage=None):
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise DeepSeekError("未设置 DEEPSEEK_API_KEY")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    if base not in (DEFAULT_BASE_URL, DEFAULT_BASE_URL + "/v1"):
        raise DeepSeekError("DEEPSEEK_BASE_URL 必须是 DeepSeek 官方 HTTPS API 根地址")
    model = (os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip()
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,80}", model):
        raise DeepSeekError("DEEPSEEK_MODEL 配置无效")
    request = Request(base + "/chat/completions", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
    }, data=json.dumps({
        "model": model, "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"}, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
    }).encode("utf-8"))
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            envelope = json.load(response)
        if usage is not None:
            counters = envelope.get("usage")
            if isinstance(counters,dict):
                usage.update({k:v for k,v in counters.items() if k in {
                    "prompt_tokens","completion_tokens","prompt_cache_hit_tokens","prompt_cache_miss_tokens"
                } and type(v) is int and v >= 0})
        choice = envelope["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise DeepSeekError("DeepSeek 回复未完整结束")
        obj = json.loads(choice["message"]["content"])
        if not isinstance(obj, dict):
            raise ValueError("Expected object")
        return obj, model
    except HTTPError as error:
        error.close()
        raise DeepSeekError(f"DeepSeek API 返回 HTTP {error.code}") from None
    except OSError:
        raise DeepSeekError("DeepSeek API 网络连接失败或超时") from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise DeepSeekError("DeepSeek API 未返回可用的 JSON 对象") from None
