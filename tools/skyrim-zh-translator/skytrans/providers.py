"""翻译引擎适配层。

内置：
- openai   : 任何 OpenAI 兼容接口（DeepSeek / 通义千问 / Kimi / 智谱 / 硅基流动 / Ollama / LM Studio / vLLM）
- deepl    : DeepL API（免费版与付费版）
- google   : Google Cloud Translation v2
- baidu    : 百度翻译开放平台
- libre    : 自建 LibreTranslate
- mock     : 不联网，返回 "[ZH] 原文"（用于试跑流水线）
- none     : 不翻译（只做扫描/导出）

只依赖标准库 urllib，不需要 pip install 任何东西。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .textfilter import Glossary


class ProviderError(Exception):
    pass


SYSTEM_PROMPT = """你是《上古卷轴5：天际》(Skyrim) 模组的资深汉化译者，负责把英文游戏文本翻译成简体中文。

硬性规则：
1. 只输出译文，绝不解释、绝不添加原文没有的内容。
2. 严格保留占位符和控制符，例如 ⟦0⟧、%s、%d、<font>、[PLYR]、\\n、<Alias=...>，位置可调整但必须全部保留。
3. 物品/技能/地点名简洁（2~6 字），界面按钮短语化（2~4 字），对话保持原语气与人物口吻，书籍/说明文字通顺可读。
4. 专有名词严格使用给定术语表；未给出的按天际官方汉化习惯音译（如 Whiterun=雪漫、Solitude=独孤城）。
5. 已经是中文、或只是数字/代号/文件名的，原样返回。
6. 不要输出中文引号以外的多余标点，不要翻译英文缩写（如 HP、MCM、SKSE、DLC）。

输出格式：只输出一个 JSON 对象，形如 {"items":[{"i":0,"t":"译文"},{"i":1,"t":"译文"}]}，i 必须与输入序号一致。"""


@dataclass
class Provider:
    name: str = "base"
    model: str = ""
    batch_chars: int = 2400
    batch_items: int = 30
    max_retries: int = 4
    timeout: int = 120
    glossary: Optional[Glossary] = None
    calls: int = 0
    chars: int = 0

    # 子类实现
    def translate_batch(self, texts: Sequence[str]) -> List[str]:  # pragma: no cover
        raise NotImplementedError

    def describe(self) -> str:
        return self.name + ("/" + self.model if self.model else "")

    # ---- 公共重试包装 ----
    def translate(self, texts: Sequence[str]) -> List[str]:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                result = self.translate_batch(texts)
                if len(result) != len(texts):
                    raise ProviderError(
                        "返回条数不符: 期望 %d, 实际 %d" % (len(texts), len(result))
                    )
                self.calls += 1
                self.chars += sum(len(t) for t in texts)
                return result
            except ProviderError as exc:
                last_error = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_error = ProviderError("网络错误: %s" % exc)
            except Exception as exc:  # noqa: BLE001
                last_error = ProviderError("%s: %s" % (type(exc).__name__, exc))
            sleep = min(30, 1.6 ** attempt) + random.random()
            time.sleep(sleep)
        raise ProviderError(str(last_error))


def _post_json(url: str, payload: dict, headers: Dict[str, str], timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise ProviderError("HTTP %s: %s" % (exc.code, body)) from exc


def _post_form(url: str, fields: List[tuple], timeout: int) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise ProviderError("HTTP %s: %s" % (exc.code, body)) from exc


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    # 退化为逐条正则提取
    items = []
    for match in re.finditer(
        r'"i"\s*:\s*(\d+)\s*,\s*"t"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.S
    ):
        items.append({"i": int(match.group(1)), "t": json.loads('"' + match.group(2) + '"')})
    if items:
        return {"items": items}
    raise ProviderError("无法解析模型返回的 JSON: %s" % text[:200])


class OpenAIProvider(Provider):
    """OpenAI 兼容接口（绝大多数国内大模型都支持）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.25,
        **kwargs,
    ) -> None:
        super().__init__(name="openai", model=model, **kwargs)
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/chat/completions"):
            self.base_url += "/chat/completions"
        self.api_key = api_key
        self.temperature = temperature

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        payload_texts = [{"i": i, "t": t} for i, t in enumerate(texts)]
        system = SYSTEM_PROMPT
        if self.glossary:
            block = self.glossary.prompt_block(text="\n".join(texts))
            if block:
                system += "\n\n" + block
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"items": payload_texts}, ensure_ascii=False
                    ),
                },
            ],
        }
        headers = {"Authorization": "Bearer %s" % self.api_key} if self.api_key else {}
        resp = _post_json(self.base_url, payload, headers, self.timeout)
        try:
            content = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError("接口返回格式异常: %s" % str(resp)[:300]) from exc
        data = _extract_json(content)
        mapping = {}
        for item in data.get("items", []):
            try:
                mapping[int(item["i"])] = str(item.get("t", "")).strip()
            except (KeyError, TypeError, ValueError):
                continue
        missing = [i for i in range(len(texts)) if not mapping.get(i)]
        if missing:
            raise ProviderError("有 %d 条未返回译文" % len(missing))
        return [mapping[i] for i in range(len(texts))]


class DeepLProvider(Provider):
    def __init__(self, api_key: str, free: bool = True, **kwargs) -> None:
        super().__init__(name="deepl", model="deepl", **kwargs)
        self.api_key = api_key
        self.url = (
            "https://api-free.deepl.com/v2/translate"
            if free
            else "https://api.deepl.com/v2/translate"
        )

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        fields: List[tuple] = [
            ("auth_key", self.api_key),
            ("source_lang", "EN"),
            ("target_lang", "ZH"),
            ("preserve_formatting", "1"),
        ]
        for text in texts:
            fields.append(("text", text))
        resp = _post_form(self.url, fields, self.timeout)
        translations = resp.get("translations") or []
        if len(translations) != len(texts):
            raise ProviderError("DeepL 返回条数不符")
        return [t.get("text", "") for t in translations]


class GoogleProvider(Provider):
    def __init__(self, api_key: str, **kwargs) -> None:
        super().__init__(name="google", model="nmt", **kwargs)
        self.api_key = api_key
        self.url = "https://translation.googleapis.com/language/translate/v2?key=%s" % urllib.parse.quote(
            api_key
        )

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        payload = {
            "q": list(texts),
            "source": "en",
            "target": "zh-CN",
            "format": "text",
        }
        resp = _post_json(self.url, payload, {}, self.timeout)
        try:
            items = resp["data"]["translations"]
        except KeyError as exc:
            raise ProviderError("谷歌返回异常: %s" % str(resp)[:300]) from exc
        return [re.sub(r"&quot;", '"', item["translatedText"]) for item in items]


class BaiduProvider(Provider):
    def __init__(self, appid: str, key: str, **kwargs) -> None:
        super().__init__(name="baidu", model="general", **kwargs)
        self.appid = appid
        self.key = key
        self.url = "https://fanyi-api.baidu.com/api/trans/vip/translate"

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        # 百度一次请求最多 6000 字节，用 \n 作为分隔符
        joined = "\n".join(texts)
        salt = str(int(time.time() * 1000))
        sign_src = self.appid + joined + salt + self.key
        sign = hashlib.md5(sign_src.encode("utf-8")).hexdigest()
        fields = [
            ("q", joined),
            ("from", "en"),
            ("to", "zh"),
            ("appid", self.appid),
            ("salt", salt),
            ("sign", sign),
        ]
        resp = _post_form(self.url, fields, self.timeout)
        if "error_code" in resp and resp["error_code"] != "52000":
            raise ProviderError("百度错误 %s" % resp.get("error_code"))
        results = resp.get("trans_result") or []
        if len(results) != len(texts):
            raise ProviderError("百度返回条数不符: %d/%d" % (len(results), len(texts)))
        return [item.get("dst", "") for item in results]


class LibreProvider(Provider):
    def __init__(self, base_url: str, api_key: str = "", **kwargs) -> None:
        super().__init__(name="libre", model="libretranslate", **kwargs)
        self.url = base_url.rstrip("/") + "/translate"
        self.api_key = api_key

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        payload = {
            "q": list(texts),
            "source": "en",
            "target": "zh",
            "format": "text",
        }
        if self.api_key:
            payload["api_key"] = self.api_key
        resp = _post_json(self.url, payload, {}, self.timeout)
        result = resp.get("translatedText")
        if isinstance(result, str):
            result = [result]
        if not result or len(result) != len(texts):
            raise ProviderError("LibreTranslate 返回异常")
        return list(result)


class MockProvider(Provider):
    """离线试跑：把原文标成 [ZH] 前缀，用于验证整条流水线。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(name="mock", model="mock", **kwargs)

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        return ["【译】" + t for t in texts]


class NoopProvider(Provider):
    def __init__(self, **kwargs) -> None:
        super().__init__(name="none", model="none", **kwargs)

    def translate_batch(self, texts: Sequence[str]) -> List[str]:
        return list(texts)


def build_provider(config: dict, glossary: Optional[Glossary] = None) -> Provider:
    kind = (config.get("provider") or "none").lower()
    common = {
        "batch_chars": int(config.get("batch_chars", 2400)),
        "batch_items": int(config.get("batch_items", 30)),
        "timeout": int(config.get("timeout", 120)),
        "max_retries": int(config.get("max_retries", 4)),
        "glossary": glossary,
    }
    if kind == "openai":
        return OpenAIProvider(
            base_url=config.get("base_url") or "https://api.openai.com/v1",
            api_key=config.get("api_key", ""),
            model=config.get("model") or "gpt-4o-mini",
            temperature=float(config.get("temperature", 0.25)),
            **common,
        )
    if kind == "deepl":
        return DeepLProvider(
            api_key=config.get("api_key", ""),
            free=bool(config.get("deepl_free", True)),
            **common,
        )
    if kind == "google":
        return GoogleProvider(api_key=config.get("api_key", ""), **common)
    if kind == "baidu":
        return BaiduProvider(
            appid=config.get("app_id", ""), key=config.get("api_key", ""), **common
        )
    if kind == "libre":
        return LibreProvider(
            base_url=config.get("base_url") or "http://localhost:5000",
            api_key=config.get("api_key", ""),
            **common,
        )
    if kind == "mock":
        return MockProvider(**common)
    return NoopProvider(**common)
