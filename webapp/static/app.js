"use strict";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const ATTRS = ["力量", "体质", "技巧", "感知", "敏捷", "意志"];
const GRADES = ["", "E", "D", "C", "B", "A", "S", "SS"];

const state = {
  tasks: [], records: [],
  devices: [], selectedTask: 0, activeTab: "home", prefs: {}, prefsLoaded: false,
  combos: [], comboModules: [], comboSelections: {}, comboIndex: -1, comboRunning: null,
  comboOpenSteps: new Set(),
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
  ["home", "tasks", "combos", "shot", "logs", "alerts", "records", "mercs", "notify"].forEach((tab) => {
    $("tab-" + tab).classList.toggle("hidden", tab !== name);
  });
  clearInterval(timers.shot);
  clearInterval(timers.logs);
  clearInterval(timers.combos);
  if (name === "shot") { refreshShot(); timers.shot = setInterval(() => { if ($("shotAuto").checked) refreshShot(); }, 2000); }
  if (name === "logs") { loadLogs(); timers.logs = setInterval(() => { if ($("logAuto").checked) loadLogs(); }, 1500); }
  if (name === "tasks") { loadTasks(); loadTaskpack(); }
  if (name === "combos") {
    loadCombos();
    timers.combos = setInterval(pollComboStatus, 2000);
  }
  if (name === "alerts") {
    if (window.AlertPanel) window.AlertPanel.load();
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
  await fetchTasks();
}

async function fetchTasks() {
  try {
    if (!state.prefsLoaded) await refreshState();
    state.tasks = await api("/api/tasks");
    renderTaskList();
    const last = (state.prefs || {}).last_task;
    const index = state.tasks.findIndex((t) => t.entry === last);
    if (state.tasks.length) selectTask(index >= 0 ? index : 0);
  } catch (error) {
    banner("err", ["读取任务失败：" + error.message]);
  }
}

async function reloadTasks() {
  state.tasks = [];
  await fetchTasks();
}

async function renameTask() {
  const task = state.tasks[state.selectedTask];
  if (!task) { banner("err", ["先选择任务"]); return; }
  const name = prompt("新的任务名称", task.name);
  if (!name || name.trim() === task.name) return;
  try {
    await post("/api/task/rename", { entry: task.entry, name: name.trim() });
    banner("ok", ["已重命名为：" + name.trim()]);
    await reloadTasks();
  } catch (error) {
    banner("err", ["重命名失败：" + error.message]);
  }
}

let lastDeletedTask = "";

async function deleteTask() {
  const task = state.tasks[state.selectedTask];
  if (!task) { banner("err", ["先选择任务"]); return; }
  if (!confirm(`确定从列表删除「${task.name}」吗？\n（只隐藏，可点“恢复删除”找回）`)) return;
  try {
    await post("/api/task/delete", { entry: task.entry });
    lastDeletedTask = task.entry;
    $("btnRestoreTask").hidden = false;
    banner("ok", ["已从列表删除：" + task.name]);
    await reloadTasks();
  } catch (error) {
    banner("err", ["删除失败：" + error.message]);
  }
}

async function restoreTask() {
  if (!lastDeletedTask) { banner("err", ["没有可恢复的任务"]); return; }
  try {
    await post("/api/task/restore", { entry: lastDeletedTask });
    banner("ok", ["已恢复：" + lastDeletedTask]);
    lastDeletedTask = "";
    $("btnRestoreTask").hidden = true;
    await reloadTasks();
  } catch (error) {
    banner("err", ["恢复失败：" + error.message]);
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

/* ------------------------------------------------------------ 任务包 */

async function loadTaskpack() {
  try {
    const data = await api("/api/taskpack");
    $("taskpackStatus").textContent =
      `${(data.folders || []).length} 个任务文件夹 · interface.json ${(data.interfaceTasks || []).length} 个任务` +
      (data.consistent ? " · 一致" : " · 需要导出或重建");
  } catch (error) {
    $("taskpackStatus").textContent = "读取失败：" + error.message;
  }
}

function showTaskpack(result) {
  const lines = [...(result.messages || [])];
  (result.errors || []).forEach((message) => lines.push("错误：" + message));
  const box = $("taskpackLog");
  box.style.display = lines.length ? "" : "none";
  box.textContent = lines.join("\n");
  banner(result.ok ? "ok" : "err", lines.length ? lines : ["完成"]);
  loadTaskpack();
  loadTasks();
}

async function runTaskpack(path, payload) {
  try {
    showTaskpack(await post(path, payload || {}));
  } catch (error) {
    banner("err", ["操作失败：" + error.message]);
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
$("btnRenameTask").addEventListener("click", renameTask);
$("btnDeleteTask").addEventListener("click", deleteTask);
$("btnRestoreTask").addEventListener("click", restoreTask);
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
$("btnChildSpeedSave").addEventListener("click", saveChildSpeed);
$("btnRecordReload").addEventListener("click", loadRecords);
$("btnMercSave").addEventListener("click", saveMercRules);
$("btnMercReload").addEventListener("click", loadMercRecords);
$("btnNotifySave").addEventListener("click", saveNotify);
$("btnNotifyTest").addEventListener("click", testNotify);

$("btnTaskpackCheck").addEventListener("click", () => runTaskpack("/api/taskpack/check"));
$("btnTaskpackExport").addEventListener("click", () => {
  if (!confirm("会用 interface.json 覆盖 tasks/ 里同名的任务文件夹，继续？")) return;
  runTaskpack("/api/taskpack/export", { force: true });
});
$("btnTaskpackBuild").addEventListener("click", () => {
  if (!confirm("用 tasks/ 重建 interface.json？旧文件会备份为 .bak。")) return;
  runTaskpack("/api/taskpack/build");
});
$("btnTaskpackPack").addEventListener("click", () => {
  const task = state.tasks[state.selectedTask];
  if (!task) { banner("err", ["先在左侧选择一个任务"]); return; }
  runTaskpack("/api/taskpack/pack", { entry: task.entry });
});
$("btnTaskpackImport").addEventListener("click", () => {
  const path = prompt("任务包 zip 路径（例如 dist\\tasks\\CastleMarryTest.zip）");
  if (!path) return;
  runTaskpack("/api/taskpack/unpack", { path: path.trim(), build: true });
});

$("taskList").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-i]");
  if (btn) selectTask(Number(btn.dataset.i));
});

$("recordBody").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-recruit]");
  if (btn) markRecruited(btn.dataset.recruit);
});

$("comboList").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-i]");
  if (!btn) return;
  state.comboIndex = Number(btn.dataset.i);
  state.comboOpenSteps = new Set();
  renderComboList();
  renderComboEditor();
});
$("comboSteps").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const index = Number(btn.dataset.i);
  const action = btn.dataset.act;
  if (action === "up") moveComboStep(index, -1);
  else if (action === "down") moveComboStep(index, 1);
  else if (action === "remove") removeComboStep(index);
  else if (action === "options") {
    if (state.comboOpenSteps.has(index)) state.comboOpenSteps.delete(index);
    else state.comboOpenSteps.add(index);
    renderComboEditor();
  }
});
$("comboSteps").addEventListener("change", updateStepOption);
$("comboSteps").addEventListener("input", updateStepOption);
$("comboDesc").addEventListener("input", () => {
  const combo = currentCombo();
  if (combo) combo.description = $("comboDesc").value;
});
$("comboName").addEventListener("input", () => {
  const combo = currentCombo();
  if (combo) {
    combo.name = $("comboName").value;
    renderComboList();
  }
});
$("comboLoops").addEventListener("input", () => {
  const combo = currentCombo();
  if (combo) combo.loops = Math.max(0, Number($("comboLoops").value) || 0);
});
$("comboRules").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-rule]");
  if (!btn) return;
  const combo = currentCombo();
  if (!combo) return;
  combo.rules.splice(Number(btn.dataset.rule), 1);
  renderComboRules();
});
$("btnRuleAdd").addEventListener("click", () => {
  const combo = currentCombo();
  if (!combo) {
    banner("err", ["先新建组合"]);
    return;
  }
  const action = $("ruleAction").value;
  const value = Math.max(0, Number($("ruleValue").value) || 0);
  if ((action === "retry" || action === "wait") && value <= 0) {
    banner("err", ["重试 / 等待需要填写次数或秒数"]);
    return;
  }
  combo.rules.push({ when: $("ruleWhen").value, action, value });
  renderComboRules();
});
$("btnComboNew").addEventListener("click", newCombo);
$("btnComboDelete").addEventListener("click", deleteCombo);
$("btnComboAddStep").addEventListener("click", addComboStep);
$("btnComboSave").addEventListener("click", saveCombos);
$("btnComboRun").addEventListener("click", runCombo);
$("btnComboStop").addEventListener("click", stopCombo);

/* ------------------------------------------------------------ 推年计划（任务组合） */

async function loadCombos() {
  try {
    const data = await api("/api/combos");
    state.combos = data.combos || [];
    state.comboModules = data.modules || [];
    state.comboSelections = data.selections || {};
    state.comboRunning = data.running || null;
    if (state.comboIndex >= state.combos.length) state.comboIndex = state.combos.length - 1;
    if (state.comboIndex < 0 && state.combos.length) state.comboIndex = 0;
    renderComboList();
    renderComboModuleOptions();
    renderComboEditor();
    renderComboStatus();
  } catch (error) {
    $("comboStatus").textContent = "读取组合失败：" + error.message;
  }
}

async function pollComboStatus() {
  try {
    const data = await api("/api/combos/status");
    state.comboRunning = data.running || null;
    renderComboStatus();
  } catch (error) {
    /* 轮询失败不打扰 */
  }
}

function currentCombo() {
  return state.combos[state.comboIndex] || null;
}

function moduleName(entry) {
  const module = state.comboModules.find((item) => item.entry === entry);
  return module ? module.name : entry;
}

function renderComboList() {
  const box = $("comboList");
  if (!box) return;
  box.innerHTML = state.combos.length
    ? state.combos
        .map(
          (combo, index) =>
            `<button data-i="${index}" title="${esc(combo.description || "")}" class="${index === state.comboIndex ? "active" : ""}">${esc(combo.name)} <span class="hint">（${combo.steps.length} 步）</span></button>`
        )
        .join("")
    : '<div class="empty">还没有组合，点「＋ 新建组合」</div>';
}

function renderComboModuleOptions() {
  const select = $("comboModule");
  if (!select) return;
  const current = select.value;
  select.innerHTML = state.comboModules
    .map((module) => `<option value="${esc(module.entry)}">${esc(module.name)}</option>`)
    .join("");
  if (current) select.value = current;
}

const RULE_WHEN_OPTIONS = [
  ["fail", "任务失败"],
  ["child_alert", "好娃提醒"],
  ["offline", "设备离线"],
  ["loop_end", "每轮结束"],
];
const RULE_ACTION_OPTIONS = [
  ["retry", "重试当前步骤"],
  ["skip", "跳过当前步骤"],
  ["stop", "停止组合"],
  ["continue", "继续下一步"],
  ["notify", "发送通知"],
  ["reconnect", "重连后重试"],
  ["wait", "等待"],
];

function ruleLabel(list, value) {
  const found = list.find((item) => item[0] === value);
  return found ? found[1] : value;
}

function renderRuleSelects() {
  const when = $("ruleWhen");
  const action = $("ruleAction");
  if (!when || !action) return;
  when.innerHTML = RULE_WHEN_OPTIONS.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  action.innerHTML = RULE_ACTION_OPTIONS.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
}

function defaultSelection(module) {
  const selection = {};
  (module.options || []).forEach((option) => {
    if (option.type === "switch" || option.type === "select") {
      selection[option.name] = option.default_case || (option.cases[0] ? option.cases[0].name : "");
    } else if (option.type === "checkbox") {
      const list = option.default_case;
      selection[option.name] = Array.isArray(list) ? list.slice() : list ? [list] : [];
    } else if (option.type === "input") {
      const values = {};
      (option.inputs || []).forEach((input) => {
        values[input.name] = input.default || "";
      });
      selection[option.name] = values;
    }
  });
  return selection;
}

function selectionFor(entry) {
  const module = state.comboModules.find((item) => item.entry === entry);
  const base = module ? defaultSelection(module) : {};
  return Object.assign(base, state.comboSelections[entry] || {});
}

function stepOptionsHtml(step, index) {
  const module = state.comboModules.find((item) => item.entry === step.entry);
  if (!module) return '<div class="step-options"><div class="hint">找不到模块信息</div></div>';
  if (!(module.options || []).length) {
    return '<div class="step-options"><div class="hint">这个模块没有选项</div></div>';
  }
  const rows = module.options.map((option) => {
    const value = step.selection ? step.selection[option.name] : undefined;
    let field = "";
    if (option.type === "checkbox") {
      const list = Array.isArray(value) ? value : [];
      field = (option.cases || [])
        .map(
          (item) =>
            `<label class="opt-check"><input type="checkbox" data-step="${index}" data-opt="${esc(option.name)}" value="${esc(item.name)}" ${list.includes(item.name) ? "checked" : ""}> ${esc(item.name)}</label>`
        )
        .join("");
    } else if (option.type === "select" || option.type === "switch") {
      field = `<select data-step="${index}" data-opt="${esc(option.name)}">${(option.cases || [])
        .map(
          (item) =>
            `<option value="${esc(item.name)}" ${item.name === value ? "selected" : ""}>${esc(item.name)}</option>`
        )
        .join("")}</select>`;
    } else if (option.type === "input") {
      field = (option.inputs || [])
        .map((input) => {
          const inputValue = (value && value[input.name]) || "";
          return `<input type="text" data-step="${index}" data-opt="${esc(option.name)}" data-input="${esc(input.name)}" value="${esc(inputValue)}" placeholder="${esc(input.name)}">`;
        })
        .join("");
    }
    return `<div class="opt-row"><span class="opt-label" title="${esc(option.description || "")}">${esc(option.label || option.name)}</span><span class="opt-field">${field}</span></div>`;
  });
  return `<div class="step-options">${rows.join("")}</div>`;
}

function renderComboSteps(combo) {
  if (!combo.steps.length) {
    return '<div class="empty">还没有步骤，从下面选一个任务模块添加</div>';
  }
  return combo.steps
    .map((step, index) => {
      const saved = step.selection || {};
      const keys = Object.keys(saved);
      const detail = keys.length ? keys.map((key) => `${key}=${saved[key]}`).join("，") : "默认";
      const summary = keys.length ? `${keys.length} 项选项` : "默认";
      const open = state.comboOpenSteps.has(index);
      return `<div class="combo-step-wrap">
        <div class="combo-step">
          <span class="step-index">${index + 1}</span>
          <span class="step-name">${esc(moduleName(step.entry))}</span>
          <span class="step-hint" title="${esc(detail)}">选项：${esc(summary)}</span>
          <span class="step-actions">
            <button class="btn small" data-act="up" data-i="${index}" ${index === 0 ? "disabled" : ""}>↑</button>
            <button class="btn small" data-act="down" data-i="${index}" ${index === combo.steps.length - 1 ? "disabled" : ""}>↓</button>
            <button class="btn small ${open ? "primary" : ""}" data-act="options" data-i="${index}">选项</button>
            <button class="btn small danger" data-act="remove" data-i="${index}">✕</button>
          </span>
        </div>
        ${open ? stepOptionsHtml(step, index) : ""}
      </div>`;
    })
    .join("");
}

function renderComboRules() {
  const combo = currentCombo();
  const box = $("comboRules");
  if (!box) return;
  const rules = combo ? combo.rules || [] : [];
  box.innerHTML = rules.length
    ? rules
        .map((rule, index) => {
          const unit = rule.action === "wait" ? " 秒" : rule.action === "retry" ? " 次" : "";
          const value = rule.value ? ` ${rule.value}${unit}` : "";
          return `<div class="combo-rule">
            <span>当「${esc(ruleLabel(RULE_WHEN_OPTIONS, rule.when))}」→ ${esc(ruleLabel(RULE_ACTION_OPTIONS, rule.action))}${esc(value)}</span>
            <button class="btn small danger" data-rule="${index}">✕</button>
          </div>`;
        })
        .join("")
    : '<div class="hint">还没有规则：任务失败默认停止、好娃提醒默认停止、设备离线默认停止。</div>';
}

function renderComboEditor() {
  const combo = currentCombo();
  if (!combo) {
    $("comboName").value = "";
    $("comboLoops").value = 1;
    $("comboDesc").value = "";
    $("comboSteps").innerHTML = '<div class="empty">还没有组合</div>';
    renderComboRules();
    return;
  }
  $("comboName").value = combo.name;
  $("comboLoops").value = combo.loops;
  $("comboDesc").value = combo.description || "";
  $("comboSteps").innerHTML = renderComboSteps(combo);
  renderComboRules();
}

function newCombo() {
  state.combos.push({
    id: "c" + Date.now().toString(36),
    name: `新组合 ${state.combos.length + 1}`,
    description: "",
    loops: 1,
    steps: [],
    rules: [
      { when: "child_alert", action: "stop", value: 0 },
      { when: "offline", action: "reconnect", value: 0 },
    ],
  });
  state.comboIndex = state.combos.length - 1;
  state.comboOpenSteps = new Set();
  renderComboList();
  renderComboEditor();
  $("comboName").focus();
  $("comboName").select();
}

function renderComboStatus() {
  const box = $("comboStatus");
  if (!box) return;
  const running = state.comboRunning || {};
  if (running.state === "running" || running.state === "stopping") {
    const loops = running.loops || "∞";
    box.textContent = `${running.combo}：第 ${running.loop}/${loops} 轮 · 步骤 ${running.step}/${running.steps} ${running.stepName}`;
  } else if (running.state && running.state !== "idle") {
    const label = running.state === "done" ? "已完成" : running.state === "failed" ? "失败" : "已停止";
    box.textContent = `${running.combo || ""}：${running.message || label}`;
  } else {
    box.textContent = "未运行";
  }
}

async function saveCombos() {
  try {
    const data = await post("/api/combos/save", { combos: state.combos });
    if (!data.ok) {
      banner("err", ["保存失败", ...(data.errors || [])]);
      return false;
    }
    state.combos = data.combos || state.combos;
    banner("ok", data.messages || ["已保存"]);
    renderComboList();
    renderComboEditor();
    return true;
  } catch (error) {
    banner("err", ["保存失败：" + error.message]);
    return false;
  }
}

async function deleteCombo() {
  const combo = currentCombo();
  if (!combo) return;
  if (!confirm(`删除组合「${combo.name}」？`)) return;
  state.combos.splice(state.comboIndex, 1);
  state.comboIndex = Math.min(state.comboIndex, state.combos.length - 1);
  state.comboOpenSteps = new Set();
  await saveCombos();
  if (state.comboIndex < 0 && state.combos.length) state.comboIndex = 0;
  renderComboList();
  renderComboEditor();
}

function addComboStep() {
  const combo = currentCombo();
  const entry = $("comboModule").value;
  if (!combo || !entry) return;
  combo.steps.push({ entry, selection: selectionFor(entry) });
  state.comboOpenSteps = new Set([combo.steps.length - 1]);
  renderComboEditor();
}

function moveComboStep(index, delta) {
  const combo = currentCombo();
  if (!combo) return;
  const target = index + delta;
  if (target < 0 || target >= combo.steps.length) return;
  const [step] = combo.steps.splice(index, 1);
  combo.steps.splice(target, 0, step);
  state.comboOpenSteps = new Set();
  renderComboEditor();
}

function removeComboStep(index) {
  const combo = currentCombo();
  if (!combo) return;
  combo.steps.splice(index, 1);
  state.comboOpenSteps = new Set();
  renderComboEditor();
}

function updateStepOption(event) {
  const element = event.target;
  if (!element.dataset || element.dataset.step === undefined || !element.dataset.opt) return;
  const combo = currentCombo();
  if (!combo) return;
  const step = combo.steps[Number(element.dataset.step)];
  if (!step) return;
  step.selection = step.selection || {};
  const name = element.dataset.opt;
  if (element.type === "checkbox") {
    const list = new Set(Array.isArray(step.selection[name]) ? step.selection[name] : []);
    if (element.checked) list.add(element.value);
    else list.delete(element.value);
    step.selection[name] = [...list];
  } else if (element.dataset.input) {
    step.selection[name] = step.selection[name] || {};
    step.selection[name][element.dataset.input] = element.value;
  } else {
    step.selection[name] = element.value;
  }
  const wrap = element.closest(".combo-step-wrap");
  const hint = wrap && wrap.querySelector(".step-hint");
  if (hint) {
    const keys = Object.keys(step.selection);
    hint.textContent = `选项：${keys.length ? keys.length + " 项选项" : "默认"}`;
    hint.title = keys.map((key) => `${key}=${step.selection[key]}`).join("，");
  }
}

async function runCombo() {
  const combo = currentCombo();
  if (!combo) return;
  if (!combo.steps.length) {
    banner("err", ["至少添加一个步骤"]);
    return;
  }
  try {
    const data = await post("/api/combos/run", { combo });
    if (!data.ok) {
      banner("err", ["运行失败", ...(data.errors || [data.error || "未知错误"])]);
      return;
    }
    state.comboRunning = data.running;
    renderComboStatus();
    banner("ok", [`开始运行：${combo.name}`]);
  } catch (error) {
    banner("err", ["运行失败：" + error.message]);
  }
}

async function stopCombo() {
  try {
    const data = await post("/api/combos/stop", {});
    state.comboRunning = data.running || state.comboRunning;
    renderComboStatus();
    banner("ok", ["已请求停止组合"]);
  } catch (error) {
    banner("err", ["停止失败：" + error.message]);
  }
}

// 支持 #alerts 之类的深链接，方便直接打开某一页
const startTab = (location.hash || "").replace(/^#/, "");
if (["home", "tasks", "combos", "shot", "logs", "alerts", "records", "mercs", "notify"].includes(startTab)) {
  switchTab(startTab);
}

renderRuleSelects();
refreshState();
setInterval(refreshState, 3000);
