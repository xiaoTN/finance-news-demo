#!/usr/bin/env python3
"""测试 fetch_all 抓取逻辑（跳过 LLM 分析）"""
from pathlib import Path
from server import Collector, Repo, SOURCES, map_tickers, normalize_text

# 创建一个 mock analyzer，跳过 LLM 调用
class MockAnalyzer:
    def analyze(self, title, summary, persons, tickers):
        return {
            "summary": normalize_text(summary)[:280] or normalize_text(title),
            "impact": "mixed",
            "why": "[测试模式] 跳过 LLM 分析",
            "error_detail": "",
            "horizon": "intraday",
            "confidence": 50,
        }

# 使用 mock analyzer
repo = Repo(Path("/tmp/test_events.db"))
analyzer = MockAnalyzer()
collector = Collector(repo, analyzer)

def test_fetch_only():
    """只测试抓取，不调用 LLM"""
    print(f"数据源: {len(SOURCES)} 个\n")
    for src in SOURCES:
        print(f"  - {src['name']} ({src['type']})")

    print("\n开始抓取...")
    result = collector.fetch_all()
    print(f"\n结果: seen={result['seen']}, inserted={result['inserted']}")
    return result

def test_single_source(name: str):
    """测试单个数据源"""
    src = next((s for s in SOURCES if s["name"] == name), None)
    if not src:
        print(f"未找到数据源: {name}")
        return

    print(f"测试数据源: {src['name']}")
    items = collector._fetch_source(src)
    print(f"获取到 {len(items)} 条新闻\n")

    for i, item in enumerate(items[:5]):  # 只显示前5条
        title = normalize_text(item.get("title", ""))
        summary = normalize_text(item.get("summary", ""))
        tickers = map_tickers(f"{title} {summary}")
        print(f"{i+1}. {title[:80]}...")
        if tickers:
            print(f"   股票: {tickers}")
        print()

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # 测试单个数据源: python test_fetch.py "Reuters Business"
        test_single_source(sys.argv[1])
    else:
        # 测试全部: python test_fetch.py
        test_fetch_only()
