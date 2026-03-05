#!/usr/bin/env python3
"""
大模型连通性测试脚本
用法：python3 test_model.py
"""

import os
import sys
import time


def load_env(path=".env"):
    """从 .env 文件加载环境变量（不覆盖已有的系统环境变量）。"""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = "✓" if ok else "✗"
    print(f"  {status} {label}", end="")
    if detail:
        print(f"  →  {detail}", end="")
    print()
    return ok


def main():
    load_env()

    print("=" * 55)
    print("  大模型连通性测试")
    print("=" * 55)

    # ── 1. 环境变量检查 ──────────────────────────────────────
    print("\n[1] 环境变量")
    api_key  = os.getenv("API_KEY", "").strip()
    base_url = os.getenv("BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model    = os.getenv("MODEL", "gpt-4o-mini").strip()

    check("BASE_URL", bool(base_url), base_url)
    check("MODEL",    bool(model),    model)
    key_ok = bool(api_key)
    check("API_KEY",  key_ok, f"{api_key[:12]}..." if key_ok else "未设置")

    if not key_ok:
        print("\n✗ API_KEY 为空，无法继续测试。请在 .env 中设置 API_KEY=sk-xxx")
        sys.exit(1)

    # ── 2. 基础连通（ping 模型，返回任意 JSON）──────────────
    print("\n[2] 基础连通（_call_model）")
    from server import Analyzer
    analyzer = Analyzer()

    t0 = time.time()
    try:
        result, raw = analyzer._call_model({
            "task": "connectivity_test",
            "required": {"reply": "string"},
            "input": {"question": "用一句中文打招呼"}
        })
        elapsed = time.time() - t0
        check("HTTP 请求", True, f"{elapsed:.1f}s")
        check("JSON 解析", isinstance(result, dict), str(result))
        check("字段返回",  "reply" in (result or {}), (result or {}).get("reply", ""))
    except Exception as e:
        elapsed = time.time() - t0
        check("HTTP 请求", False, f"{elapsed:.1f}s  {e}")
        print("\n✗ 基础连通失败，后续测试跳过")
        sys.exit(1)

    # ── 3. 完整分析链路（analyze，验证字段完整性）────────────
    print("\n[3] 完整分析链路（analyze）")
    t0 = time.time()
    try:
        ai = analyzer.analyze(
            title="Fed holds rates steady amid tariff uncertainty",
            summary="The Federal Reserve kept interest rates unchanged on Wednesday, "
                    "citing ongoing uncertainty from trade tariffs.",
            persons=["Powell"],
            tickers=["SPY", "TLT", "GLD"],
        )
        elapsed = time.time() - t0
        required = ["summary", "impact", "why", "horizon", "confidence"]
        all_present = all(k in ai for k in required)
        check("HTTP 请求",  True,        f"{elapsed:.1f}s")
        check("必填字段",   all_present, str({k: ai.get(k) for k in required}))
        check("中文摘要",   bool(ai.get("summary")), ai.get("summary", "")[:60])
        check("impact 值", ai.get("impact") in ("bullish","bearish","mixed"),
              ai.get("impact",""))
        check("horizon 值",ai.get("horizon") in ("intraday","swing","long_term"),
              ai.get("horizon",""))
        check("置信度",     isinstance(ai.get("confidence"), int),
              str(ai.get("confidence")))
    except Exception as e:
        elapsed = time.time() - t0
        check("HTTP 请求", False, f"{elapsed:.1f}s  {e}")
        sys.exit(1)

    print("\n" + "=" * 55)
    print("  全部测试通过，大模型服务正常 ✓")
    print("=" * 55)


if __name__ == "__main__":
    main()
