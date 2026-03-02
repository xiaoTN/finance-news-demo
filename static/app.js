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

function renderEvents(items) {
  eventListEl.innerHTML = "";
  for (const item of items) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    const sourceEl = node.querySelector(".source");
    sourceEl.textContent = item.source_name;
    const impactEl = node.querySelector(".impact");
    const impact = impactText(item.impact);
    impactEl.textContent = impact;
    if (impact) {
      impactEl.classList.add(item.impact);
      impactEl.style.display = "inline";
    } else {
      impactEl.style.display = "none";
    }
    node.querySelector(".title").textContent = item.title;
    node.querySelector(".summary").textContent = `摘要：${item.summary || "-"}`;
    node.querySelector(".why").textContent = `原因：${item.why || "-"}`;
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
    const tickers = (item.tickers || []).join(", ") || "无";
    const publishedStr = item.published_at ? `发布: ${toLocal(item.published_at)} | ` : "";
    node.querySelector(".meta").textContent = `人物: ${persons} | 标的: ${tickers} | 周期: ${item.horizon} | ${publishedStr}抓取: ${toLocal(item.captured_at)}`;
    const a = node.querySelector(".link");
    a.href = item.url;
    eventListEl.appendChild(node);
  }
}

async function loadEvents() {
  statusEl.textContent = "正在加载事件...";
  const sort = sortSelect ? sortSelect.value : "captured";
  const res = await fetch(`/api/events?limit=80&sort=${sort}`);
  const data = await res.json();
  const items = data.items || [];
  renderEvents(items);
  statusEl.textContent = `最近 ${items.length} 条，更新时间 ${new Date().toLocaleTimeString()}`;
}

let _progressTimer = null;

function startProgressPolling() {
  if (_progressTimer) return;
  _progressTimer = setInterval(async () => {
    try {
      const res = await fetch("/api/progress");
      const p = await res.json();
      if (p.running) {
        const total = p.total || 1;
        const idx = p.current_idx || 0;
        const src = p.current_source || "";
        let phaseStr = "";
        if (p.phase === "fetching") {
          phaseStr = `拉取中`;
        } else if (p.phase === "analyzing") {
          const at = p.analyzing_total || 0;
          const ai = p.analyzing_idx || 0;
          phaseStr = `AI分析 ${ai}/${at}`;
        }
        statusEl.textContent = `[${idx}/${total}] ${src} ${phaseStr} | 累计 ${p.fetched} 条，新增 ${p.inserted} 条`;
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
  statusEl.textContent = "正在抓取并分析...";
  startProgressPolling();
  try {
    const res = await fetch("/api/refresh", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      statusEl.textContent = `抓取失败: ${data.error || "unknown"}`;
      return;
    }
    statusEl.textContent = `抓取完成：共扫描 ${data.seen} 条，新增 ${data.inserted} 条`;
  } catch (e) {
    statusEl.textContent = `抓取失败: ${e.message}`;
    return;
  } finally {
    stopProgressPolling();
    refreshBtn.disabled = false;
  }
  await loadEvents();
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

function renderDigestItems(container, items) {
  container.innerHTML = "";
  if (!items || items.length === 0) {
    container.innerHTML = '<p class="digest-empty">暂无</p>';
    return;
  }
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "digest-item";
    card.innerHTML = `
      <div class="digest-ticker">${item.ticker || "-"}</div>
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
    renderDigestItems(digestBullish, data.bullish || []);
    renderDigestItems(digestBearish, data.bearish || []);
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
