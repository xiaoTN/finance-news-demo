#!/usr/bin/env python3
import datetime as dt
import json
import os
import re
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from queue import Queue
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "events.db"
STATIC_DIR = BASE_DIR / "static"
HOST = "127.0.0.1"
PORT = 8787
REFRESH_SECONDS = 300
USER_AGENT = "FinanceNewsDemo/0.1 (+local)"

SOURCES = [
    {
        "name": "Reuters Business",
        "type": "rss",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "focus": "Business, Finance",
    },
    {
        "name": "CNBC Top News",
        "type": "rss",
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "focus": "US market intraday",
    },
    {
        "name": "NVIDIA Newsroom",
        "type": "json",
        "url": "https://nvidianews.nvidia.com/json",
        "focus": "Jensen Huang / NVDA",
    },
    {
        "name": "Federal Reserve Press Releases",
        "type": "rss",
        "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "focus": "Powell / rates / inflation",
    },
    {
        "name": "MarketWatch",
        "type": "rss",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories",
        "focus": "US Markets",
    },
    {
        "name": "Benzinga",
        "type": "rss",
        "url": "https://www.benzinga.com/feed",
        "focus": "Stock Market News",
    },
    {
        "name": "Seeking Alpha",
        "type": "rss",
        "url": "https://seekingalpha.com/market_currents.xml",
        "focus": "Investment Analysis",
    },
]


TICKER_RULES = [
    (r"\btesla\b|\btsla\b|\bmusk\b", ["TSLA"]),
    (r"\bnvidia\b|\bnvda\b|\bjensen\b", ["NVDA", "AMD", "TSM"]),
    (r"\bfed\b|\bpowell\b|rate cut|inflation|labor market", ["SPY", "QQQ", "TLT", "DXY"]),
    (r"tariff|sanction|trade war|china", ["DXY", "XLI", "XLE"]),
    (r"oil|crude|opec", ["XLE"]),
    (r"bank|yield|treasury", ["XLF", "TLT"]),
]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_rfc822_to_iso(s: str) -> str | None:
    """Parse RFC 822 time format to ISO format for proper sorting."""
    if not s:
        return None
    try:
        # RFC 822 format: "Fri, 27 Feb 2026 16:54:44 GMT"
        dt_obj = dt.datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %Z")
        return dt_obj.replace(tzinfo=dt.timezone.utc).isoformat()
    except Exception:
        return None


def log(msg: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_env_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def http_get(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def safe_json_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    direct = safe_json_loads(text)
    if isinstance(direct, dict):
        return direct
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
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
                    candidate = text[start : i + 1]
                    parsed = safe_json_loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def map_tickers(text: str) -> list[str]:
    t = text.lower()
    tickers: set[str] = set()
    for pattern, items in TICKER_RULES:
        if re.search(pattern, t):
            tickers.update(items)
    return sorted(tickers)


@dataclass
class ModelConfig:
    base_url: str
    api_key: str
    model: str


class Analyzer:
    def __init__(self) -> None:
        base_url = os.getenv("BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        model = os.getenv("MODEL", "gpt-4o-mini").strip()
        key = os.getenv("API_KEY", "").strip()
        self.cfg = ModelConfig(base_url=base_url, api_key=key, model=model)

    def analyze(self, title: str, summary: str, persons: list[str], tickers: list[str]) -> dict[str, Any]:
        if not self.cfg.api_key:
            return self._unknown_analysis(
                title=title,
                summary=summary,
                reason="未配置大模型 API Key，无法判断",
                detail=f"API_KEY is empty; MODEL={self.cfg.model}; BASE_URL={self.cfg.base_url}",
            )

        prompt = {
            "task": "You are a buy-side analyst assistant. Return strict JSON only.",
            "required": {
                "summary": "1-3 concise Chinese sentences",
                "impact": "bullish|bearish|mixed",
                "why": "因果链解释，最多80字",
                "horizon": "intraday|swing|long_term",
                "confidence": "0-100 integer",
            },
            "input": {
                "title": title,
                "summary": summary,
                "persons": persons,
                "tickers": tickers,
            },
        }

        try:
            data, raw_content = self._call_model(prompt)
            if data and all(k in data for k in ["summary", "impact", "why", "horizon", "confidence"]):
                return data
            missing = [k for k in ["summary", "impact", "why", "horizon", "confidence"] if k not in (data or {})]
            return self._unknown_analysis(
                title=title,
                summary=summary,
                reason="模型返回无效，无法判断",
                detail=f"Missing keys: {missing}; raw_content={raw_content[:1200]}",
            )
        except Exception as e:
            return self._unknown_analysis(
                title=title,
                summary=summary,
                reason="模型调用失败，无法判断",
                detail=str(e)[:2000],
            )

    def _unknown_analysis(self, title: str, summary: str, reason: str, detail: str = "") -> dict[str, Any]:
        return {
            "summary": normalize_text(summary)[:280] or normalize_text(title),
            "impact": "mixed",
            "why": reason,
            "error_detail": detail,
            "horizon": "intraday",
            "confidence": 0,
        }

    def digest(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """汇总过去24h新闻，输出利好/利空股票列表及原因。"""
        if not self.cfg.api_key:
            return {"error": "未配置 API Key，无法生成汇总"}

        # 构造简洁的新闻摘要列表喂给模型
        news_lines = []
        for i, ev in enumerate(events[:60], 1):
            impact = ev.get("impact", "mixed")
            title = (ev.get("title") or "")[:120]
            why = (ev.get("why") or "")[:100]
            tickers = ",".join(ev.get("tickers") or [])
            news_lines.append(f"{i}. [{impact}] {title} | 标的:{tickers} | {why}")

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

    def _call_model(self, prompt: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
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
        content = ""
        try:
            content = payload["choices"][0]["message"]["content"]
        except Exception:
            preview = json.dumps(payload, ensure_ascii=False)[:1200]
            raise RuntimeError(f"Invalid response payload: {preview}")
        return extract_first_json_object(content), content


class Repo:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_name TEXT NOT NULL,
                  title TEXT NOT NULL,
                  url TEXT NOT NULL,
                  published_at TEXT,
                  captured_at TEXT NOT NULL,
                  summary TEXT,
                  persons TEXT,
                  tickers TEXT,
                  impact TEXT,
                  why TEXT,
                  error_detail TEXT,
                  horizon TEXT,
                  confidence INTEGER,
                  analysis_status TEXT,
                  analysis_started_at TEXT,
                  analysis_finished_at TEXT,
                  unique_key TEXT NOT NULL UNIQUE
                )
                """
            )
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "error_detail" not in cols:
                conn.execute("ALTER TABLE events ADD COLUMN error_detail TEXT")
            if "analysis_status" not in cols:
                conn.execute("ALTER TABLE events ADD COLUMN analysis_status TEXT")
            if "analysis_started_at" not in cols:
                conn.execute("ALTER TABLE events ADD COLUMN analysis_started_at TEXT")
            if "analysis_finished_at" not in cols:
                conn.execute("ALTER TABLE events ADD COLUMN analysis_finished_at TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_captured ON events(captured_at DESC)")

    def existing_keys(self, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        with self._connect() as conn:
            placeholders = ",".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT unique_key FROM events WHERE unique_key IN ({placeholders})", keys
            ).fetchall()
        return {r["unique_key"] for r in rows}

    def insert_event(self, event: dict[str, Any]) -> int | None:
        with self._lock, self._connect() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO events (
                      source_name,title,url,published_at,captured_at,summary,persons,tickers,
                      impact,why,error_detail,horizon,confidence,analysis_status,analysis_started_at,
                      analysis_finished_at,unique_key
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event["source_name"],
                        event["title"],
                        event["url"],
                        event.get("published_at"),
                        event["captured_at"],
                        event.get("summary", ""),
                        json.dumps(event.get("persons", []), ensure_ascii=False),
                        json.dumps(event.get("tickers", []), ensure_ascii=False),
                        event.get("impact", "mixed"),
                        event.get("why", ""),
                        event.get("error_detail", ""),
                        event.get("horizon", "intraday"),
                        int(event.get("confidence", 50)),
                        event.get("analysis_status", "done"),
                        event.get("analysis_started_at"),
                        event.get("analysis_finished_at"),
                        event["unique_key"],
                    ),
                )
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def mark_event_analyzing(self, event_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE events
                SET analysis_status = ?, analysis_started_at = ?
                WHERE id = ?
                """,
                ("analyzing", utc_now_iso(), event_id),
            )

    def finish_event_analysis(self, event_id: int, ai: dict[str, Any], status: str) -> None:
        impact = ai.get("impact", "mixed")
        if status != "done":
            impact = "mixed"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE events
                SET summary = ?, impact = ?, why = ?, error_detail = ?, horizon = ?, confidence = ?,
                    analysis_status = ?, analysis_finished_at = ?
                WHERE id = ?
                """,
                (
                    ai.get("summary", ""),
                    impact,
                    ai.get("why", ""),
                    ai.get("error_detail", ""),
                    ai.get("horizon", "intraday"),
                    int(ai.get("confidence", 0)),
                    status,
                    utc_now_iso(),
                    event_id,
                ),
            )

    def reset_stuck_analyzing(self) -> int:
        """重启恢复：将卡在 analyzing 的任务回退为 pending，等待重试。"""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE events
                SET analysis_status = 'pending', analysis_started_at = NULL
                WHERE analysis_status = 'analyzing'
                """
            )
            return int(cur.rowcount or 0)

    def list_pending_analysis_tasks(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(2000, limit))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, summary, persons, tickers
                FROM events
                WHERE analysis_status = 'pending'
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "title": r["title"],
                "summary": r["summary"] or "",
                "persons": safe_json_loads(r["persons"]) or [],
                "tickers": safe_json_loads(r["tickers"]) or [],
            })
        return result

    def count_pending_analysis(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM events
                WHERE analysis_status IN ('pending', 'analyzing')
                """
            ).fetchone()
        return int(row["cnt"] if row else 0)

    def list_events(self, limit: int = 50, sort: str = "captured") -> list[dict[str, Any]]:
        limit = max(1, min(200, limit))
        if sort == "published":
            # Fetch more rows, then sort in Python with proper ISO conversion
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM events LIMIT ?", (limit * 3,)).fetchall()
            items = []
            for r in rows:
                items.append({
                    "id": r["id"],
                    "source_name": r["source_name"],
                    "title": r["title"],
                    "url": r["url"],
                    "published_at": r["published_at"],
                    "captured_at": r["captured_at"],
                    "summary": r["summary"],
                    "persons": safe_json_loads(r["persons"]) or [],
                    "tickers": safe_json_loads(r["tickers"]) or [],
                    "impact": r["impact"],
                    "why": r["why"],
                    "error_detail": r["error_detail"] or "",
                    "horizon": r["horizon"],
                    "confidence": r["confidence"],
                    "analysis_status": r["analysis_status"] or ("done" if r["why"] else "pending"),
                    "analysis_started_at": r["analysis_started_at"],
                    "analysis_finished_at": r["analysis_finished_at"],
                })
            # Sort by published_at (convert to ISO for proper sorting)
            def get_published_sort_key(item):
                iso = parse_rfc822_to_iso(item.get("published_at", "") or "")
                return iso or ""
            items.sort(key=get_published_sort_key, reverse=True)
            result = items[:limit]
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM events ORDER BY captured_at DESC, id DESC LIMIT ?", (limit,)
                ).fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "source_name": r["source_name"],
                    "title": r["title"],
                    "url": r["url"],
                    "published_at": r["published_at"],
                    "captured_at": r["captured_at"],
                    "summary": r["summary"],
                    "persons": safe_json_loads(r["persons"]) or [],
                    "tickers": safe_json_loads(r["tickers"]) or [],
                    "impact": r["impact"],
                    "why": r["why"],
                    "error_detail": r["error_detail"] or "",
                    "horizon": r["horizon"],
                    "confidence": r["confidence"],
                    "analysis_status": r["analysis_status"] or ("done" if r["why"] else "pending"),
                    "analysis_started_at": r["analysis_started_at"],
                    "analysis_finished_at": r["analysis_finished_at"],
                })
        return result

    def list_recent_events(self, hours: int = 24) -> list[dict[str, Any]]:
        """查询最近 N 小时内抓取的事件。"""
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE captured_at >= ? ORDER BY captured_at DESC",
                (cutoff,),
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "title": r["title"],
                "source_name": r["source_name"],
                "impact": r["impact"],
                "why": r["why"],
                "tickers": safe_json_loads(r["tickers"]) or [],
                "persons": safe_json_loads(r["persons"]) or [],
                "horizon": r["horizon"],
                "captured_at": r["captured_at"],
            })
        return result

    def clear_events(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM events")
            return int(cur.rowcount or 0)

    def delete_non_mixed_events(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM events WHERE impact IN ('bullish','bearish')")
            return int(cur.rowcount or 0)


class Collector:
    def __init__(self, repo: Repo, analyzer: Analyzer) -> None:
        self.repo = repo
        self.analyzer = analyzer

    def fetch_all(self, progress: dict[str, Any] | None = None) -> dict[str, int]:
        inserted = 0
        seen = 0
        for idx, src in enumerate(SOURCES):
            if progress is not None:
                progress["current_idx"] = idx + 1
                progress["current_source"] = src["name"]
                progress["phase"] = "fetching"
                progress["analyzing_idx"] = 0
                progress["analyzing_total"] = 0
            try:
                items = self._fetch_source(src)
            except Exception:
                items = []
            valid_items = [
                it for it in items
                if normalize_text(it.get("title", "")) and normalize_text(it.get("url", ""))
            ]
            # 批量过滤已存在条目，只对真正新的才调 AI
            candidate_keys = [f"{src['name']}|{normalize_text(it.get('url', ''))}" for it in valid_items]
            already_exists = self.repo.existing_keys(candidate_keys)
            new_items = [it for it, k in zip(valid_items, candidate_keys) if k not in already_exists]
            skipped = len(valid_items) - len(new_items)
            if skipped:
                log(f"来源 {src['name']} 跳过已存在 {skipped} 条，待分析新增 {len(new_items)} 条")
            if progress is not None:
                progress["phase"] = "analyzing"
                progress["analyzing_total"] = len(new_items)
                progress["analyzing_idx"] = 0
            for ai_idx, item in enumerate(new_items):
                seen += 1
                if progress is not None:
                    progress["fetched"] = seen
                    progress["analyzing_idx"] = ai_idx + 1
                title = normalize_text(item.get("title", ""))
                summary = normalize_text(item.get("summary", ""))
                url = item.get("url", "")
                merged = f"{title} {summary}"
                persons = []
                tickers = map_tickers(merged)
                event = {
                    "source_name": src["name"],
                    "title": title,
                    "url": url,
                    "published_at": item.get("published_at"),
                    "captured_at": utc_now_iso(),
                    "summary": summary or title,
                    "persons": persons,
                    "tickers": tickers,
                    "impact": "",
                    "why": "",
                    "error_detail": "",
                    "horizon": "",
                    "confidence": 0,
                    "analysis_status": "pending",
                    "analysis_started_at": None,
                    "analysis_finished_at": None,
                    "unique_key": f"{src['name']}|{url}",
                }
                event_id = self.repo.insert_event(event)
                if event_id is not None:
                    _enqueue_analysis({
                        "id": event_id,
                        "title": title,
                        "summary": summary,
                        "persons": persons,
                        "tickers": tickers,
                    })
                    inserted += 1
                    if progress is not None:
                        progress["inserted"] = inserted
        return {"seen": seen, "inserted": inserted}

    def _fetch_source(self, src: dict[str, str]) -> list[dict[str, str]]:
        try:
            if src["type"] == "rss":
                items = self._fetch_rss(src["url"])
                log(f"来源 {src['name']} 抓取完成，共 {len(items)} 条")
                return items
            if src["type"] == "json":
                items = self._fetch_nvidia_json(src["url"])
                log(f"来源 {src['name']} 抓取完成，共 {len(items)} 条")
                return items
        except Exception as e:
            log(f"来源 {src['name']} 抓取失败: {e}")
        return []

    def _fetch_rss(self, url: str) -> list[dict[str, str]]:
        raw = http_get(url)
        root = ET.fromstring(raw)
        items: list[dict[str, str]] = []

        for item in root.findall(".//item")[:40]:
            title = normalize_text((item.findtext("title") or ""))
            link = normalize_text((item.findtext("link") or ""))
            desc = normalize_text((item.findtext("description") or ""))
            pub_date = normalize_text((item.findtext("pubDate") or ""))
            items.append({"title": title, "url": link, "summary": desc, "published_at": pub_date})

        # Atom fallback
        if not items:
            atom_ns = "{http://www.w3.org/2005/Atom}"
            for entry in root.findall(f".//{atom_ns}entry")[:40]:
                title = normalize_text((entry.findtext(f"{atom_ns}title") or ""))
                link_node = entry.find(f"{atom_ns}link")
                link = normalize_text(link_node.attrib.get("href", "") if link_node is not None else "")
                summary = normalize_text((entry.findtext(f"{atom_ns}summary") or entry.findtext(f"{atom_ns}content") or ""))
                pub = normalize_text((entry.findtext(f"{atom_ns}updated") or ""))
                items.append({"title": title, "url": link, "summary": summary, "published_at": pub})
        return items

    def _fetch_nvidia_json(self, url: str) -> list[dict[str, str]]:
        raw = http_get(url)
        payload = json.loads(raw.decode("utf-8", errors="ignore"))
        results = payload.get("items") or payload.get("posts") or []
        items = []
        for it in results[:40]:
            title = normalize_text(str(it.get("title", "")))
            link = normalize_text(str(it.get("permalink", "") or it.get("url", "")))
            summary = normalize_text(str(it.get("excerpt", "") or it.get("content", "")))
            pub = normalize_text(str(it.get("date", "") or it.get("published", "")))
            if link and link.startswith("/"):
                link = f"https://nvidianews.nvidia.com{link}"
            items.append({"title": title, "url": link, "summary": summary, "published_at": pub})
        return items


load_env_file(BASE_DIR / ".env")

repo = Repo(DB_PATH)
analyzer = Analyzer()
collector = Collector(repo, analyzer)

# ── 股价缓存 ──────────────────────────────────────────────
_quote_cache: dict[str, dict[str, Any]] = {}   # symbol -> {data, ts}
_QUOTE_TTL = 60  # 秒

_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

def _fetch_quote_stooq(symbol: str, retries: int = 2) -> dict[str, Any] | None:
    """从 stooq.com 获取单只股票行情，失败最多重试 retries 次。"""
    sym_lower = symbol.lower()
    url = f"https://stooq.com/q/l/?s={sym_lower}.us&f=sd2t2ohlcvn"
    for attempt in range(retries + 1):
        try:
            if attempt > 0:
                time.sleep(0.6 * attempt)
            req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", errors="ignore").strip()
            parts = raw.split(",")
            if len(parts) < 8:
                continue
            close_str = parts[6].strip()
            if close_str in ("N/D", "", "-"):
                continue
            close = float(close_str)
            open_ = float(parts[3])
            high  = float(parts[4])
            low   = float(parts[5])
            vol_str = parts[7].strip()
            vol   = int(vol_str) if vol_str.lstrip("-").isdigit() else 0
            name  = parts[8].strip() if len(parts) > 8 else symbol
            change_pct = (close - open_) / open_ * 100 if open_ else 0
            return {
                "symbol": symbol.upper(),
                "name": name,
                "price": close,
                "open": open_,
                "high": high,
                "low": low,
                "volume": vol,
                "change_pct": round(change_pct, 2),
                "date": parts[1].strip(),
                "time": parts[2].strip(),
            }
        except Exception as e:
            if attempt == retries:
                log(f"股票 {symbol} 行情抓取失败，重试 {retries+1} 次后仍失败: {e}")
    return None

def get_quotes(symbols: list[str]) -> dict[str, Any]:
    """并发批量获取股价，优先走缓存。"""
    now = time.time()
    result: dict[str, Any] = {}
    to_fetch = []
    for sym in symbols:
        sym = sym.upper()
        cached = _quote_cache.get(sym)
        if cached and now - cached["ts"] < _QUOTE_TTL:
            result[sym] = cached["data"]
        else:
            to_fetch.append(sym)

    if to_fetch:
        for i, sym in enumerate(to_fetch):
            if i > 0:
                time.sleep(0.4)  # stooq 限速保护
            data = _fetch_quote_stooq(sym)
            if data:
                _quote_cache[sym] = {"data": data, "ts": now}
            result[sym] = data

    return result

# 全局抓取进度状态
_fetch_progress: dict[str, Any] = {
    "running": False,
    "total": len(SOURCES),
    "current_idx": 0,
    "current_source": "",
    "phase": "",          # "fetching" | "analyzing"
    "analyzing_idx": 0,   # 当前源正在分析第几条
    "analyzing_total": 0, # 当前源共几条需要分析
    "fetched": 0,
    "inserted": 0,
    "done": False,
    "started_at": "",
    "finished_at": "",
}

# 全局 AI 分析进度状态
_analysis_queue: Queue[dict[str, Any]] = Queue()
_analysis_progress: dict[str, Any] = {
    "running": False,
    "current_event_id": 0,
    "current_title": "",
    "done": 0,
    "failed": 0,
}
_analysis_lock = threading.Lock()
_analysis_worker_started = False
_analysis_enqueued_ids: set[int] = set()


def _ensure_analysis_worker() -> None:
    global _analysis_worker_started
    with _analysis_lock:
        if _analysis_worker_started:
            return
        t = threading.Thread(target=_analysis_worker_loop, daemon=True)
        t.start()
        _analysis_worker_started = True


def _enqueue_analysis(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    with _analysis_lock:
        if task_id in _analysis_enqueued_ids:
            return
        _analysis_enqueued_ids.add(task_id)
    _ensure_analysis_worker()
    _analysis_queue.put(task)


def _analysis_worker_loop() -> None:
    while True:
        task = _analysis_queue.get()
        event_id = int(task["id"])
        title = normalize_text(task.get("title", ""))
        summary = normalize_text(task.get("summary", ""))
        persons = task.get("persons", []) or []
        tickers = task.get("tickers", []) or []
        _analysis_progress["running"] = True
        _analysis_progress["current_event_id"] = event_id
        _analysis_progress["current_title"] = title
        try:
            repo.mark_event_analyzing(event_id)
            ai = analyzer.analyze(title=title, summary=summary, persons=persons, tickers=tickers)
            status = "failed" if ai.get("error_detail") else "done"
            repo.finish_event_analysis(event_id, ai, status=status)
            if status == "failed":
                _analysis_progress["failed"] += 1
            else:
                _analysis_progress["done"] += 1
        except Exception as e:
            fallback = {
                "summary": summary or title,
                "impact": "mixed",
                "why": "模型调用失败，无法判断",
                "error_detail": str(e)[:2000],
                "horizon": "intraday",
                "confidence": 0,
            }
            repo.finish_event_analysis(event_id, fallback, status="failed")
            _analysis_progress["failed"] += 1
        finally:
            _analysis_queue.task_done()
            with _analysis_lock:
                _analysis_enqueued_ids.discard(event_id)
            if _analysis_queue.qsize() == 0:
                _analysis_progress["running"] = False
                _analysis_progress["current_event_id"] = 0
                _analysis_progress["current_title"] = ""


def _recover_pending_analysis_tasks() -> int:
    """启动恢复：把遗留 pending/analyzing 任务重新入队。"""
    recovered_analyzing = repo.reset_stuck_analyzing()
    tasks = repo.list_pending_analysis_tasks(limit=2000)
    for t in tasks:
        _enqueue_analysis(t)
    if recovered_analyzing > 0 or tasks:
        log(f"分析任务恢复：回退 analyzing={recovered_analyzing}，重新入队 pending={len(tasks)}")
    return len(tasks)


def _get_progress_snapshot() -> dict[str, Any]:
    db_pending = repo.count_pending_analysis()
    return {
        **_fetch_progress,
        "analysis_running": bool(_analysis_progress["running"]),
        "analysis_current_event_id": _analysis_progress["current_event_id"],
        "analysis_current_title": _analysis_progress["current_title"],
        "analysis_done": _analysis_progress["done"],
        "analysis_failed": _analysis_progress["failed"],
        "analysis_pending": _analysis_queue.qsize(),
        "analysis_pending_db": db_pending,
    }


def _run_fetch_with_progress(label: str) -> dict[str, int]:
    global _fetch_progress
    _fetch_progress.update({
        "running": True,
        "total": len(SOURCES),
        "current_idx": 0,
        "current_source": "",
        "phase": "",
        "analyzing_idx": 0,
        "analyzing_total": 0,
        "fetched": 0,
        "inserted": 0,
        "done": False,
        "started_at": utc_now_iso(),
        "finished_at": "",
    })
    _analysis_progress["done"] = 0
    _analysis_progress["failed"] = 0
    _analysis_progress["current_event_id"] = 0
    _analysis_progress["current_title"] = ""
    try:
        result = collector.fetch_all(progress=_fetch_progress)
        _fetch_progress["fetched"] = result["seen"]
        _fetch_progress["inserted"] = result["inserted"]
        return result
    finally:
        _fetch_progress["running"] = False
        _fetch_progress["done"] = True
        _fetch_progress["finished_at"] = utc_now_iso()
        _fetch_progress["current_source"] = ""


def auto_refresh_loop() -> None:
    log(f"自动刷新循环已启动，间隔 {REFRESH_SECONDS} 秒")
    while True:
        try:
            started = time.time()
            result = _run_fetch_with_progress("auto")
            elapsed = round(time.time() - started, 2)
            log(f"自动刷新完成：扫描 {result['seen']} 条，新增 {result['inserted']} 条，耗时 {elapsed} 秒")
        except Exception as e:
            log(f"自动刷新失败: {e}")
            traceback.print_exc()
        time.sleep(REFRESH_SECONDS)


def boot_fetch_once() -> None:
    try:
        log("启动时首次抓取开始")
        started = time.time()
        result = _run_fetch_with_progress("boot")
        elapsed = round(time.time() - started, 2)
        log(f"启动时首次抓取完成：扫描 {result['seen']} 条，新增 {result['inserted']} 条，耗时 {elapsed} 秒")
    except Exception as e:
        log(f"启动时首次抓取失败: {e}")
        traceback.print_exc()


class Handler(BaseHTTPRequestHandler):
    server_version = "FinanceNewsDemo/0.1"

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ["/", ""] else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in target.parents and target != STATIC_DIR:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if target.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        else:
            ctype = "application/octet-stream"

        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        data = safe_json_loads(raw.decode("utf-8", errors="ignore"))
        if isinstance(data, dict):
            return data
        return {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "time": utc_now_iso(),
                    "model": analyzer.cfg.model,
                    "base_url": analyzer.cfg.base_url,
                    "api_key_configured": bool(analyzer.cfg.api_key),
                }
            )
            return
        if parsed.path == "/api/sources":
            self._send_json({"sources": SOURCES})
            return
        if parsed.path == "/api/events":
            q = urllib.parse.parse_qs(parsed.query)
            limit = int(q.get("limit", ["50"])[0])
            sort = q.get("sort", ["captured"])[0]
            self._send_json({"items": repo.list_events(limit=limit, sort=sort)})
            return
        if parsed.path == "/api/progress":
            self._send_json(_get_progress_snapshot())
            return
        if parsed.path == "/api/quotes":
            q = urllib.parse.parse_qs(parsed.query)
            raw_syms = q.get("symbols", [""])[0]
            symbols = [s.strip().upper() for s in raw_syms.split(",") if s.strip()]
            if not symbols:
                self._send_json({"error": "symbols required"}, status=400)
                return
            quotes = get_quotes(symbols)
            self._send_json({"quotes": quotes})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/refresh":
            try:
                result = _run_fetch_with_progress("manual")
                self._send_json({"ok": True, **result, "time": utc_now_iso()})
            except urllib.error.URLError as e:
                self._send_json({"ok": False, "error": f"network error: {e}"}, status=502)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/clear_events":
            try:
                deleted = repo.clear_events()
                self._send_json({"ok": True, "deleted": deleted, "time": utc_now_iso()})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/digest":
            try:
                body = self._read_json_body()
                hours = int(body.get("hours", 24))
                events = repo.list_recent_events(hours=hours)
                if not events:
                    self._send_json({"ok": True, "bullish": [], "bearish": [], "macro_summary": "过去24小时暂无新闻数据", "event_count": 0})
                    return
                result = analyzer.digest(events)
                if "error" in result:
                    self._send_json({"ok": False, "error": result["error"]}, status=500)
                    return
                self._send_json({"ok": True, **result, "event_count": len(events), "time": utc_now_iso()})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        self._send_json({"ok": False, "error": "not found"}, status=404)


if __name__ == "__main__":
    log(f"加载配置：MODEL={analyzer.cfg.model} BASE_URL={analyzer.cfg.base_url}")
    if not analyzer.cfg.api_key:
        log("警告：API_KEY 为空，分析结果将返回“无法判断”")
    if "/anthropic" in analyzer.cfg.base_url and not analyzer.cfg.base_url.endswith("/v1"):
        log("警告：BASE_URL 看起来不是 OpenAI 兼容地址，期望 Chat Completions 接口地址（通常以 /v1 结尾）")

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"HTTP 服务已启动，监听地址 http://{HOST}:{PORT}")

    # 先启动分析 worker 并恢复重启前遗留任务，避免 pending 永久不动
    _ensure_analysis_worker()
    _recover_pending_analysis_tasks()

    auto_thread = threading.Thread(target=auto_refresh_loop, daemon=True)
    auto_thread.start()

    boot_thread = threading.Thread(target=boot_fetch_once, daemon=True)
    boot_thread.start()

    httpd.serve_forever()
