"""
analyzer.py — 大模型调用与新闻分析模块

职责：
  - 封装 OpenAI-兼容 Chat Completions API 的 HTTP 调用
  - 对单条新闻做利好/利空/中性分析（analyze）
  - 对批量新闻做24h汇总摘要（digest）

依赖：仅标准库 + 同目录 server.py 中的两个纯函数工具
      extract_first_json_object / normalize_text
      （如未来彻底拆分，可将这两个函数一并移入本文件）
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

ANALYSIS_REQUIRED_KEYS = ["summary", "impact", "why", "horizon", "confidence"]


@dataclass
class ModelConfig:
    base_url: str
    api_key: str
    model: str


class Analyzer:
    """大模型分析器，读取环境变量完成初始化。

    环境变量：
        BASE_URL  — Chat Completions 端点前缀（默认 https://api.openai.com/v1）
        MODEL     — 模型名称（默认 gpt-4o-mini）
        API_KEY   — API 密钥
    """

    def __init__(self) -> None:
        base_url = os.getenv("BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        model = os.getenv("MODEL", "gpt-4o-mini").strip()
        key = os.getenv("API_KEY", "").strip()
        self.cfg = ModelConfig(base_url=base_url, api_key=key, model=model)

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    def analyze(
        self,
        title: str,
        summary: str,
        persons: list[str],
        tickers: list[str],
    ) -> dict[str, Any]:
        """对单条新闻返回结构化分析结果。

        返回字段：summary / impact / why / horizon / confidence / error_detail
        无论成功与否都保证返回完整字典，失败时 confidence=0。
        """
        if not self.cfg.api_key:
            return self._fallback(
                title=title,
                summary=summary,
                reason="未配置大模型 API Key，无法判断",
                detail=f"API_KEY is empty; MODEL={self.cfg.model}; BASE_URL={self.cfg.base_url}",
            )

        prompt = {
            "task": "你是美股买方分析师助手。请仅返回严格的 JSON 格式结果。",
            "required": {
                "summary": "用1-3句简明中文总结",
                "impact": "bullish|bearish|mixed（三选一，分别表示利好/利空/影响中性或复杂）",
                "why": "用简短中文给出主要因果链解释（不超过80字）",
                "horizon": "intraday|swing|long_term（三选一，分别表示影响时长：盘中/波段/长线）",
                "confidence": "0-100整数，表示你对判断的信心分数",
            },
            "input": {
                "title": title,
                "summary": summary,
                "persons": persons,
                "tickers": tickers,
            },
        }

        try:
            data, raw = self._call_model(prompt)
            if data and all(k in data for k in ANALYSIS_REQUIRED_KEYS):
                return data
            missing = [k for k in ANALYSIS_REQUIRED_KEYS if k not in (data or {})]
            return self._fallback(
                title=title,
                summary=summary,
                reason="模型返回无效，无法判断",
                detail=f"Missing keys: {missing}; raw_content={raw[:1200]}",
            )
        except Exception as e:
            return self._fallback(
                title=title,
                summary=summary,
                reason="模型调用失败，无法判断",
                detail=str(e)[:2000],
            )

    def digest(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """汇总过去24h新闻，输出利好/利空股票列表及宏观摘要。

        返回字段：bullish / bearish / macro_summary
        失败时返回 {"error": "..."}。
        """
        if not self.cfg.api_key:
            return {"error": "未配置 API Key，无法生成汇总"}

        news_lines = []
        for i, ev in enumerate(events[:60], 1):
            impact = ev.get("impact", "mixed")
            title = (ev.get("title") or "")[:120]
            why = (ev.get("why") or "")[:100]
            tickers_str = ",".join(ev.get("tickers") or [])
            news_lines.append(f"{i}. [{impact}] {title} | 标的:{tickers_str} | {why}")

        prompt = {
            "task": (
                "你是资深美股买方分析师。根据以下过去24小时的财经新闻列表，"
                "汇总出对哪些股票利好、对哪些股票利空，并给出简明因果解释。"
                "优先选择各赛道龙头股（如科技选NVDA/AAPL/MSFT，能源选XLE，金融选XLF等），"
                "除非新闻明确点名具体公司才用该公司股票代码。"
                "每个股票只出现一次，合并同一股票的多条新闻影响后给出综合判断。"
                "严格返回JSON，不得有任何额外文字。"
            ),
            "output_format": {
                "bullish": [
                    {"ticker": "股票代码", "reason": "利好原因，30-60字", "key_news": "最关键的1条新闻标题"}
                ],
                "bearish": [
                    {"ticker": "股票代码", "reason": "利空原因，30-60字", "key_news": "最关键的1条新闻标题"}
                ],
                "macro_summary": "宏观环境一句话总结，50字以内",
            },
            "news": news_lines,
        }

        try:
            data, raw = self._call_model(prompt)
            if data and "bullish" in data and "bearish" in data:
                return data
            return {"error": f"模型返回格式异常: {raw[:300]}"}
        except Exception as e:
            return {"error": str(e)[:500]}

    # ──────────────────────────────────────────────
    # 内部实现
    # ──────────────────────────────────────────────

    def _call_model(self, prompt: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """发送 Chat Completions 请求，返回 (parsed_dict, raw_content)。

        解析失败时 parsed_dict 为 None；网络/HTTP 错误时抛出 RuntimeError。
        """
        url = f"{self.cfg.base_url}/chat/completions"
        body = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.cfg.api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {e.code} {e.reason}; body={err_body[:1200]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error: {e.reason}")

        try:
            content = payload["choices"][0]["message"]["content"]
        except Exception:
            preview = json.dumps(payload, ensure_ascii=False)[:1200]
            raise RuntimeError(f"Invalid response payload: {preview}")

        return _extract_first_json_object(content), content

    def _fallback(
        self, title: str, summary: str, reason: str, detail: str = ""
    ) -> dict[str, Any]:
        """分析失败时返回的保底结构，保证字段完整。"""
        return {
            "summary": _normalize_text(summary)[:280] or _normalize_text(title),
            "impact": "mixed",
            "why": reason,
            "error_detail": detail,
            "horizon": "intraday",
            "confidence": 0,
        }


# ──────────────────────────────────────────────────────────
# 模块内部工具函数（不对外暴露）
# ──────────────────────────────────────────────────────────

def _normalize_text(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", (s or "")).strip()


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    direct = _safe_json_loads(text)
    if isinstance(direct, dict):
        return direct
    start = text.find("{")
    while start != -1:
        depth, in_str, escape = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    parsed = _safe_json_loads(text[start: i + 1])
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None
