const statusEl = document.getElementById("status");
const refreshBtn = document.getElementById("refreshBtn");
const reloadBtn = document.getElementById("reloadBtn");
const clearBtn = document.getElementById("clearBtn");
const mockPersonSelect = document.getElementById("mockPersonSelect");
const mockTextInput = document.getElementById("mockTextInput");
const mockSubmitBtn = document.getElementById("mockSubmitBtn");
const sortSelect = document.getElementById("sortSelect");
const mockCheckbox = document.getElementById("mockCheckbox");
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
    // 为 mock 数据添加明显标识
    if (item.source_name.startsWith("X (Mock")) {
      node.classList.add("mock-event");
      sourceEl.classList.add("mock-badge");
    }
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
  const includeMock = mockCheckbox ? mockCheckbox.checked : true;
  const res = await fetch(`/api/events?limit=80&sort=${sort}&mock=${includeMock}`);
  const data = await res.json();
  const items = data.items || [];
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
  } catch (e) {
    statusEl.textContent = `抓取失败: ${e.message}`;
    return;
  } finally {
    refreshBtn.disabled = false;
  }
  await loadEvents();
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
reloadBtn.addEventListener("click", loadEvents);
clearBtn.addEventListener("click", clearEventsNow);
mockSubmitBtn.addEventListener("click", submitCustomMockPost);
sortSelect.addEventListener("change", loadEvents);
mockCheckbox.addEventListener("change", loadEvents);
mockTextInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    submitCustomMockPost();
  }
});

loadEvents().catch((e) => {
  statusEl.textContent = `加载失败: ${e.message}`;
});
