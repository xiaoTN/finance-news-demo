const statusEl = document.getElementById("status");
const refreshBtn = document.getElementById("refreshBtn");
const mockTweetsBtn = document.getElementById("mockTweetsBtn");
const reloadBtn = document.getElementById("reloadBtn");
const clearBtn = document.getElementById("clearBtn");
const mockPersonSelect = document.getElementById("mockPersonSelect");
const mockTextInput = document.getElementById("mockTextInput");
const mockSubmitBtn = document.getElementById("mockSubmitBtn");
const eventListEl = document.getElementById("eventList");
const tickerBoardEl = document.getElementById("tickerBoard");
const tpl = document.getElementById("eventTpl");

function impactText(v) {
  if (v === "bullish") return "利好";
  if (v === "bearish") return "利空";
  return "中性/分化";
}

function toLocal(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

function renderTickers(items) {
  const count = new Map();
  for (const item of items) {
    for (const t of item.tickers || []) {
      count.set(t, (count.get(t) || 0) + 1);
    }
  }
  const arr = [...count.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20);
  tickerBoardEl.innerHTML = arr.length
    ? arr.map(([t, n]) => `<span class="chip">${t} (${n})</span>`).join("")
    : `<span class="chip">暂无数据</span>`;
}

function renderEvents(items) {
  eventListEl.innerHTML = "";
  for (const item of items) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.querySelector(".source").textContent = item.source_name;
    const impactEl = node.querySelector(".impact");
    impactEl.textContent = impactText(item.impact);
    impactEl.classList.add(item.impact || "mixed");
    node.querySelector(".confidence").textContent = `置信度 ${item.confidence}`;
    node.querySelector(".title").textContent = item.title;
    node.querySelector(".summary").textContent = `摘要：${item.summary || "-"}`;
    node.querySelector(".why").textContent = `原因：${item.why || "-"}`;
    const persons = (item.persons || []).join(", ") || "无";
    const tickers = (item.tickers || []).join(", ") || "无";
    node.querySelector(".meta").textContent = `人物: ${persons} | 标的: ${tickers} | 周期: ${item.horizon} | 抓取时间: ${toLocal(item.captured_at)}`;
    const a = node.querySelector(".link");
    a.href = item.url;
    eventListEl.appendChild(node);
  }
}

async function loadEvents() {
  statusEl.textContent = "正在加载事件...";
  const res = await fetch("/api/events?limit=80");
  const data = await res.json();
  const items = data.items || [];
  renderTickers(items);
  renderEvents(items);
  statusEl.textContent = `最近 ${items.length} 条，更新时间 ${new Date().toLocaleTimeString()}`;
}

async function refreshNow() {
  refreshBtn.disabled = true;
  statusEl.textContent = "正在抓取并分析...";
  try {
    const res = await fetch("/api/refresh", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      statusEl.textContent = `抓取失败: ${data.error || "unknown"}`;
      return;
    }
    statusEl.textContent = `抓取完成: seen=${data.seen}, inserted=${data.inserted}`;
    await loadEvents();
  } finally {
    refreshBtn.disabled = false;
  }
}

async function mockTweetsNow() {
  mockTweetsBtn.disabled = true;
  statusEl.textContent = "正在注入模拟推特事件...";
  try {
    const res = await fetch("/api/mock_tweets", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      statusEl.textContent = `推特测试失败: ${data.error || "unknown"}`;
      return;
    }
    statusEl.textContent = `推特测试完成: seen=${data.seen}, inserted=${data.inserted}`;
    await loadEvents();
  } finally {
    mockTweetsBtn.disabled = false;
  }
}

async function submitCustomMockPost() {
  const person = (mockPersonSelect.value || "").trim();
  const text = (mockTextInput.value || "").trim();
  if (!person) {
    statusEl.textContent = "请选择人物";
    return;
  }
  if (!text) {
    statusEl.textContent = "请输入要模拟发布的动态内容";
    return;
  }

  mockSubmitBtn.disabled = true;
  statusEl.textContent = "正在发布模拟动态...";
  try {
    const res = await fetch("/api/mock_tweets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person, text }),
    });
    const data = await res.json();
    if (!data.ok) {
      statusEl.textContent = `发布失败: ${data.error || "unknown"}`;
      return;
    }
    statusEl.textContent = `发布成功: person=${person}, inserted=${data.inserted}`;
    mockTextInput.value = "";
    await loadEvents();
  } finally {
    mockSubmitBtn.disabled = false;
  }
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

refreshBtn.addEventListener("click", refreshNow);
mockTweetsBtn.addEventListener("click", mockTweetsNow);
reloadBtn.addEventListener("click", loadEvents);
clearBtn.addEventListener("click", clearEventsNow);
mockSubmitBtn.addEventListener("click", submitCustomMockPost);
mockTextInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    submitCustomMockPost();
  }
});

loadEvents().catch((e) => {
  statusEl.textContent = `加载失败: ${e.message}`;
});
