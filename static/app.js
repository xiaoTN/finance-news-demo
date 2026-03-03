const statusEl = document.getElementById("status");
const refreshBtn = document.getElementById("refreshBtn");
const reloadBtn = document.getElementById("reloadBtn");
const clearBtn = document.getElementById("clearBtn");
const digestBtn = document.getElementById("digestBtn");
const digestPanel = document.getElementById("digestPanel");
const digestCloseBtn = document.getElementById("digestCloseBtn");
const digestMacro = document.getElementById("digestMacro");
const digestMeta = document.getElementById("digestMeta");
const digestBullish = document.getElementById("digestBullish");
const digestBearish = document.getElementById("digestBearish");
const sortSelect = document.getElementById("sortSelect");
const eventListEl = document.getElementById("eventList");
const tpl = document.getElementById("eventTpl");
let _loadingEvents = false;
let _lastEventsRefreshAt = 0;

function impactText(v) {
  if (v === "bullish") return "利好";
  if (v === "bearish") return "利空";
  return "";
}

function toLocal(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

async function copyText(text) {
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
}

// ── 股价查询与 badge ─────────────────────────────────────

const _quoteCache = {};  // symbol -> {data, ts}
const QUOTE_TTL = 65000; // ms，略长于后端缓存

async function fetchQuotes(symbols) {
  if (!symbols || symbols.length === 0) return {};
  const uniq = [...new Set(symbols.map(s => s.toUpperCase()))];
  const now = Date.now();
  const needed = uniq.filter(s => !_quoteCache[s] || now - _quoteCache[s].ts > QUOTE_TTL);
  if (needed.length > 0) {
    try {
      const res = await fetch(`/api/quotes?symbols=${needed.join(",")}`);
      const data = await res.json();
      const quotes = data.quotes || {};
      for (const [sym, q] of Object.entries(quotes)) {
        _quoteCache[sym] = { data: q, ts: now };
      }
    } catch (_) {}
  }
  const result = {};
  for (const s of uniq) {
    result[s] = _quoteCache[s]?.data || null;
  }
  return result;
}

function makePriceCard(sym, q) {
  const card = document.createElement("div");
  card.className = "price-card";
  if (!q) {
    card.innerHTML = `<span class="pc-sym">${sym}</span><span class="pc-na">暂无数据</span>`;
    return card;
  }
  const sign = q.change_pct >= 0 ? "+" : "";
  const cls = q.change_pct >= 0 ? "up" : "down";
  card.innerHTML = `
    <span class="pc-sym">${q.symbol}</span>
    <span class="pc-name">${q.name}</span>
    <span class="pc-price">$${q.price.toFixed(2)}</span>
    <span class="pc-chg ${cls}">${sign}${q.change_pct.toFixed(2)}%</span>
    <span class="pc-detail">开 $${q.open.toFixed(2)} · 高 $${q.high.toFixed(2)} · 低 $${q.low.toFixed(2)} · 量 ${(q.volume/1e6).toFixed(1)}M</span>
    <span class="pc-time">${q.date} ${q.time}</span>
  `;
  return card;
}

function makeTickerBadge(sym, containerEl) {
  const badge = document.createElement("button");
  badge.className = "ticker-badge";
  badge.textContent = sym;
  badge.type = "button";
  let expanded = false;
  let priceEl = null;

  badge.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (expanded && priceEl) {
      priceEl.remove();
      priceEl = null;
      expanded = false;
      badge.classList.remove("active");
      return;
    }
    badge.classList.add("active");
    badge.textContent = sym + " …";
    const quotes = await fetchQuotes([sym]);
    badge.textContent = sym;
    priceEl = makePriceCard(sym, quotes[sym]);
    badge.after(priceEl);
    expanded = true;
  });
  return badge;
}

function renderTickerRow(container, tickers) {
  container.innerHTML = "";
  if (!tickers || tickers.length === 0) return;
  for (const sym of tickers) {
    container.appendChild(makeTickerBadge(sym, container));
  }
}

function renderEvents(items) {
  eventListEl.innerHTML = "";
  for (const item of items) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.querySelector(".source").textContent = item.source_name;
    const analysisStatus = item.analysis_status || "pending";
    const progressEl = node.querySelector(".analysis-progress");
    const whyEl = node.querySelector(".why");
    const impactEl = node.querySelector(".impact");
    const impact = analysisStatus === "done" ? impactText(item.impact) : "";
    impactEl.textContent = impact;
    if (impact) {
      impactEl.classList.add(item.impact);
      impactEl.style.display = "inline";
    } else {
      impactEl.style.display = "none";
    }
    node.querySelector(".title").textContent = item.title;
    node.querySelector(".summary").textContent = `摘要：${item.summary || "-"}`;
    if (analysisStatus === "pending") {
      progressEl.textContent = "分析进度：等待 AI 分析";
      progressEl.className = "analysis-progress pending";
      whyEl.textContent = "原因：分析完成后显示";
    } else if (analysisStatus === "analyzing") {
      progressEl.textContent = "分析进度：AI 分析中...";
      progressEl.className = "analysis-progress analyzing";
      whyEl.textContent = "原因：分析中";
    } else if (analysisStatus === "failed") {
      progressEl.textContent = "分析进度：分析失败（已回退默认结论）";
      progressEl.className = "analysis-progress failed";
      whyEl.textContent = `原因：${item.why || "模型调用失败，无法判断"}`;
    } else {
      const confidence = Number(item.confidence || 0);
      progressEl.textContent = `分析进度：已完成（置信度 ${confidence}）`;
      progressEl.className = "analysis-progress done";
      whyEl.textContent = `原因：${item.why || "-"}`;
    }
    const errorDetail = (item.error_detail || "").trim();
    const copyBtn = node.querySelector(".copy-error-btn");
    if (errorDetail) {
      copyBtn.hidden = false;
      copyBtn.addEventListener("click", async () => {
        const detail = [
          `title=${item.title || ""}`,
          `source=${item.source_name || ""}`,
          `time=${item.captured_at || ""}`,
          `model_error=${errorDetail}`,
        ].join("\n");
        try {
          await copyText(detail);
          statusEl.textContent = "失败详情已复制";
        } catch (e) {
          statusEl.textContent = `复制失败: ${e.message}`;
        }
      });
    } else {
      copyBtn.hidden = true;
    }
    const persons = (item.persons || []).join(", ") || "无";
    const publishedStr = item.published_at ? `发布: ${toLocal(item.published_at)} | ` : "";
    node.querySelector(".meta").textContent = `人物: ${persons} | 周期: ${item.horizon} | ${publishedStr}抓取: ${toLocal(item.captured_at)}`;
    // ticker badges
    renderTickerRow(node.querySelector(".ticker-row"), item.tickers || []);
    node.querySelector(".link").href = item.url;
    eventListEl.appendChild(node);
  }
}

async function loadEvents(options = {}) {
  const silent = !!options.silent;
  if (_loadingEvents) return;
  _loadingEvents = true;
  if (!silent) {
    statusEl.textContent = "正在加载事件...";
  }
  const sort = sortSelect ? sortSelect.value : "captured";
  try {
    const res = await fetch(`/api/events?limit=80&sort=${sort}`);
    const data = await res.json();
    const items = data.items || [];
    renderEvents(items);
    _lastEventsRefreshAt = Date.now();
    if (!silent) {
      statusEl.textContent = `最近 ${items.length} 条，更新时间 ${new Date().toLocaleTimeString()}`;
    }
  } finally {
    _loadingEvents = false;
  }
}

let _progressTimer = null;

function startProgressPolling() {
  if (_progressTimer) return;
  _progressTimer = setInterval(async () => {
    try {
      const res = await fetch("/api/progress");
      const p = await res.json();
      const hasFetch = !!p.running;
      const hasAnalysis = !!p.analysis_running || (p.analysis_pending || 0) > 0;
      if (hasFetch || hasAnalysis) {
        const total = p.total || 1;
        const idx = p.current_idx || 0;
        const src = p.current_source || "";
        const pending = p.analysis_pending_db ?? p.analysis_pending ?? 0;
        const done = p.analysis_done || 0;
        const failed = p.analysis_failed || 0;
        if (hasFetch) {
          let phaseStr = "";
          if (p.phase === "fetching") {
            phaseStr = "拉取中";
          } else if (p.phase === "analyzing") {
            const at = p.analyzing_total || 0;
            const ai = p.analyzing_idx || 0;
            phaseStr = `入队 ${ai}/${at}`;
          }
          statusEl.textContent = `[${idx}/${total}] ${src} ${phaseStr} | 累计 ${p.fetched} 条，新增 ${p.inserted} 条 | AI待分析 ${pending}`;
        } else {
          const currentTitle = p.analysis_current_title ? `，当前：${p.analysis_current_title.slice(0, 28)}` : "";
          statusEl.textContent = `抓取完成，AI 后台分析中 | 待分析 ${pending}，已完成 ${done}，失败 ${failed}${currentTitle}`;
        }
        if (Date.now() - _lastEventsRefreshAt > 1500) {
          await loadEvents({ silent: true });
        }
      } else if (_progressTimer) {
        stopProgressPolling();
        await loadEvents({ silent: true });
        statusEl.textContent = `抓取与分析已完成，更新时间 ${new Date().toLocaleTimeString()}`;
      }
    } catch (_) {}
  }, 800);
}

function stopProgressPolling() {
  if (_progressTimer) {
    clearInterval(_progressTimer);
    _progressTimer = null;
  }
}

async function refreshNow() {
  refreshBtn.disabled = true;
  statusEl.textContent = "正在抓取，AI 将在后台异步分析...";
  startProgressPolling();
  try {
    const res = await fetch("/api/refresh", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      statusEl.textContent = `抓取失败: ${data.error || "unknown"}`;
      return;
    }
    statusEl.textContent = `抓取完成：共扫描 ${data.seen} 条，新增 ${data.inserted} 条，AI 分析后台进行中`;
  } catch (e) {
    statusEl.textContent = `抓取失败: ${e.message}`;
    return;
  } finally {
    refreshBtn.disabled = false;
  }
  await loadEvents({ silent: true });
}

async function clearEventsNow() {
  const ok = window.confirm("确认清空所有事件数据吗？此操作不可恢复。");
  if (!ok) return;
  clearBtn.disabled = true;
  statusEl.textContent = "正在清空数据...";
  try {
    const res = await fetch("/api/clear_events", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      statusEl.textContent = `清空失败: ${data.error || "unknown"}`;
      return;
    }
    statusEl.textContent = `清空完成: deleted=${data.deleted}`;
    await loadEvents();
  } finally {
    clearBtn.disabled = false;
  }
}

async function renderDigestItems(container, items) {
  container.innerHTML = "";
  if (!items || items.length === 0) {
    container.innerHTML = '<p class="digest-empty">暂无</p>';
    return;
  }
  // 预取所有 ticker 的股价
  const syms = items.map(i => i.ticker).filter(Boolean);
  const quotes = await fetchQuotes(syms);

  for (const item of items) {
    const sym = item.ticker || "-";
    const q = quotes[sym] || null;
    const card = document.createElement("div");
    card.className = "digest-item";

    let priceHtml = "";
    if (q) {
      const sign = q.change_pct >= 0 ? "+" : "";
      const cls = q.change_pct >= 0 ? "up" : "down";
      priceHtml = `
        <div class="digest-price-row">
          <span class="dpr-price">$${q.price.toFixed(2)}</span>
          <span class="dpr-chg ${cls}">${sign}${q.change_pct.toFixed(2)}%</span>
          <span class="dpr-detail">开 $${q.open.toFixed(2)} · 高 $${q.high.toFixed(2)} · 低 $${q.low.toFixed(2)} · 量 ${(q.volume/1e6).toFixed(1)}M</span>
        </div>
      `;
    } else {
      priceHtml = `<div class="digest-price-row"><span class="dpr-na">行情加载中…</span></div>`;
    }

    card.innerHTML = `
      <div class="digest-ticker-row">
        <span class="digest-ticker">${sym}</span>
        ${q ? `<span class="digest-fullname">${q.name}</span>` : ""}
      </div>
      ${priceHtml}
      <div class="digest-reason">${item.reason || ""}</div>
      <div class="digest-keynews">${item.key_news || ""}</div>
    `;
    container.appendChild(card);
  }
}

async function runDigest() {
  digestBtn.disabled = true;
  statusEl.textContent = "正在生成 24h 快讯总结…";
  digestPanel.hidden = true;
  try {
    const res = await fetch("/api/digest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hours: 24 }),
    });
    const data = await res.json();
    if (!data.ok) {
      statusEl.textContent = `总结失败: ${data.error || "unknown"}`;
      return;
    }
    digestMacro.textContent = data.macro_summary || "";
    digestMeta.textContent = `基于过去 24h ${data.event_count} 条新闻`;
    await Promise.all([
      renderDigestItems(digestBullish, data.bullish || []),
      renderDigestItems(digestBearish, data.bearish || []),
    ]);
    digestPanel.hidden = false;
    digestPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    statusEl.textContent = `24h 总结完成，分析了 ${data.event_count} 条新闻`;
  } catch (e) {
    statusEl.textContent = `总结失败: ${e.message}`;
  } finally {
    digestBtn.disabled = false;
  }
}

refreshBtn.addEventListener("click", refreshNow);
reloadBtn.addEventListener("click", loadEvents);
digestBtn.addEventListener("click", runDigest);
digestCloseBtn.addEventListener("click", () => { digestPanel.hidden = true; });
clearBtn.addEventListener("click", clearEventsNow);
sortSelect.addEventListener("change", loadEvents);

loadEvents().catch((e) => {
  statusEl.textContent = `加载失败: ${e.message}`;
});
