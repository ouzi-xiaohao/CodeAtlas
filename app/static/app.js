const api = "/api/v1";

const pageTitles = {
  query: "研发知识问答",
  impact: "代码变更分析",
  ingest: "仓库知识索引",
};

const escapeHtml = (value = "") =>
  String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 3200);
}

function setLoading(container, label) {
  container.innerHTML = `<div class="loading-state"><div class="loader"></div><span>${escapeHtml(label)}</span></div>`;
}

async function request(path, options = {}) {
  const { headers = {}, ...requestOptions } = options;
  const response = await fetch(`${api}${path}`, {
    ...requestOptions,
    headers: { "Content-Type": "application/json", "X-Teams": "default", ...headers },
  });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* response is not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    const page = button.dataset.page;
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll(".page").forEach((item) => item.classList.toggle("active", item.id === `page-${page}`));
    document.getElementById("page-title").textContent = pageTitles[page];
  });
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => { document.getElementById("question-input").value = button.dataset.question; });
});

function renderCitations(citations = []) {
  if (!citations.length) return "";
  return `<div class="subheading">来源引用 · ${citations.length}</div><div class="citation-list">${citations.map((item) => `
    <div class="citation">
      <div class="citation-top"><strong>${escapeHtml(item.label)}</strong><code>${escapeHtml(item.source_type)} · L${item.line_start || 1}</code></div>
      <p>${escapeHtml(item.excerpt || item.uri)}</p>
    </div>`).join("")}</div>`;
}

document.getElementById("query-submit").addEventListener("click", async () => {
  const button = document.getElementById("query-submit");
  const result = document.getElementById("query-result");
  const question = document.getElementById("question-input").value.trim();
  if (!question) return showToast("请先输入问题");
  button.disabled = true;
  setLoading(result, "正在检索代码、文档与知识图谱…");
  try {
    const repository = document.getElementById("query-repository").value.trim();
    const data = await request("/query", {
      method: "POST",
      body: JSON.stringify({ question, repositories: repository ? [repository] : [], top_k: Number(document.getElementById("query-top-k").value) }),
    });
    const paths = (data.related_paths || []).slice(0, 4).map((path) =>
      `<div class="path-list">${path.nodes.map((node) => `<span class="path-node">${escapeHtml(node.name)}</span>`).join("<span>→</span>")}</div>`
    ).join("");
    result.innerHTML = `
      <div class="answer-header"><div><span class="section-label">GROUNDED ANSWER</span><h3>分析结果</h3></div><span class="trace">${escapeHtml(data.trace_id.slice(0, 12))}</span></div>
      <div class="answer-body">${escapeHtml(data.answer)}</div>
      ${paths ? `<div class="subheading">关联路径</div>${paths}` : ""}
      ${renderCitations(data.citations)}`;
  } catch (error) {
    result.innerHTML = `<div class="empty-state"><h3>检索失败</h3><p>${escapeHtml(error.message)}</p></div>`;
    showToast(error.message);
  } finally { button.disabled = false; }
});

function renderItems(title, items = []) {
  if (!items.length) return "";
  return `<div class="result-section"><h4>${escapeHtml(title)} · ${items.length}</h4><div class="item-list">${items.map((item) => `
    <div class="impact-item"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.reason)}</span></div>`).join("")}</div></div>`;
}

document.getElementById("impact-submit").addEventListener("click", async () => {
  const button = document.getElementById("impact-submit");
  const result = document.getElementById("impact-result");
  const value = document.getElementById("impact-value").value.trim();
  if (!value) return showToast("请填写变更内容");
  button.disabled = true;
  setLoading(result, "正在分析依赖路径与风险…");
  try {
    const data = await request("/impact", {
      method: "POST",
      body: JSON.stringify({ input_type: document.getElementById("impact-type").value, value, repository: document.getElementById("impact-repository").value.trim() }),
    });
    result.innerHTML = `
      <div class="risk-card ${escapeHtml(data.risk_level)}"><span class="section-label">RISK LEVEL</span><h3>${escapeHtml(data.risk_level)}</h3><p>${escapeHtml(data.summary)}</p></div>
      ${renderItems("风险依据", (data.risk_reasons || []).map((reason, index) => ({ name: `依据 ${index + 1}`, reason })))}
      ${renderItems("影响范围", data.affected)}
      ${renderItems("建议测试", data.suggested_tests)}
      ${renderItems("历史问题", data.historical_issues)}
      ${renderCitations(data.citations)}`;
  } catch (error) {
    result.innerHTML = `<div class="empty-state"><h3>分析失败</h3><p>${escapeHtml(error.message)}</p></div>`;
    showToast(error.message);
  } finally { button.disabled = false; }
});

document.getElementById("impact-type").addEventListener("change", (event) => {
  const input = document.getElementById("impact-value");
  const examples = {
    description: "删除 orders.status 字段，并调整订单状态映射",
    diff: "diff --git a/src/OrderService.java b/src/OrderService.java\n...",
    commit: "输入本地仓库中的 Commit SHA",
    pull_request: "https://github.com/owner/repository/pull/123",
  };
  input.value = examples[event.target.value];
  input.placeholder = examples[event.target.value];
});

document.getElementById("ingest-submit").addEventListener("click", async () => {
  const button = document.getElementById("ingest-submit");
  const result = document.getElementById("ingest-result");
  const scope = document.getElementById("ingest-scope").value;
  const paths = scope === "all" ? [] : document.getElementById("ingest-paths").value.split("\n").map((item) => item.trim()).filter(Boolean);
  button.disabled = true;
  result.classList.remove("hidden");
  result.textContent = "正在解析文件并更新索引…";
  try {
    const data = await request("/ingest", {
      method: "POST",
      headers: { "X-Teams": document.getElementById("ingest-team").value.trim() || "default" },
      body: JSON.stringify({ repository: document.getElementById("ingest-repository").value.trim(), team: document.getElementById("ingest-team").value.trim() || "default", paths }),
    });
    result.innerHTML = `<strong>索引完成</strong><br>知识片段：${data.indexed_chunks}<br>图谱实体：${data.indexed_entities}<br>跳过文件：${data.skipped_files}`;
  } catch (error) {
    result.textContent = error.message;
    showToast(error.message);
  } finally { button.disabled = false; }
});

async function checkHealth() {
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");
  const detail = document.getElementById("status-detail");
  try {
    const data = await request("/health");
    dot.classList.add("online");
    text.textContent = "服务正常";
    detail.textContent = `${data.mode === "local" ? "本地" : "外部"}模式 · ${data.embedding_backend} · ${data.indexed_chunks} 个片段`;
  } catch (_) {
    dot.classList.remove("online");
    text.textContent = "连接异常";
    detail.textContent = "请确认后端服务已启动";
  }
}

checkHealth();
