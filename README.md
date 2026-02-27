# Finance News Analyst Demo (Web)

一个可运行的 Web Demo：自动抓取金融新闻与关键人物动态，并输出“利好谁/利空谁/为什么”。

## 功能

- 多源抓取（RSS + 官网 JSON 优先，避免反爬和付费墙）
- 关键词/实体识别（Trump, Musk, Jensen Huang, Powell 等）
- 影响标的映射（TSLA/NVDA/SPY/QQQ/XLE/XLF/DXY 等）
- AI 分析（OpenAI-compatible Chat Completions）
- 无 API Key 时，输出“无法判断”（`impact=mixed, confidence=0`）
- 实时看板（最近事件流 + 影响榜单）

## 启动

```bash
cd /Users/xiaotn/finance-news-demo
python3 server.py
```

访问：<http://127.0.0.1:8787>

## 服务端配置（推荐）

服务端启动时会自动读取项目根目录的 `.env` 文件（仅服务端可见）。

```bash
cp .env.example .env
```

然后编辑 `.env`：

```bash
MODEL=gpt-4o-mini
API_KEY=xxxx
BASE_URL=https://api.openai.com/v1
```

如果不配置 Key，系统会输出“无法判断”（`impact=mixed, confidence=0`），不再使用规则引擎。

## API

- `GET /api/health` 健康检查
- `POST /api/refresh` 立即抓取并分析一轮
- `POST /api/mock_tweets` 注入 4 位关键人物的模拟推特事件（用于演示）
  - 可选 JSON body：`{"person":"Elon Musk","text":"自定义动态内容"}`，用于模拟指定人物发布动态
- `POST /api/clear_events` 清空本地事件数据（演示重置）
- `GET /api/events?limit=50` 最近事件
- `GET /api/sources` 当前抓取源

## 说明

- Demo 优先“可跑通 + 可扩展”，未覆盖付费内容源（Bloomberg/WSJ/FT 正文）。
- X/Truth Social 生产级接入建议走官方 API 或稳定代理层。
