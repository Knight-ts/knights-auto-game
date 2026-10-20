"use strict";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const ATTRS = ["力量", "体质", "技巧", "感知", "敏捷", "意志"];
const GRADES = ["", "E", "D", "C", "B", "A", "S", "SS"];

const state = {
  tasks: [], alerts: [], catalog: [], records: [],
  devices: [], selectedTask: 0, activeTab: "home", prefs: {}, prefsLoaded: false,
};
const timers = {};
let selectionTimer = null;

/* ------------------------------------------------------------ 基础 */

async function api(path, options) {
  const resp = await fetch(path, options);
  const type = resp.headers.get("Content-Type") || "";
  const data = type.includes("json") ? await resp.json() : null;
  if (!resp.ok) throw new Error((data && (data.error || data.errors?.join("；"))) || resp.statusText);
  return data;
}

function post(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
}

function banner(kind, lines) {
  const el = $("banner");
  el.className = "banner " + kind;
  el.innerHTML = lines.length > 1
    ? `<ul>${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>`
    : esc(lines[0] || "");
  clearTimeout(banner.timer);
  banner.timer = setTimeout(() => el.classList.add("hidden"), 8000);
}

function switchTab(name) {
  state.activeTab = name;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  ["home", "tasks", "shot", "logs", "alerts", "records", "mercs", "notify"].forEach((tab) => {
    $("tab-" + tab).classList.toggle("hidden", tab !== name);
  });
  clearInterval(timers.shot);
  clearInterval(timers.logs);
  if (name === "shot") { refreshShot(); timers.shot = setInterval(() => { if ($("shotAuto").checked) refreshShot(); }, 2000); }
  if (name === "logs") { loadLogs(); timers.logs = setInterval(() => { if ($("logAuto").checked) loadLogs(); }, 1500); }
  if (name === "tasks") loadTasks();
  if (name === "alerts") {
    if (!state.alerts.length) loadAlerts();
    loadChildSpeed();
  }
  if (name === "records") loadRecords();
  if (name === "mercs") { loadMercRules(); loadMercRecords(); }
  if (name === "notify") loadNotify();
}

/* ------------------------------------------------------------ 状态 */

async function refreshState() {
  try {
    const data = await api("/api/state");
    state.devices = data.devices || [];
    state.prefs = data.config || {};
    state.prefsLoaded = true;
    renderDevices(data);
    renderHistory(data.history || []);

    const connected = Boolean(data.device && data.device.address);
    $("chip-device").textContent = "设备：" + (connected ? data.device.address : "未连接");
    $("chip-device").className = "chip " + (connected ? "ok" : "bad");
    $("chip-resource").textContent = "资源：" + (data.resource ? "已加载" : "未加载");
    $("chip-resource").className = "chip " + (data.resource ? "ok" : "");
    $("chip-agent").textContent = "Agent：" + (data.agent ? "在线" : "未启动");
    $("chip-agent").className = "chip " + (data.agent ? "ok" : "bad");

    const task = data.task || { state: "idle", name: "" };
    $("chip-task").textContent = "任务：" + (task.state === "running" ? (task.name || "进行中") : task.state === "failed" ? "失败" : task.state === "done" ? "已完成" : "空闲");
    $("chip-task").className = "chip " + (task.state === "running" ? "warn" : task.state === "failed" ? "bad" : task.state === "done" ? "ok" : "");
    $("homeTask").textContent = task.name ? `${task.name}（${task.state}）` : "空闲";
    const game = data.game || {};
    $("homeGameTime").textContent = game.year ? `${game.year}年${game.month || "?"}月` : "未识别";
    $("homeState").textContent = connected ? (task.state === "running" ? "任务运行中" : "已就绪") : "未连接设备";
    $("homeState").className = "big " + (connected ? "ok" : "bad");
    $("homeResource").textContent = data.resource ? "已加载" : "未加载";
    $("homeAgent").textContent = data.agent ? "在线" : "未启动";
    $("homeDevice").textContent = connected ? `${data.device.address}（${data.device.adb_path}）` : "-";
    $("homeLogs").textContent = (data.logs || []).join("\n") || "暂无日志";
  } catch (error) {
    $("homeLogs").textContent = "读取状态失败：" + error.message;
  }
}

function renderDevices(data) {  const select = $("deviceSelect");
  const current = select.value;
  const options = (data.devices || []).map((d) =>
    `<option value="${esc(d.adb_path)}|${esc(d.address)}">${esc(d.name || d.address)}（${esc(d.address)}）</option>`);
  select.innerHTML = options.join("") || '<option value="">未发现设备</option>';
  const wanted = data.config && data.config.address
    ? `${data.config.adb_path}|${data.config.address}` : "";
  if (wanted && [...select.options].some((o) => o.value === wanted)) select.value = wanted;
  else if (current) select.value = current;
}

async function connectDevice() {
  const value = $("deviceSelect").value || "";
  const [adb_path, address] = value.split("|");
  if (!address) { banner("err", ["先选择一个设备"]); return; }
  try {
    $("btnConnect").disabled = true;
    banner("ok", ["正在连接 " + address + " …"]);
    await post("/api/connect", { adb_path, address });
    banner("ok", ["设备已连接"]);
    await refreshState();
  } catch (error) {
    banner("err", ["连接失败：" + error.message]);
  } finally {
    $("btnConnect").disabled = false;
  }
}

async function startAgent() {
  try {
    banner("ok", ["正在启动 Agent（需要几秒）…"]);
    await post("/api/agent/start", {});
    banner("ok", ["Agent 已连接"]);
    await refreshState();
  } catch (error) {
    banner("err", ["Agent 启动失败：" + error.message]);
  }
}

async function stopAgent() {
  try {
    await post("/api/agent/stop", {});
    banner("ok", ["Agent 已停止"]);
    await refreshState();
  } catch (error) {
    banner("err", [error.message]);
  }
}

/* ------------------------------------------------------------ 任务 */

async function loadTasks() {
  if (state.tasks.length) return;
  try {
    if (!state.prefsLoaded) await refreshState();
    state.tasks = await api("/api/tasks");
    renderTaskList();
    const last = (state.prefs || {}).last_task;
    const index = state.tasks.findIndex((t) => t.entry === last);
    if (state.tasks.length) selectTask(index >= 0 ? index : 0);
    renderLoopTasks();
  } catch (error) {
    banner("err", ["读取任务失败：" + error.message]);
  }
}

function renderHistory(items) {
  const body = $("historyBody");
  if (!body) return;
  body.innerHTML = items.length
    ? items.map((h) => `<tr>
        <td class="mono">${esc(h.time)}</td>
        <td>${esc(h.name)}</td>
        <td class="${h.state === "done" ? "ok" : "bad"}">${h.state === "done" ? "成功" : "失败"}</td>
        <td>${esc(h.seconds)}s</td>
        <td>${esc(h.error || "")}</td>
      </tr>`).join("")
    : '<tr><td colspan="5" class="empty">还没有执行记录</td></tr>';
}

function renderTaskList() {
  $("taskList").innerHTML = state.tasks.length
    ? state.tasks.map((t, i) =>
        `<button data-i="${i}" class="${i === state.selectedTask ? "active" : ""}">${esc(t.name)}</button>`).join("")
    : '<div class="empty">没有任务</div>';
}

function selectTask(index) {
  clearTimeout(selectionTimer);
  state.selectedTask = index;
  const task = state.tasks[index];
  if (!task) return;
  renderTaskList();
  $("taskTitle").textContent = task.name;
  $("taskDoc").textContent = (task.doc || []).join("\n") || task.description || "";
  $("taskOptions").innerHTML = "";
  task.options.forEach((option) => {
    const field = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = option.label || option.name;
    field.appendChild(legend);

    if (option.description) {
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = option.description;
      field.appendChild(hint);
    }

    const wrap = document.createElement("div");
    wrap.style.marginTop = "6px";

    if (option.type === "switch" || option.type === "select") {
      const select = document.createElement("select");
      select.id = `opt-${index}-${option.name}`;
      select.dataset.option = option.name;
      select.dataset.kind = option.type;
      option.cases.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.name;
        opt.textContent = c.description ? `${c.name} - ${c.description}` : c.name;
        select.appendChild(opt);
      });
      const dflt = Array.isArray(option.default_case) ? option.default_case[0] : option.default_case;
      if (dflt) select.value = dflt;
      wrap.appendChild(select);
    } else if (option.type === "checkbox") {
      const defaults = Array.isArray(option.default_case) ? option.default_case : [option.default_case].filter(Boolean);
      option.cases.forEach((c) => {
        const label = document.createElement("label");
        label.className = "opt";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = c.name;
        input.dataset.option = option.name;
        input.dataset.kind = "checkbox";
        input.checked = defaults.includes(c.name);
        label.appendChild(input);
        const text = document.createElement("span");
        text.textContent = c.description ? `${c.name} - ${c.description}` : c.name;
        label.appendChild(text);
        wrap.appendChild(label);
      });
    } else if (option.type === "input") {
      (option.inputs || []).forEach((item) => {
        const label = document.createElement("label");
        label.className = "field";
        label.style.marginBottom = "6px";
        const title = document.createElement("span");
        title.textContent = item.name + (item.description ? `（${item.description}）` : "");
        const input = document.createElement("input");
        input.type = "text";
        input.value = item.default || "";
        input.dataset.option = option.name;
        input.dataset.kind = "input";
        input.dataset.input = item.name;
        label.appendChild(title);
        label.appendChild(input);
        wrap.appendChild(label);
      });
    }
    field.appendChild(wrap);
    $("taskOptions").appendChild(field);
  });
  $("taskHint").textContent = task.description || "";
  applySelection(task);
}

function applySelection(task) {
  const saved = (state.prefs.selections || {})[task.entry];
  if (!saved) return;
  task.options.forEach((option) => {
    if (option.type === "switch" || option.type === "select") {
      const el = $(`opt-${state.selectedTask}-${option.name}`);
      if (el && saved[option.name] != null) el.value = saved[option.name];
    } else if (option.type === "checkbox") {
      const values = saved[option.name] || [];
      document.querySelectorAll(`input[data-kind="checkbox"][data-option="${CSS.escape(option.name)}"]`)
        .forEach((i) => { i.checked = values.includes(i.value); });
    } else if (option.type === "input") {
      const values = saved[option.name] || {};
      document.querySelectorAll(`input[data-kind="input"][data-option="${CSS.escape(option.name)}"]`)
        .forEach((i) => { if (values[i.dataset.input] != null) i.value = values[i.dataset.input]; });
    }
  });
}

function collectSelection() {
  const task = state.tasks[state.selectedTask];
  const selection = {};
  if (!task) return selection;
  task.options.forEach((option) => {
    if (option.type === "switch" || option.type === "select") {
      const el = $(`opt-${state.selectedTask}-${option.name}`);
      if (el) selection[option.name] = el.value;
    } else if (option.type === "checkbox") {
      selection[option.name] = [...document.querySelectorAll(`input[data-kind="checkbox"][data-option="${CSS.escape(option.name)}"]`)]
        .filter((i) => i.checked).map((i) => i.value);
    } else if (option.type === "input") {
      const values = {};
      document.querySelectorAll(`input[data-kind="input"][data-option="${CSS.escape(option.name)}"]`).forEach((i) => {
        values[i.dataset.input] = i.value;
      });
      selection[option.name] = values;
    }
  });
  return selection;
}

function scheduleSelectionSave() {
  clearTimeout(selectionTimer);
  selectionTimer = setTimeout(saveSelectionNow, 600);
}

async function saveSelectionNow() {
  const task = state.tasks[state.selectedTask];
  if (!task) return;
  const selection = collectSelection();
  try {
    await post("/api/task/selection/save", { entry: task.entry, selection });
    state.prefs.last_task = task.entry;
    state.prefs.selections = state.prefs.selections || {};
    state.prefs.selections[task.entry] = selection;
  } catch (error) {
    /* 自动保存失败不打扰用户，点“开始任务”时还会再存一次 */
  }
}

async function runTask() {
  const task = state.tasks[state.selectedTask];
  if (!task) { banner("err", ["先选择任务"]); return; }
  const selection = collectSelection();
  try {
    $("btnRun").disabled = true;
    await post("/api/task/run", { entry: task.entry, name: task.name, selection });
    state.prefs.last_task = task.entry;
    state.prefs.selections = state.prefs.selections || {};
    state.prefs.selections[task.entry] = selection;
    banner("ok", ["已开始：" + task.name]);
    await refreshState();
  } catch (error) {
    banner("err", ["启动失败：" + error.message]);
  } finally {
    $("btnRun").disabled = false;
  }
}

async function stopTask() {
  try {
    await post("/api/task/stop", {});
    banner("ok", ["已请求停止"]);
    await refreshState();
  } catch (error) {
    banner("err", [error.message]);
  }
}

/* ------------------------------------------------------------ 截图 / 日志 */

function refreshShot() {
  const img = $("shotImg");
  const empty = $("shotEmpty");
  const probe = new Image();
  probe.onload = () => {
    img.src = probe.src;
    img.style.display = "";
    empty.style.display = "none";
    $("shotHint").textContent = "更新于 " + new Date().toLocaleTimeString();
    $("shotDownload").href = probe.src;
  };
  probe.onerror = () => {
    img.style.display = "none";
    empty.style.display = "";
    empty.textContent = "截图失败：设备未连接";
  };
  probe.src = "/api/screenshot?t=" + Date.now();
}

async function loadLogs() {
  try {
    const data = await api("/api/logs?limit=400");
    const view = $("logView");
    const stick = view.scrollTop + view.clientHeight >= view.scrollHeight - 30;
    view.textContent = (data.logs || []).join("\n");
    if (stick) view.scrollTop = view.scrollHeight;
  } catch (error) {
    $("logView").textContent = "读取日志失败：" + error.message;
  }
}

/* ------------------------------------------------------------ 好娃提醒 */

function emptyAlert(index) {
  const n = index + 1;
  const grades = {}, mins = {};
  ATTRS.forEach((a) => { grades[a] = ""; mins[a] = ""; });
  return {
    id: "type_" + n, name: "新提醒类型", description: "",
    caseName: "新提醒类型 " + n, node: "检测_新提醒类型" + n,
    defaultChecked: false, features: [], grades, mins, orLines: [],
  };
}

const gradeOptions = (selected) =>
  GRADES.map((g) => `<option value="${g}" ${g === selected ? "selected" : ""}>${g || "—"}</option>`).join("");

function alertCard(condition, index) {
  const badges = [];
  if (!condition.nodeExists) badges.push('<span class="badge new">保存时创建检测节点</span>');
  if (!condition.caseExists) badges.push('<span class="badge new">保存时创建界面选项</span>');
  const chips = condition.features.map((f, fi) =>
    `<span class="chip2">${esc(f)}<button data-act="rm-feature" data-i="${index}" data-fi="${fi}">×</button></span>`).join("");
  const attrs = ATTRS.map((attr) => `
    <tr><td>${attr}</td>
      <td><select class="grade" data-i="${index}" data-attr="${attr}" data-f="grade">${gradeOptions(condition.grades[attr] || "")}</select></td>
      <td><input class="min" type="number" step="0.01" min="0" max="5" placeholder="不限"
           value="${esc(condition.mins[attr])}" data-i="${index}" data-attr="${attr}" data-f="min"></td></tr>`).join("");

  return `
  <div class="alert-card">
    <div class="row">
      <label class="field grow">界面显示名
        <input type="text" data-i="${index}" data-f="caseName" value="${esc(condition.caseName)}"></label>
      <label class="field">内部 ID
        <input class="mono" type="text" data-i="${index}" data-f="id" value="${esc(condition.id)}"></label>
      <label class="field">条件名
        <input type="text" data-i="${index}" data-f="name" value="${esc(condition.name)}"></label>
      <label class="row" style="margin-top:16px;gap:6px">
        <input type="checkbox" data-i="${index}" data-f="defaultChecked" ${condition.defaultChecked ? "checked" : ""}> 默认勾选</label>
      <button class="btn danger" data-act="remove" data-i="${index}" style="margin-top:16px">删除</button>
    </div>
    <div class="row" style="margin-top:10px">
      <label class="field grow">说明
        <input type="text" data-i="${index}" data-f="description" value="${esc(condition.description)}"></label>
      <label class="field">检测节点
        <input class="mono" type="text" data-i="${index}" data-f="node" value="${esc(condition.node)}"></label>
      ${badges.join("")}
    </div>
    <div class="row" style="margin-top:10px;align-items:flex-start">
      <div class="grow">
        <div class="hint" style="margin-bottom:6px">必需特性（全部命中；留空表示不看特性）</div>
        <div class="chips2">${chips}</div>
        <div class="row">
          <input type="text" list="catalog" placeholder="输入特性名，如 太阳之子"
                 data-i="${index}" data-role="feature-input" style="flex:1 1 200px">
          <button class="btn" data-act="add-feature" data-i="${index}">添加特性</button>
        </div>
      </div>
      <div style="flex:1 1 300px">
        <div class="hint" style="margin-bottom:6px">属性门槛（等级为 ≥）</div>
        <table class="attrs">${attrs}</table>
      </div>
    </div>
    <div class="row" style="margin-top:10px">
      <label class="field" style="width:100%">或条件（每行一条，如 敏捷>=0.3；同一行用「且」）
        <textarea data-i="${index}" data-f="orLines">${esc((condition.orLines || []).join("\n"))}</textarea></label>
    </div>
  </div>`;
}

function renderAlerts() {
  $("alertList").innerHTML = state.alerts.length
    ? state.alerts.map(alertCard).join("")
    : '<div class="empty">还没有提醒类型</div>';
}

async function loadAlerts() {
  try {
    const data = await api("/api/alerts");
    state.alerts = data.conditions || [];
    state.catalog = data.catalog || [];
    $("catalog").innerHTML = state.catalog.map((n) => `<option value="${esc(n)}"></option>`).join("");
    renderAlerts();
  } catch (error) {
    banner("err", ["读取提醒配置失败：" + error.message]);
  }
}

function updateAlert(event) {
  const t = event.target;
  const index = Number(t.dataset.i);
  const field = t.dataset.f;
  if (!Number.isInteger(index) || !field || !state.alerts[index]) return;
  const c = state.alerts[index];
  if (field === "grade") c.grades[t.dataset.attr] = t.value;
  else if (field === "min") c.mins[t.dataset.attr] = t.value;
  else if (field === "orLines") c.orLines = t.value.split("\n").map((s) => s.trim()).filter(Boolean);
  else if (field === "defaultChecked") c.defaultChecked = t.checked;
  else if (field === "caseName") {
    const old = c.caseName;
    c.caseName = t.value;
    if (c.node === "检测_" + old) {
      c.node = "检测_" + t.value;
      const input = document.querySelector(`input[data-f="node"][data-i="${index}"]`);
      if (input) input.value = c.node;
    }
  } else c[field] = t.value;
}

function addFeature(index) {
  const input = document.querySelector(`input[data-role="feature-input"][data-i="${index}"]`);
  const value = (input.value || "").trim();
  if (!value) return;
  const c = state.alerts[index];
  if (!c.features.includes(value)) c.features.push(value);
  input.value = "";
  renderAlerts();
}

async function saveAlerts() {
  try {
    $("btnAlertSave").disabled = true;
    const data = await post("/api/alerts/save", { conditions: state.alerts });
    if (data.ok) {
      state.alerts = data.conditions || state.alerts;
      renderAlerts();
      banner("ok", ["保存成功：", ...(data.messages || [])]);
    } else {
      banner("err", ["保存失败：", ...(data.errors || ["未知错误"])]);
    }
  } catch (error) {
    banner("err", ["保存失败：" + error.message]);
  } finally {
    $("btnAlertSave").disabled = false;
  }
}

/* ------------------------------------------------------------ 孩子记录 */

async function loadRecords() {
  try {
    const data = await api("/api/records");
    state.records = (data.records || []).slice().reverse();
    $("recordBody").innerHTML = state.records.length
      ? state.records.map((r) => `<tr>
          <td class="mono">${esc(r["时间"])}</td>
          <td>${esc(r["孩子序号"])}</td>
          <td>${esc(r["名字"])}</td>
          <td>${esc(r["血脉"])}</td>
          <td>${esc(r["特性"])}</td>
          <td class="${r["好娃提醒"] === "是" ? "warn" : ""}">${esc(r["好娃提醒"])}</td>
          <td>${esc(r["匹配条件"])}</td>
          <td>${esc(r["招募状态"])}</td>
          <td>${r["招募状态"] === "已招募" ? "" : `<button class="btn" data-recruit="${esc(r["名字"])}">标记已招募</button>`}</td>
        </tr>`).join("")
      : '<tr><td colspan="9" class="empty">还没有记录，生娃命名后会自动写入</td></tr>';
  } catch (error) {
    banner("err", ["读取记录失败：" + error.message]);
  }
}

async function markRecruited(name) {
  try {
    await post("/api/records/recruited", { name });
    banner("ok", ["已标记：" + name]);
    await loadRecords();
  } catch (error) {
    banner("err", [error.message]);
  }
}

/* ------------------------------------------------------------ 通知 */

async function loadNotify() {
  try {
    const data = await api("/api/notify");
    const config = data.config || {};
    $("notifyEnabled").checked = Boolean(config.enabled);
    $("notifyFormat").value = config.format || "feishu";
    $("notifyWebhook").value = config.webhook || "";
    $("notifyTimeout").value = config.timeout || 8;
    $("notifyStatus").textContent = data.hasWebhook
      ? "当前使用：" + data.effective
      : "尚未配置 webhook";
  } catch (error) {
    $("notifyStatus").textContent = "读取失败：" + error.message;
  }
}

async function saveNotify() {
  try {
    const data = await post("/api/notify/save", {
      enabled: $("notifyEnabled").checked,
      format: $("notifyFormat").value,
      webhook: $("notifyWebhook").value.trim(),
      timeout: Number($("notifyTimeout").value) || 8,
    });
    $("notifyStatus").textContent = data.hasWebhook
      ? "已保存，当前使用：" + data.effective
      : "已保存，但没有可用的 webhook";
    banner("ok", ["通知配置已保存"]);
  } catch (error) {
    banner("err", ["保存失败：" + error.message]);
  }
}

async function testNotify() {
  $("notifyStatus").textContent = "发送中…";
  try {
    const data = await post("/api/notify/test", {});
    $("notifyStatus").textContent = data.message;
    banner(data.ok ? "ok" : "err", [data.message]);
  } catch (error) {
    $("notifyStatus").textContent = "发送失败：" + error.message;
  }
}

/* ------------------------------------------------------------ 孩子命名速度 */

async function loadChildSpeed() {
  try {
    const data = await api("/api/child-name-config");
    $("childSpeed").value = data.speed || "normal";
    $("childSpeedHint").textContent = (data.profiles && data.profiles[data.speed]) || "";
  } catch (error) {
    $("childSpeedHint").textContent = "读取失败：" + error.message;
  }
}

async function saveChildSpeed() {
  try {
    const data = await post("/api/child-name-config/save", { speed: $("childSpeed").value });
    $("childSpeedHint").textContent = "已保存：" + data.speed;
    banner("ok", ["孩子命名速度已保存：" + data.speed]);
  } catch (error) {
    banner("err", ["保存失败：" + error.message]);
  }
}

/* ------------------------------------------------------------ 佣兵记录 */

async function loadMercRules() {
  try {
    const rules = await api("/api/mercenary-rules");
    $("mercMode").value = rules.mode || "whitelist";
    $("mercNames").value = (rules.names || []).join("\n");
    $("mercHint").textContent =
      `当前：${rules.mode === "blacklist" ? "黑名单" : "白名单"}，${(rules.names || []).length} 个关键字` +
      ((rules.names || []).length ? "" : "（名单为空，智能模式会全部拒绝）");
  } catch (error) {
    $("mercHint").textContent = "读取失败：" + error.message;
  }
}

async function saveMercRules() {
  try {
    const names = $("mercNames").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    const data = await post("/api/mercenary-rules/save", { mode: $("mercMode").value, names });
    $("mercHint").textContent = `已保存：${data.rules.names.length} 个关键字`;
    banner("ok", ["佣兵规则已保存"]);
  } catch (error) {
    banner("err", ["保存失败：" + error.message]);
  }
}

async function loadMercRecords() {
  try {
    const data = await api("/api/mercenary-records");
    const rows = (data.records || []).slice().reverse();
    $("mercBody").innerHTML = rows.length
      ? rows.map((r) => `<tr>
          <td class="mono">${esc(r["时间"])}</td>
          <td>${esc(r["游戏年"])}</td>
          <td>${esc(r["游戏月"])}</td>
          <td>${esc(r["佣兵名字"])}</td>
          <td class="${r["结果"] === "接受" ? "ok" : "bad"}">${esc(r["结果"])}</td>
          <td>${esc(r["匹配规则"])}</td>
          <td>${esc(r["备注"])}</td>
        </tr>`).join("")
      : '<tr><td colspan="7" class="empty">还没有佣兵记录</td></tr>';
  } catch (error) {
    banner("err", ["读取佣兵记录失败：" + error.message]);
  }
}

/* ------------------------------------------------------------ 事件绑定 */

document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (btn) switchTab(btn.dataset.tab);
});

$("btnConnect").addEventListener("click", connectDevice);
$("btnScan").addEventListener("click", refreshState);
$("btnAgentStart").addEventListener("click", startAgent);
$("btnAgentStop").addEventListener("click", stopAgent);
$("btnRun").addEventListener("click", runTask);
$("btnLoopStart").addEventListener("click", startLoop);
$("btnLoopStop").addEventListener("click", stopLoop);
$("taskOptions").addEventListener("change", scheduleSelectionSave);
$("taskOptions").addEventListener("input", scheduleSelectionSave);
$("btnStop").addEventListener("click", stopTask);
$("btnShot").addEventListener("click", refreshShot);
$("btnLogRefresh").addEventListener("click", loadLogs);
$("btnLogClear").addEventListener("click", async () => {
  try {
    await post("/api/logs/clear", {});
    $("logView").textContent = "";
    await loadLogs();
  } catch (error) {
    banner("err", [error.message]);
  }
});
$("btnAlertReload").addEventListener("click", loadAlerts);
$("btnChildSpeedSave").addEventListener("click", saveChildSpeed);
$("btnAlertSave").addEventListener("click", saveAlerts);
$("btnAlertAdd").addEventListener("click", () => { state.alerts.push(emptyAlert(state.alerts.length)); renderAlerts(); });
$("btnRecordReload").addEventListener("click", loadRecords);
$("btnMercSave").addEventListener("click", saveMercRules);
$("btnMercReload").addEventListener("click", loadMercRecords);
$("btnNotifySave").addEventListener("click", saveNotify);
$("btnNotifyTest").addEventListener("click", testNotify);

$("taskList").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-i]");
  if (btn) selectTask(Number(btn.dataset.i));
});

$("alertList").addEventListener("input", updateAlert);
$("alertList").addEventListener("change", updateAlert);
$("alertList").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.dataset.role === "feature-input") {
    e.preventDefault();
    addFeature(Number(e.target.dataset.i));
  }
});
$("alertList").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const i = Number(btn.dataset.i);
  if (btn.dataset.act === "remove") {
    if (confirm("确定删除这个提醒类型？")) { state.alerts.splice(i, 1); renderAlerts(); }
  } else if (btn.dataset.act === "rm-feature") {
    state.alerts[i].features.splice(Number(btn.dataset.fi), 1);
    renderAlerts();
  } else if (btn.dataset.act === "add-feature") {
    addFeature(i);
  }
});

$("recordBody").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-recruit]");
  if (btn) markRecruited(btn.dataset.recruit);
});

/* ------------------------------------------------------------ 循环任务 */

const loopState = { running: false, stop: false };

function renderLoopTasks() {
  const box = $("loopTaskList");
  if (!box) return;
  if (!state.tasks.length) {
    box.innerHTML = '<div class="hint">还没有任务列表</div>';
    return;
  }
  const checked = new Set(
    [...box.querySelectorAll("input[type=checkbox]:checked")].map((i) => i.dataset.index)
  );
  box.innerHTML = state.tasks
    .map(
      (task, index) =>
        `<label class="opt"><input type="checkbox" data-index="${index}" ${
          checked.has(String(index)) ? "checked" : ""
        }> ${esc(task.name)}</label>`
    )
    .join("");
}

async function waitLoopTaskDone() {
  let started = false;
  while (!loopState.stop) {
    try {
      const data = await api("/api/state");
      const st = data.task || {};
      if (!started && st.state === "running") started = true;
      if (st.state === "done") return "done";
      if (st.state === "failed") return "failed";
      if (started && st.state === "idle") return "idle";
    } catch (error) {
      /* 网络抖动时继续等 */
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  return "stopped";
}

async function startLoop() {
  if (loopState.running) return;
  await loadTasks();
  renderLoopTasks();
  const indexes = [...$("loopTaskList").querySelectorAll("input:checked")].map((i) =>
    Number(i.dataset.index)
  );
  if (!indexes.length) {
    banner("err", ["先勾选要循环的任务"]);
    return;
  }
  const continueOnFail = $("loopContinue").checked;
  loopState.running = true;
  loopState.stop = false;
  $("loopStatus").textContent = "循环运行中…";

  while (!loopState.stop) {
    for (const index of indexes) {
      if (loopState.stop) break;
      const task = state.tasks[index];
      if (!task) continue;
      const selection = (state.prefs.selections || {})[task.entry] || {};
      $("loopStatus").textContent = `正在执行：${task.name}`;
      try {
        await post("/api/task/run", {
          entry: task.entry,
          name: task.name,
          selection,
        });
      } catch (error) {
        $("loopStatus").textContent = `${task.name} 启动失败：${error.message}`;
        if (!continueOnFail) loopState.stop = true;
        continue;
      }
      const result = await waitLoopTaskDone();
      const label = result === "done" ? "成功" : result === "failed" ? "失败" : "已停止";
      $("loopStatus").textContent = `${task.name} → ${label}`;
      if (result === "failed" && !continueOnFail) {
        loopState.stop = true;
      }
      if (result === "stopped") loopState.stop = true;
    }
    if (!loopState.stop) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }
  loopState.running = false;
  $("loopStatus").textContent = "循环已停止";
}

async function stopLoop() {
  loopState.stop = true;
  try {
    await post("/api/task/stop", {});
  } catch (error) {
    /* ignore */
  }
  $("loopStatus").textContent = "正在停止循环…";
}

refreshState();
setInterval(refreshState, 3000);
