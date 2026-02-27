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
]

MOCK_TWEETS = {
    "Donald Trump": [
        "Tariffs on strategic imports may rise again to protect US manufacturing.",
        "Big tax cuts and deregulation agenda will support domestic growth and jobs.",
    ],
    "Elon Musk": [
        "Tesla FSD progress is accelerating, demand trends remain strong into next quarter.",
        "xAI and Tesla engineering collaboration can improve autonomy and cost efficiency.",
    ],
    "Jensen Huang": [
        "AI infrastructure demand remains very strong and supply is improving this year.",
        "NVIDIA software and networking momentum continues across enterprise and cloud partners.",
    ],
    "Jerome Powell": [
        "Disinflation has progressed, but policy decisions remain data dependent.",
        "Labor market is cooling gradually; we will act if inflation re-accelerates.",
    ],
}

PERSON_PATTERNS = {
    "Donald Trump": [r"\btrump\b", r"realdonaldtrump", r"truth social"],
    "Elon Musk": [r"\belon\b", r"\bmusk\b", r"tesla"],
    "Jensen Huang": [r"jensen huang", r"nvidia ceo", r"nvda"],
    "Jerome Powell": [r"powell", r"federal reserve", r"fed chair", r"fomc"],
}

TICKER_RULES = [
    (r"\btesla\b|\btsla\b|\bmusk\b", ["TSLA"]),
    (r"\bnvidia\b|\bnvda\b|\bjensen\b", ["NVDA", "AMD", "TSM"]),
    (r"\bfed\b|\bpowell\b|rate cut|inflation|labor market", ["SPY", "QQQ", "TLT", "DXY"]),
    (r"tariff|sanction|trade war|china", ["DXY", "XLI", "XLE"]),
    (r"oil|crude|opec", ["XLE"]),
    (r"bank|yield|treasury", ["XLF", "TLT"]),
]

BULLISH_WORDS = {
    "beat",
    "surge",
    "growth",
    "strong",
    "record",
    "raise guidance",
    "demand",
    "cooling inflation",
    "rate cut",
    "partnership",
}

BEARISH_WORDS = {
    "miss",
    "drop",
    "weak",
    "cut guidance",
    "lawsuit",
    "probe",
    "tariff",
    "sanction",
    "supply constraint",
    "hot inflation",
}


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def safe_json_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def extract_persons(text: str) -> list[str]:
    t = text.lower()
    found: list[str] = []
    for person, patterns in PERSON_PATTERNS.items():
        if any(re.search(p, t) for p in patterns):
            found.append(person)
    return found


def map_tickers(text: str) -> list[str]:
    t = text.lower()
    tickers: set[str] = set()
    for pattern, items in TICKER_RULES:
        if re.search(pattern, t):
            tickers.update(items)
    return sorted(tickers)


def rule_based_analysis(title: str, summary: str, persons: list[str], tickers: list[str]) -> dict[str, Any]:
    merged = f"{title} {summary}".lower()
    bull_score = sum(1 for w in BULLISH_WORDS if w in merged)
    bear_score = sum(1 for w in BEARISH_WORDS if w in merged)

    if bull_score > bear_score:
        impact = "bullish"
    elif bear_score > bull_score:
        impact = "bearish"
    else:
        impact = "mixed"

    if "rate cut" in merged or "cooling inflation" in merged:
        horizon = "swing"
    elif any(x in merged for x in ["tariff", "sanction", "probe"]):
        horizon = "intraday"
    else:
        horizon = "intraday"

    base_conf = 55
    if persons:
        base_conf += 10
    if tickers:
        base_conf += 10
    if bull_score or bear_score:
        base_conf += min(20, (bull_score + bear_score) * 5)

    reason_parts = []
    if persons:
        reason_parts.append(f"人物信号: {', '.join(persons)}")
    if tickers:
        reason_parts.append(f"影响标的: {', '.join(tickers)}")
    if bull_score:
        reason_parts.append(f"利好词 {bull_score} 个")
    if bear_score:
        reason_parts.append(f"利空词 {bear_score} 个")
    if not reason_parts:
        reason_parts.append("新闻信息偏中性，等待更多上下文确认")

    return {
        "summary": normalize_text(summary)[:280] or normalize_text(title),
        "impact": impact,
        "why": "；".join(reason_parts),
        "horizon": horizon,
        "confidence": min(95, base_conf),
    }


@dataclass
class ModelConfig:
    provider: str
    api_key: str
    model: str


class Analyzer:
    def __init__(self) -> None:
        provider = os.getenv("AI_PROVIDER", "openai").strip().lower()
        if provider == "gemini":
            model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            key = os.getenv("GEMINI_API_KEY", "")
        else:
            provider = "openai"
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            key = os.getenv("OPENAI_API_KEY", "")
        self.cfg = ModelConfig(provider=provider, api_key=key, model=model)

    def analyze(self, title: str, summary: str, persons: list[str], tickers: list[str]) -> dict[str, Any]:
        if not self.cfg.api_key:
            return self._unknown_analysis(title=title, summary=summary, reason="未配置大模型 API Key，无法判断")

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
            if self.cfg.provider == "openai":
                data = self._call_openai(prompt)
            else:
                data = self._call_gemini(prompt)
            if data and all(k in data for k in ["summary", "impact", "why", "horizon", "confidence"]):
                return data
        except Exception:
            return self._unknown_analysis(title=title, summary=summary, reason="模型调用失败，无法判断")
        return self._unknown_analysis(title=title, summary=summary, reason="模型返回无效，无法判断")

    def _unknown_analysis(self, title: str, summary: str, reason: str) -> dict[str, Any]:
        return {
            "summary": normalize_text(summary)[:280] or normalize_text(title),
            "impact": "mixed",
            "why": reason,
            "horizon": "intraday",
            "confidence": 0,
        }

    def _call_openai(self, prompt: dict[str, Any]) -> dict[str, Any] | None:
        url = "https://api.openai.com/v1/chat/completions"
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return safe_json_loads(content)

    def _call_gemini(self, prompt: dict[str, Any]) -> dict[str, Any] | None:
        q = urllib.parse.urlencode({"key": self.cfg.api_key})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.cfg.model}:generateContent?{q}"
        body = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": "Return JSON only with keys: summary, impact, why, horizon, confidence.\\n"
                            + json.dumps(prompt, ensure_ascii=False)
                        }
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        return safe_json_loads(text)


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
                  horizon TEXT,
                  confidence INTEGER,
                  unique_key TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_captured ON events(captured_at DESC)")

    def insert_event(self, event: dict[str, Any]) -> bool:
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO events (
                      source_name,title,url,published_at,captured_at,summary,persons,tickers,
                      impact,why,horizon,confidence,unique_key
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                        event.get("horizon", "intraday"),
                        int(event.get("confidence", 50)),
                        event["unique_key"],
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY captured_at DESC, id DESC LIMIT ?", (max(1, min(200, limit)),)
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
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
                    "horizon": r["horizon"],
                    "confidence": r["confidence"],
                }
            )
        return out

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

    def fetch_all(self) -> dict[str, int]:
        inserted = 0
        seen = 0
        for src in SOURCES:
            try:
                items = self._fetch_source(src)
            except Exception:
                items = []
            for item in items:
                seen += 1
                title = normalize_text(item.get("title", ""))
                summary = normalize_text(item.get("summary", ""))
                url = item.get("url", "")
                if not title or not url:
                    continue
                merged = f"{title} {summary}"
                persons = extract_persons(merged)
                tickers = map_tickers(merged)
                ai = self.analyzer.analyze(title=title, summary=summary, persons=persons, tickers=tickers)
                event = {
                    "source_name": src["name"],
                    "title": title,
                    "url": url,
                    "published_at": item.get("published_at"),
                    "captured_at": utc_now_iso(),
                    "summary": ai.get("summary", summary),
                    "persons": persons,
                    "tickers": tickers,
                    "impact": ai.get("impact", "mixed"),
                    "why": ai.get("why", ""),
                    "horizon": ai.get("horizon", "intraday"),
                    "confidence": int(ai.get("confidence", 50)),
                    "unique_key": f"{src['name']}|{url}",
                }
                if self.repo.insert_event(event):
                    inserted += 1
        return {"seen": seen, "inserted": inserted}

    def insert_mock_tweets(self) -> dict[str, int]:
        inserted = 0
        seen = 0
        now = utc_now_iso()
        for person, tweets in MOCK_TWEETS.items():
            if not tweets:
                continue
            seen += 1
            # Rotate deterministic samples so each click shows a different statement.
            pick = int(dt.datetime.now(dt.timezone.utc).timestamp()) % len(tweets)
            text = tweets[pick]
            title = f"[Mock Tweet] {person}: {text[:90]}"
            summary = text
            url_person = person.lower().replace(" ", "")
            url = f"https://x.com/{url_person}/status/mock-{int(time.time())}-{seen}"
            merged = f"{title} {summary}"
            persons = extract_persons(merged)
            if person not in persons:
                persons.append(person)
            tickers = map_tickers(merged)
            ai = self.analyzer.analyze(title=title, summary=summary, persons=persons, tickers=tickers)
            event = {
                "source_name": "X (Mock)",
                "title": title,
                "url": url,
                "published_at": now,
                "captured_at": now,
                "summary": ai.get("summary", summary),
                "persons": sorted(set(persons)),
                "tickers": tickers,
                "impact": ai.get("impact", "mixed"),
                "why": ai.get("why", ""),
                "horizon": ai.get("horizon", "intraday"),
                "confidence": int(ai.get("confidence", 50)),
                "unique_key": f"mock_tweet|{person}|{int(time.time())}|{seen}",
            }
            if self.repo.insert_event(event):
                inserted += 1
        return {"seen": seen, "inserted": inserted}

    def _fetch_source(self, src: dict[str, str]) -> list[dict[str, str]]:
        if src["type"] == "rss":
            return self._fetch_rss(src["url"])
        if src["type"] == "json":
            return self._fetch_nvidia_json(src["url"])
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


repo = Repo(DB_PATH)
analyzer = Analyzer()
collector = Collector(repo, analyzer)


def auto_refresh_loop() -> None:
    while True:
        try:
            collector.fetch_all()
        except Exception:
            traceback.print_exc()
        time.sleep(REFRESH_SECONDS)


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

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "time": utc_now_iso(), "provider": analyzer.cfg.provider})
            return
        if parsed.path == "/api/sources":
            self._send_json({"sources": SOURCES})
            return
        if parsed.path == "/api/events":
            q = urllib.parse.parse_qs(parsed.query)
            limit = int(q.get("limit", ["50"])[0])
            self._send_json({"items": repo.list_events(limit=limit)})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/refresh":
            try:
                result = collector.fetch_all()
                self._send_json({"ok": True, **result, "time": utc_now_iso()})
            except urllib.error.URLError as e:
                self._send_json({"ok": False, "error": f"network error: {e}"}, status=502)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/mock_tweets":
            try:
                result = collector.insert_mock_tweets()
                self._send_json({"ok": True, **result, "time": utc_now_iso()})
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
        self._send_json({"ok": False, "error": "not found"}, status=404)


if __name__ == "__main__":
    t = threading.Thread(target=auto_refresh_loop, daemon=True)
    t.start()

    # boot fetch to populate initial rows
    try:
        collector.fetch_all()
    except Exception:
        traceback.print_exc()

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Server running at http://{HOST}:{PORT}")
    print(f"AI provider: {analyzer.cfg.provider} model={analyzer.cfg.model}")
    httpd.serve_forever()
