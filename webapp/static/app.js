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
  selfheal: { items: [], detail: null, selected: "", stats: {}, repair: {} },
  scenes: { data: null, tableKey: "", recentKey: "" },
  unknowns: { data: null, items: [], presets: [], defaultRoute: {}, stats: {}, key: "" },
  source: { path: "", line: 0 },
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

/* 页面脚本报错也存一份现场，避免只在 F12 里一闪而过 */
function reportFrontendError(payload) {
  try {
    fetch("/api/selfheal/frontend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => {});
  } catch (error) { /* 忽略 */ }
}

window.addEventListener("error", (event) => {
  try {
    if (!event.message) return;
    reportFrontendError({
      title: "页面脚本报错",
      message: event.message,
      source: event.filename || "",
      line: event.lineno || 0,
      column: event.colno || 0,
      stack: event.error && event.error.stack ? event.error.stack : "",
      url: location.href,
    });
  } catch (error) { /* 忽略 */ }
});

window.addEventListener("unhandledrejection", (event) => {
  try {
    const reason = event.reason;
    reportFrontendError({
      title: "页面未处理的 Promise 异常",
      message: reason && reason.message ? reason.message : String(reason),
      stack: reason && reason.stack ? reason.stack : "",
      url: location.href,
    });
  } catch (error) { /* 忽略 */ }
});

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
  ["home", "tasks", "combos", "shot", "logs", "selfheal", "alerts", "records", "mercs", "tprec", "notify"].forEach((tab) => {
    $("tab-" + tab).classList.toggle("hidden", tab !== name);
  });
  clearInterval(timers.shot);
  clearInterval(timers.logs);
  clearInterval(timers.combos);
  clearInterval(timers.selfheal);
  clearInterval(timers.tprec);
  clearInterval(timers.scenes);
  if (name === "home") {
    loadScenes(true);
    timers.scenes = setInterval(() => { if (state.activeTab === "home") loadScenes(true); }, 10000);
  }
  if (name === "shot") { refreshShot(); timers.shot = setInterval(() => { if ($("shotAuto").checked) refreshShot(); }, 2000); }
  if (name === "logs") { loadLogs(); timers.logs = setInterval(() => { if ($("logAuto").checked) loadLogs(); }, 1500); }
  if (name === "tasks") { loadTasks(); loadTaskpack(); }
  if (name === "selfheal") {
    loadSelfheal();
    timers.selfheal = setInterval(() => { if (state.activeTab === "selfheal") loadSelfheal(true); }, 5000);
  }
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
  if (name === "tprec") {
    loadTaskRecords();
    timers.tprec = setInterval(() => { if (state.activeTab === "tprec") loadTaskRecords(true); }, 5000);
  }
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

/* ------------------------------------------------------------ 场景处理（月度循环） */

async function loadScenes(silent, force) {
  try {
    const data = await api("/api/scenes");
    state.scenes.data = data;
    const tableKey = [
      data.updated, data.count, data.routes_updated,
      (data.scenes || []).map((scene) => `${scene.id}${scene.new ? "*" : ""}${scene.route ? scene.route.id : ""}`).join(","),
    ].join("|");
    if (force || state.scenes.tableKey !== tableKey) {
      state.scenes.tableKey = tableKey;
      renderSceneGroups(data.groups || []);
      renderScenes();
    }
    renderSceneRecent(data);
    await loadUnknowns(true);
    return data;
  } catch (error) {
    if (!silent) banner("err", ["读取场景处理失败：" + error.message]);
    const body = $("sceneBody");
    if (body) body.innerHTML = `<tr><td colspan="4" class="empty">读取失败：${esc(error.message)}</td></tr>`;
    return null;
  }
}

function renderSceneGroups(groups) {
  const select = $("sceneGroup");
  if (!select) return;
  const existing = [...select.options].slice(1).map((option) => option.value);
  if (existing.length === groups.length && groups.every((group, i) => group === existing[i])) return;
  const current = select.value;
  select.innerHTML = '<option value="">全部类别</option>' +
    groups.map((group) => `<option value="${esc(group)}">${esc(group)}</option>`).join("");
  if (groups.includes(current)) select.value = current;
}

function renderScenes() {
  const data = state.scenes.data;
  if (!data) return;
  const body = $("sceneBody");
  if (!body) return;
  const wrap = body.closest(".scene-table-wrap");
  const scrollTop = wrap ? wrap.scrollTop : 0;
  const query = ($("sceneSearch").value || "").trim().toLowerCase();
  const group = $("sceneGroup").value || "";
  const items = (data.scenes || []).filter((scene) => {
    if (group && scene.group !== group) return false;
    if (!query) return true;
    const haystack = [
      scene.name, scene.group, scene.when, scene.then, scene.handler,
      scene.route ? scene.route.label : "", scene.route ? (scene.route.steps || []).join(" ") : "",
      (scene.nodes || []).join(" "),
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  });

  $("sceneCount").textContent = `${items.length}/${data.count || items.length} 个场景`;
  $("sceneUpdated").textContent = data.updated ? `更新：${data.updated}` : "";
  const newScenes = (data.scenes || []).filter((scene) => scene.new).map((scene) => scene.name);
  const newBadge = $("sceneNewBadge");
  newBadge.hidden = !newScenes.length;
  if (newScenes.length) newBadge.textContent = `新增：${newScenes.join(" / ")}`;

  body.innerHTML = items.length ? items.map((scene) => {
    const nodes = (scene.nodes || []).map((node) => `<code>${esc(node)}</code>`).join(" ");
    const task = scene.task
      ? `<button class="btn small" data-scene-task="${esc(scene.task)}">去任务</button>`
      : '<span class="hint">—</span>';
    const newBadge = scene.new ? '<span class="badge new">新</span>' : "";
    const note = scene.note ? `<div class="hint scene-note">${esc(scene.note)}</div>` : "";
    const handler = scene.handler
      ? `<button class="link-btn mono" data-source-symbol="${esc(scene.handler)}" title="查看处理函数源码">${esc(scene.handler)}</button>`
      : ((scene.nodes || []).length
        ? `<button class="link-btn" data-source-node="${esc(scene.nodes[0])}" title="查看识别节点源码">源码</button>`
        : "");
    const route = scene.route
      ? `<div class="hint scene-route">返回：${esc(scene.route.label)} <code>${esc((scene.route.steps || []).join(" → "))}</code>`
        + (scene.route.symbol
          ? ` <button class="link-btn" data-source-symbol="${esc(scene.route.symbol)}" title="查看返回逻辑源码">实现</button>`
          : "")
        + "</div>"
      : "";
    return `<tr class="${scene.new ? "scene-row-new" : ""}">
      <td>
        <div class="scene-name">${esc(scene.name)}${newBadge}</div>
        <span class="badge">${esc(scene.group || "其他")}</span>
        ${handler}
      </td>
      <td>${esc(scene.when)}${nodes ? `<div class="scene-nodes">${nodes}</div>` : ""}</td>
      <td>${esc(scene.then)}${note}${route}</td>
      <td>${task}</td>
    </tr>`;
  }).join("") : '<tr><td colspan="4" class="empty">没有匹配的场景</td></tr>';
  if (wrap) wrap.scrollTop = scrollTop;
}

function renderSceneRecent(data) {
  const recent = data.recent || [];
  const guard = data.guard || {};
  const guardEvents = guard.events || [];
  const labels = {
    mercenary_baby: "佣兵生娃",
    mercenary_join: "佣兵加入",
    child_death: "孩子夭折",
    annual_closing: "年度结算",
    popup_tip: "提示弹窗",
    ration_prepay: "口粮预交",
    ration_short: "口粮不足",
    ration_prepay_failed: "口粮预交点确定失败",
    unknown_recovered: "未知界面已恢复",
    unknown_recorded: "未知界面已记录",
  };
  const key = `${(recent || []).join("|")}::${guard.time}::${guardEvents.join("|")}`;
  if (state.scenes.recentKey === key) return;
  state.scenes.recentKey = key;
  const guardText = guardEvents.map((event) => labels[event] || event).join("、");
  $("sceneGuardHint").textContent = guard.time
    ? `上次场景确认：${guard.time}${guardEvents.length ? "（处理 " + guardText + "）" : "（没有短事件）"}`
    : "";
  $("sceneRecent").innerHTML = recent.length
    ? recent.map((line) => `<li>${esc(line)}</li>`).join("")
    : '<li class="empty">还没有记录</li>';
}

async function gotoSceneTask(entry) {
  if (!entry) return;
  try {
    if (!state.tasks.length) await fetchTasks();
    const index = state.tasks.findIndex((task) => task.entry === entry);
    if (index < 0) {
      banner("err", [`任务列表里没有入口 ${entry}（可能已隐藏或未打包）`]);
      return;
    }
    switchTab("tasks");
    selectTask(index);
  } catch (error) {
    banner("err", ["打开任务失败：" + error.message]);
  }
}

/* ------------------------------------------------------------ 未知界面 + 源码定位 */

async function loadUnknowns(silent) {
  try {
    const data = await api("/api/unknowns");
    state.unknowns.data = data;
    state.unknowns.items = data.items || [];
    state.unknowns.presets = data.presets || [];
    state.unknowns.defaultRoute = data.default || {};
    state.unknowns.stats = data.stats || {};
    renderUnknowns();
    return data;
  } catch (error) {
    if (!silent) banner("err", ["读取未知界面记录失败：" + error.message]);
    return null;
  }
}

function unknownRouteOptions(selected) {
  const data = state.unknowns || {};
  const presetIds = (data.presets || []).map((preset) => preset.id);
  const chosen = presetIds.includes(selected) ? selected : "";
  const label = (data.defaultRoute && data.defaultRoute.label) || "默认路线";
  const steps = ((data.defaultRoute && data.defaultRoute.steps) || []).join(" → ");
  const opts = [`<option value=""${chosen ? "" : " selected"}>${esc(label)}${steps ? "（" + esc(steps) + "）" : ""}</option>`];
  for (const preset of data.presets || []) {
    opts.push(
      `<option value="${esc(preset.id)}"${preset.id === chosen ? " selected" : ""}>${esc(preset.label)}（${esc((preset.steps || []).join(" → "))}）</option>`
    );
  }
  return opts.join("");
}

function renderUnknowns() {
  const data = state.unknowns || {};
  const items = data.items || [];
  const stats = data.stats || {};
  const count = $("unknownCount");
  if (count) {
    count.textContent = `${items.length} 条` + (stats.hits ? ` · 遇到 ${stats.hits} 次` : "")
      + (stats.routed ? ` · ${stats.routed} 条已配返回` : "");
  }
  const list = $("unknownList");
  if (!list) return;
  const key = [
    stats.total, stats.hits, stats.routed,
    items.map((item) => [item.id, item.hits, item.route_id, item.note, item.has_image].join(":")).join(","),
  ].join("|");
  if (state.unknowns.key === key) return;
  state.unknowns.key = key;

  if (!items.length) {
    list.innerHTML = '<div class="empty">还没有记录。遇到认不出的画面时，会先点 3 个安全位置；画面仍无变化才记在这里。</div>';
    return;
  }

  list.innerHTML = items.map((item) => {
    const attempts = (item.attempts || []).map((step) => {
      const mark = step.changed === true ? "（变了）" : step.changed === false ? "（没变）" : "";
      return `${esc(step.step)}${mark}`;
    }).join(" → ") || "—";
    const probes = (item.probes || [])
      .filter((probe) => probe.hit || Number(probe.score || 0) >= 0.6)
      .map((probe) => `${esc(probe.node)}（${esc(probe.score)}）`)
      .join("、");
    const shot = item.has_image
      ? `<a class="unknown-shot-link" href="/api/unknowns/image?id=${encodeURIComponent(item.id)}" target="_blank" rel="noreferrer" title="点开大图"><img class="unknown-shot" src="/api/unknowns/image?id=${encodeURIComponent(item.id)}" alt="未知界面截图" loading="lazy"></a>`
      : '<div class="unknown-shot empty">无截图</div>';
    const source = item.source_path
      ? `<button class="link-btn mono" data-source-path="${esc(item.source_path)}" data-source-line="${item.source_line || 1}">${esc(item.source_path)}:${item.source_line || 0} ${esc(item.source_function || "")}</button>`
      : '<span class="hint">来源未知（没有调用栈）</span>';
    return `<div class="unknown-item" data-id="${esc(item.id)}">
      ${shot}
      <div class="unknown-body">
        <div class="unknown-line">
          <strong>${esc(item.created_at)}</strong>
          <span class="badge">${esc(item.task || item.entry || "未知任务")}</span>
          <span class="hint">命中 ${item.hits || 1} 次</span>
          ${item.route_saved ? '<span class="badge new">已配返回</span>' : ""}
        </div>
        <div class="unknown-line">${source}</div>
        <div class="unknown-line hint">点过：${attempts}</div>
        ${probes ? `<div class="unknown-line hint">可疑识别：${probes}</div>` : ""}
        ${item.reason ? `<div class="unknown-line hint">原因：${esc(item.reason)}</div>` : ""}
        <div class="unknown-line">
          <select data-unknown-route="${esc(item.id)}">${unknownRouteOptions(item.route_id)}</select>
          <button class="btn small" data-unknown-save="${esc(item.id)}">保存返回方式</button>
          <button class="btn small" data-unknown-note="${esc(item.id)}">备注</button>
          <button class="btn small danger" data-unknown-delete="${esc(item.id)}">删除</button>
        </div>
        ${item.note ? `<div class="hint unknown-note">备注：${esc(item.note)}</div>` : ""}
      </div>
    </div>`;
  }).join("");
}

async function saveUnknownRoute(id, routeId) {
  try {
    await post("/api/unknowns/route", { id, route: routeId });
    banner("ok", [routeId ? "返回方式已保存：下次遇到同一画面会照着这几步点" : "已改回默认返回方式"]);
    state.unknowns.key = "";
    await loadUnknowns(true);
  } catch (error) {
    banner("err", ["保存返回方式失败：" + error.message]);
  }
}

async function showSource({ symbol, node, path, line }) {
  try {
    const query = new URLSearchParams();
    if (symbol) query.set("symbol", symbol);
    if (node) query.set("node", node);
    if (path) query.set("path", path);
    if (line) query.set("line", String(line));
    const data = await api("/api/source?" + query.toString());
    const code = data.code;
    if (!code) {
      banner("err", ["没有找到对应的源码位置"]);
      return;
    }
    state.source = { path: code.path, line: code.line };
    const name = code.path.split("/").pop();
    $("sourceTitle").textContent = `源码 · ${name}`;
    $("sourceMeta").textContent = `${code.path}:${code.line}` +
      ((data.locations || []).length > 1 ? `（共 ${data.locations.length} 处，点下面切换）` : "");
    $("sourceCode").textContent = code.text;
    $("sourceLocations").innerHTML = (data.locations || []).length > 1
      ? data.locations.map((loc) =>
          `<button class="link-btn mono" data-source-path="${esc(loc.path)}" data-source-line="${loc.line}">${esc(loc.path)}:${loc.line}${loc.qualname && loc.qualname !== loc.name ? " " + esc(loc.qualname) : ""}</button>`
        ).join(" ")
      : `<span>${esc(data.uri || "")}</span>`;
    const dialog = $("sourceDialog");
    if (dialog && typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else if (dialog) {
      dialog.setAttribute("open", "");
    }
  } catch (error) {
    banner("err", ["读取源码失败：" + error.message]);
  }
}

async function openSourceInEditor() {
  if (!state.source.path) return;
  try {
    const result = await post("/api/source/open", state.source);
    if (result.ok) {
      banner("ok", [`已用 ${result.opened} 打开 ${result.path}:${result.line}` + (result.note ? `（${result.note}）` : "")]);
    } else {
      banner("err", ["打开编辑器失败：" + (result.error || "未知原因"), `可以复制这个链接打开：${result.uri || ""}`]);
    }
  } catch (error) {
    banner("err", ["打开编辑器失败：" + error.message]);
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

function baseSelectionValue(name) {
  const base = (state.prefs.selections || {})["Auto_YearlyTask"] || {};
  const value = base[name];
  if (Array.isArray(value)) return value[0] || "";
  if (value && typeof value === "object") {
    return Object.values(value).filter((item) => String(item || "").trim()).join(" / ");
  }
  return value || "";
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
  const savedSelection = (state.prefs.selections || {})[task.entry] || {};
  task.options.forEach((option) => {
    const field = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = option.label || option.name;
    field.appendChild(legend);

    if (option.description) {
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = option.description +
        (option.inheritBase ? "（未单独选择时跟随月度循环的基础配置）" : "");
      field.appendChild(hint);
    }

    const wrap = document.createElement("div");
    wrap.style.marginTop = "6px";

    if (option.type === "switch" || option.type === "select") {
      const select = document.createElement("select");
      select.id = `opt-${index}-${option.name}`;
      select.dataset.option = option.name;
      select.dataset.kind = option.type;
      if (option.inheritBase) {
        const inherit = document.createElement("option");
        inherit.value = "跟随月度循环";
        inherit.textContent = `跟随月度循环（当前：${baseSelectionValue(option.name) || "默认"}）`;
        select.appendChild(inherit);
      }
      option.cases.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.name;
        opt.textContent = c.description ? `${c.name} - ${c.description}` : c.name;
        select.appendChild(opt);
      });
      const dflt = Array.isArray(option.default_case) ? option.default_case[0] : option.default_case;
      if (option.inheritBase && savedSelection[option.name] == null) {
        select.value = "跟随月度循环";
      } else if (dflt) {
        select.value = dflt;
      }
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
        if (option.inheritBase) {
          input.value = "";
          input.placeholder = `跟随月度循环（当前：${baseSelectionValue(option.name) || "默认"}）`;
        } else {
          input.value = item.default || "";
        }
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

state.mercs = { combos: [], attributes: [], grades: [], loaded: false };

async function loadMercCombos() {
  if (state.mercs.loaded) return;
  try {
    const data = await api("/api/mercenary-combos");
    state.mercs.combos = data.combos || [];
    state.mercs.attributes = data.attributes || ["力量", "体质", "技巧", "感知", "敏捷", "意志"];
    state.mercs.grades = data.grades || ["SS", "S", "A", "B", "C", "D", "E"];
    renderMercCombos();
    renderMercAttrs();
    state.mercs.loaded = true;
  } catch (error) {
    $("mercCombos").textContent = "读取入队组合失败：" + error.message;
  }
}

function renderMercCombos() {
  const box = $("mercCombos");
  if (!state.mercs.combos.length) {
    box.innerHTML = '<span class="hint">「好娃提醒」里还没有组合，先到提醒页配一个</span>';
    return;
  }
  box.innerHTML = state.mercs.combos
    .map((combo) => {
      const detail = combo.description || (combo.features || []).join(" + ");
      return `<label class="row" style="gap:8px;align-items:flex-start">
        <input type="checkbox" data-combo="${esc(combo.name)}">
        <span><strong>${esc(combo.name)}</strong> <span class="hint">${esc(detail)}</span></span>
      </label>`;
    })
    .join("");
}

function renderMercAttrs() {
  $("mercAttrs").innerHTML = state.mercs.attributes
    .map(
      (attr) => `<label class="field">${esc(attr)}
        <select data-attr="${esc(attr)}">
          <option value="">不限</option>
          ${state.mercs.grades.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("")}
        </select>
      </label>`
    )
    .join("");
}

function applyMercRulesToForm(rules) {
  const picked = new Set(rules.combos || []);
  document.querySelectorAll("#mercCombos input[data-combo]").forEach((box) => {
    box.checked = picked.has(box.dataset.combo);
  });
  $("mercFeatures").value = (rules.features || []).join("、");
  const potential = rules.potential_min || {};
  document.querySelectorAll("#mercAttrs select[data-attr]").forEach((sel) => {
    sel.value = potential[sel.dataset.attr] || "";
  });
}

function collectMercRules() {
  const combos = Array.from(document.querySelectorAll("#mercCombos input[data-combo]:checked")).map(
    (box) => box.dataset.combo
  );
  const features = $("mercFeatures")
    .value.split(/[、,，;；\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const potential = {};
  document.querySelectorAll("#mercAttrs select[data-attr]").forEach((sel) => {
    if (sel.value) potential[sel.dataset.attr] = sel.value;
  });
  return { combos, features, potential_min: potential };
}

async function loadMercRules() {
  try {
    await loadMercCombos();
    const rules = await api("/api/mercenary-rules");
    $("mercMode").value = rules.mode || "whitelist";
    $("mercTitle").value = rules.title_min || "无";
    $("mercOrange").value = rules.min_orange || 0;
    $("mercMatch").value = rules.match || "all";
    $("mercNames").value = (rules.names || []).join("\n");
    applyMercRulesToForm(rules);
    $("mercHint").textContent = mercRuleSummary(rules);
  } catch (error) {
    $("mercHint").textContent = "读取失败：" + error.message;
  }
}

function mercRuleSummary(rules) {
  const mode = rules.mode === "blacklist" ? "黑名单" : "白名单";
  const names = (rules.names || []).length;
  const parts = [`${mode} ${names} 个名字关键字`];
  const orange = Number(rules.min_orange || 0);
  if (orange > 0) parts.push(`橙色特性≥${orange}`);
  const title = rules.title_min || "无";
  if (title !== "无") parts.push(`爵位≥${title}`);
  const combos = (rules.combos || []).length;
  if (combos > 0) parts.push(`入队组合×${combos}`);
  const features = (rules.features || []).length;
  if (features > 0) parts.push(`必需特性×${features}`);
  const attrs = Object.keys(rules.potential_min || {}).length;
  if (attrs > 0) parts.push(`属性门槛×${attrs}`);
  const join = (rules.match || "all") === "any" ? "任一满足" : "全部满足";
  const empty =
    mode === "白名单" && names === 0 && orange <= 0 && title === "无" &&
    combos === 0 && features === 0 && attrs === 0
      ? "（没有可用的接受条件，智能模式会全部拒绝）"
      : "";
  return `${empty}当前：${parts.join(" / ")}，${join}`;
}

async function saveMercRules() {
  try {
    const names = $("mercNames").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    const extra = collectMercRules();
    const data = await post("/api/mercenary-rules/save", {
      mode: $("mercMode").value,
      names,
      match: $("mercMatch").value,
      title_min: $("mercTitle").value,
      min_orange: Number($("mercOrange").value || 0),
      combos: extra.combos,
      features: extra.features,
      potential_min: extra.potential_min,
      read_detail: "auto",
    });
    $("mercHint").textContent = mercRuleSummary(data.rules);
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
          <td>${esc(r["爵位"])}</td>
          <td>${esc(r["橙色特性"])}</td>
          <td>${esc(r["属性"])}</td>
          <td title="${esc(r["特性"])}">${esc(r["特性"])}</td>
          <td>${esc(r["备注"])}</td>
        </tr>`).join("")
      : '<tr><td colspan="11" class="empty">还没有佣兵记录</td></tr>';
  } catch (error) {
    banner("err", ["读取佣兵记录失败：" + error.message]);
  }
}

/* ------------------------------------------------------------ 任务接取（城市规则 / 黑名单 / 任务板 / 记录） */

state.tprec = {
  city: "",
  touched: false,
  rules: { defaults: {}, cities: {} },
  global: [],
  pool: {},
  records: {},
  cities: [],
  updated: "",
};

async function loadTaskRecords(silent) {
  try {
    const [rulesData, recordsData] = await Promise.all([
      api("/api/task-rules"),
      api("/api/task-records"),
    ]);
    state.tprec.rules = rulesData.rules || { defaults: {}, cities: {} };
    state.tprec.global = rulesData.global_blacklist || [];
    state.tprec.pool = rulesData.pool || {};
    state.tprec.cities = rulesData.cities || [];
    state.tprec.records = recordsData.cities || {};
    state.tprec.updated = recordsData.updated || "";

    const select = $("tprecCity");
    const current = state.tprec.city;
    const cities = Array.from(new Set([
      ...state.tprec.cities,
      ...Object.keys(state.tprec.records),
      state.tprec.pool.city,
    ].filter(Boolean))).sort();
    select.innerHTML = '<option value="">全部城市</option>' +
      cities.map((city) => `<option value="${esc(city)}">${esc(city)}</option>`).join("");
    if (current && cities.includes(current)) {
      select.value = current;
    } else if (current) {
      state.tprec.city = "";
      select.value = "";
    } else if (!state.tprec.touched && state.tprec.pool.city && cities.includes(state.tprec.pool.city)) {
      // 首次打开默认选中最近任务板的城市，直接看到该城市的规则与判定
      state.tprec.city = state.tprec.pool.city;
      select.value = state.tprec.city;
    }

    renderTprecRule();
    renderTprecPool();
    renderTprecBlacklist();
    renderTprecRecords();
    $("tprecUpdated").textContent = state.tprec.updated ? `记录更新：${state.tprec.updated}` : "";
  } catch (error) {
    if (!silent) banner("err", ["读取任务规则失败：" + error.message]);
  }
}

function tprecCity() {
  return $("tprecCity").value || "";
}

function tprecCityRule(city) {
  return (state.tprec.rules.cities || {})[city] || {};
}

function tprecRule(city) {
  const target = city === undefined ? tprecCity() : city;
  const defaults = state.tprec.rules.defaults || {};
  const entry = tprecCityRule(target);
  return {
    min: entry.min_level !== undefined ? entry.min_level : (defaults.min_level ?? 0),
    max: entry.max_level !== undefined ? entry.max_level : (defaults.max_level ?? 120),
    explicit: Object.keys(entry).length > 0,
  };
}

function tprecEffectiveBlacklist(city) {
  const target = city === undefined ? tprecCity() : city;
  const names = new Set(state.tprec.global || []);
  (tprecCityRule(target).blacklist || []).forEach((name) => names.add(name));
  return names;
}

function tprecEvaluate(item, city) {
  const target = city === undefined ? tprecCity() : city;
  const rule = tprecRule(target);
  const names = tprecEffectiveBlacklist(target);
  const level = parseInt((String(item.level || "").match(/\d+/) || ["0"])[0], 10) || 0;
  if (!item.name) return { ok: false, reason: "未识别到任务名" };
  if (names.has(item.name)) return { ok: false, reason: "黑名单" };
  if (level < rule.min) return { ok: false, reason: `低于最低等级 ${rule.min}` };
  if (rule.max && level > rule.max) return { ok: false, reason: `高于最高等级 ${rule.max}` };
  if (item.reason && (item.reason.includes("类型排除") || item.reason.includes("非战斗"))) {
    return { ok: false, reason: item.reason };
  }
  return { ok: true, reason: "可接取" };
}

function renderTprecRule() {
  const city = tprecCity();
  const rule = tprecRule();
  $("tprecMin").value = rule.min;
  $("tprecMax").value = rule.max;
  $("tprecRuleHint").textContent = city
    ? (rule.explicit ? `${city}：已单独配置` : `${city}：跟随默认，保存后单独生效`)
    : "正在编辑默认规则（没有单独配置的城市都使用它）";
  $("tprecCityBlackWrap").style.display = city ? "" : "none";
}

function renderTprecPool() {
  const pool = state.tprec.pool || {};
  const poolCity = pool.city || "";
  const tasks = pool.tasks || [];
  const wanted = tprecCity();
  $("tprecPoolHint").textContent = tasks.length
    ? `${poolCity || "未知城市"} · ${pool.time || ""} · ${tasks.length} 条` +
      (wanted ? "" : `（选择「${poolCity}」可套用城市规则判定）`)
    : "还没有扫描到任务板（跑一次任务接取后会出现）";

  const rows = tasks
    .filter((item) => !wanted || poolCity === wanted)
    .map((item) => {
      const verdict = wanted ? tprecEvaluate(item, wanted) : null;
      const badge = verdict
        ? (verdict.ok
          ? '<span class="chip ok">可接</span>'
          : `<span class="chip bad" title="${esc(verdict.reason)}">跳过</span>`)
        : '<span class="hint">选择城市后判定</span>';
      const blocked = wanted
        ? tprecEffectiveBlacklist(wanted).has(item.name)
        : (state.tprec.global || []).includes(item.name);
      return `<tr>
        <td>${esc(item.name)}</td>
        <td>${esc(item.type)}</td>
        <td>${esc(item.level)}</td>
        <td class="hint">${esc(item.description || "")}</td>
        <td>${badge}${verdict ? `<div class="hint">${esc(verdict.reason)}</div>` : ""}</td>
        <td>${
          item.name && !blocked
            ? `<button class="btn danger" data-black="${esc(item.name)}">${wanted ? "拉黑" : "拉黑（全局）"}</button>`
            : (item.name ? '<span class="hint">已在黑名单</span>' : "")
        }</td>
      </tr>`;
    })
    .join("");

  $("tprecPool").innerHTML = rows ||
    `<tr><td colspan="6" class="hint">${wanted ? `当前选择「${esc(wanted)}」，最近任务板来自「${esc(poolCity || "未知")}」` : "暂无任务板数据"}</td></tr>`;
}

function renderTprecBlacklist() {
  const city = tprecCity();
  const globalNames = state.tprec.global || [];
  const cityNames = city ? (tprecCityRule(city).blacklist || []) : [];

  $("tprecBlackHint").textContent = city
    ? `全局 ${globalNames.length} 个 + ${city} ${cityNames.length} 个`
    : `全局 ${globalNames.length} 个；选择城市后可单独配置该城市的黑名单`;

  $("tprecGlobalBlack").innerHTML = globalNames.length
    ? globalNames.map((name) => `<span class="chip" style="display:inline-flex;align-items:center;gap:6px;margin:0 10px 8px 0">${esc(name)}<button class="btn" data-unblack-global="${esc(name)}">解除</button></span>`).join("")
    : '<span class="hint">暂无全局黑名单</span>';

  $("tprecCityBlack").innerHTML = city
    ? (cityNames.length
      ? cityNames.map((name) => `<span class="chip" style="display:inline-flex;align-items:center;gap:6px;margin:0 10px 8px 0">${esc(name)}<button class="btn" data-unblack-city="${esc(name)}">解除</button></span>`).join("")
      : '<span class="hint">该城市没有单独黑名单，取消勾选请直接编辑右侧列表</span>')
    : "";
}

function renderTprecRecords() {
  const all = state.tprec.records || {};
  const wanted = tprecCity();
  const names = Object.keys(all).filter((city) => !wanted || city === wanted).sort();
  if (!names.length) {
    $("tprecBody").innerHTML = '<div class="empty">还没有任务记录</div>';
    return;
  }
  $("tprecBody").innerHTML = names.map((city) => {
    const tasks = all[city] || {};
    const rows = Object.keys(tasks).sort().map((name) => {
      const t = tasks[name] || {};
      const avg = typeof t.rounds_avg === "number" ? t.rounds_avg.toFixed(1) : "-";
      return `<tr>
        <td>${esc(name)}</td>
        <td>${esc(t.type || "")}</td>
        <td>${esc(t.level || "")}</td>
        <td>${t.count || 0}</td>
        <td>${avg}</td>
        <td>${t.last_rounds === undefined ? "-" : t.last_rounds}</td>
        <td class="mono">${esc(t.last || "")}</td>
        <td><button class="btn danger" data-black="${esc(name)}">拉黑</button></td>
      </tr>`;
    }).join("");
    return `<div style="margin-top:12px">
      <div class="row"><strong>${esc(city)}</strong><span class="hint">${Object.keys(tasks).length} 个任务</span></div>
      <table style="margin-top:8px">
        <thead><tr><th>任务</th><th>类型</th><th>等级</th><th>次数</th><th>平均回合</th><th>最近回合</th><th>最近时间</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }).join("");
}

async function saveTprecRule() {
  const city = tprecCity();
  const min = Number($("tprecMin").value || 0);
  const max = Number($("tprecMax").value || 120);
  try {
    await post("/api/task-rules/save", { city, min_level: min, max_level: max });
    banner("ok", [city ? `已保存 ${city} 的接取等级 ${min}~${max}` : `已保存默认接取等级 ${min}~${max}`]);
    await loadTaskRecords();
  } catch (error) {
    banner("err", ["保存规则失败：" + error.message]);
  }
}

async function addTprecBlacklist() {
  const name = ($("tprecBlackInput").value || "").trim();
  if (!name) return;
  const city = tprecCity();
  try {
    await post("/api/task-rules/blacklist", { city, task: name });
    $("tprecBlackInput").value = "";
    banner("ok", [city ? `已加入 ${city} 黑名单：${name}` : `已加入全局黑名单：${name}`]);
    await loadTaskRecords();
  } catch (error) {
    banner("err", ["添加黑名单失败：" + error.message]);
  }
}

async function blacklistTask(name) {
  const city = tprecCity();
  try {
    await post("/api/task-rules/blacklist", { city, task: name });
    banner("ok", [city ? `已加入 ${city} 黑名单：${name}` : `已加入全局黑名单：${name}`]);
    await loadTaskRecords();
  } catch (error) {
    banner("err", ["拉黑失败：" + error.message]);
  }
}

async function unblacklistTask(name, scope) {
  const city = scope === "city" ? tprecCity() : "";
  try {
    await post("/api/task-rules/unblacklist", { city, task: name });
    banner("ok", [city ? `已从 ${city} 黑名单移除：${name}` : `已从全局黑名单移除：${name}`]);
    await loadTaskRecords();
  } catch (error) {
    banner("err", ["解除失败：" + error.message]);
  }
}

/* ------------------------------------------------------------ 问题自纠 */

const SELFHEAL_SOURCE = { task: "任务", agent: "Agent", api: "接口", manual: "手动", combo: "组合", frontend: "页面" };
const SELFHEAL_STATUS = { pending: "待判定", kept: "已保留", deleted: "已删除" };
const REPAIR_LABELS = {
  idle: "未处理", queued: "排队中", running: "AI 修复中", fixed: "已修复",
  escalated: "需要你判断", stopped: "已停止", unavailable: "AI 不可用",
};

async function loadSelfheal(silent) {
  try {
    const filter = $("selfhealFilter").value || "all";
    const data = await api("/api/selfheal?status=" + encodeURIComponent(filter));
    state.selfheal.items = data.items || [];
    state.selfheal.stats = data.stats || {};
    state.selfheal.repair = data.repair || {};
    applySelfhealConfig(data.config || {});
    applyRepairConfig(data.repair || {});
    applyRepairVisibility(data.repair || {});
    renderSelfhealStats();
    renderSelfhealRepairStatus();
    renderSelfhealList();
    const ids = state.selfheal.items.map((item) => item.id);
    if (!state.selfheal.selected && ids.length) {
      await selectSelfheal(ids[0]);
    } else if (state.selfheal.selected && !ids.includes(state.selfheal.selected) && !silent) {
      state.selfheal.selected = "";
      clearSelfhealDetail();
      if (ids.length) await selectSelfheal(ids[0]);
    } else if (silent && state.selfheal.selected && state.selfheal.detail) {
      const row = state.selfheal.items.find((item) => item.id === state.selfheal.selected);
      if (row && row.repair_state !== state.selfheal.detail.repair_state) {
        await selectSelfheal(row.id);
      }
    }
  } catch (error) {
    if (!silent) banner("err", ["读取报错记录失败：" + error.message]);
  }
}

function applyRepairVisibility(repair) {
  const enabled = repair && repair.enabled === true;
  ["selfhealRepairHint", "btnSelfhealEscalation", "selfhealRepairSettings", "selfhealRepairPanel"].forEach((id) => {
    const element = $(id);
    if (element) element.classList.toggle("hidden", !enabled);
  });
}

function renderSelfhealRepairStatus() {
  const r = state.selfheal.repair || {};
  if (r.enabled !== true) return;
  const hint = $("selfhealRepairHint");
  if (!hint) return;
  if (r.running) {
    const cur = r.current || {};
    hint.textContent = `AI 修复中：${cur.title || cur.id || ""}（第 ${cur.attempt || 1} 次）`;
    hint.className = "hint warn";
  } else if (r.paused) {
    hint.textContent = "AI 修复暂停：" + (r.pauseReason || "等一会儿");
    hint.className = "hint warn";
  } else if (r.bridge === false) {
    hint.textContent = "AI 修复：DSH Bridge 未连接";
    hint.className = "hint bad";
  } else if (r.auto) {
    hint.textContent = "AI 修复：自动开启";
    hint.className = "hint ok";
  } else {
    hint.textContent = "AI 修复：自动关闭";
    hint.className = "hint";
  }
  renderSelfhealRepairDetail();
}

function renderSelfhealStats() {
  const stats = state.selfheal.stats || {};
  $("selfhealHint").textContent =
    `待判定 ${stats.pending || 0} · 保留 ${stats.kept || 0} · 已删除 ${stats.deleted || 0}` +
    (stats.mb != null ? ` · 占用 ${stats.mb} MB` : "") +
    (state.selfheal.repair?.enabled === true && stats.repair_escalated
      ? ` · 待判断 ${stats.repair_escalated}`
      : "");
  $("selfhealHint").title = stats.dir ? "目录：" + stats.dir : "";
  $("selfhealCount").textContent = `${state.selfheal.items.length} 条`;
}

function renderSelfhealList() {
  const box = $("selfhealList");
  if (!box) return;
  const items = state.selfheal.items;
  box.innerHTML = items.length
    ? items.map((item) => {
        const thumb = item.image_url
          ? `<img class="sh-thumb" src="${esc(item.image_url)}" alt="" loading="lazy">`
          : '<div class="sh-thumb sh-thumb-empty">无图</div>';
        const error = (item.error || "").split("\n")[0];
        const repair = state.selfheal.repair?.enabled === true && item.repair_state && item.repair_state !== "idle"
          ? `<span class="sh-badge ${esc(item.repair_state)}">${esc(REPAIR_LABELS[item.repair_state] || item.repair_state)}</span>`
          : "";
        return `<div class="sh-item ${item.id === state.selfheal.selected ? "active" : ""}" data-id="${esc(item.id)}">
          ${thumb}
          <div class="sh-main">
            <div class="sh-title" title="${esc(item.title)}">${esc(item.title)}</div>
            <div class="sh-sub">${esc(item.created_at)} · ${esc(item.source_label || SELFHEAL_SOURCE[item.source] || item.source)}${item.note ? " · 有备注" : ""}</div>
            ${error ? `<div class="sh-error" title="${esc(error)}">${esc(error)}</div>` : ""}
            <span class="sh-badge ${esc(item.status)}">${esc(SELFHEAL_STATUS[item.status] || item.status)}</span>
            ${repair}
          </div>
        </div>`;
      }).join("")
    : '<div class="empty">还没有报错记录。<br>任务失败、Agent 异常、接口报错时会自动抓图。</div>';
}

async function selectSelfheal(id) {
  if (!id) return;
  state.selfheal.selected = id;
  renderSelfhealList();
  try {
    const detail = await api("/api/selfheal/detail?id=" + encodeURIComponent(id));
    state.selfheal.detail = detail;
    renderSelfhealDetail(detail);
  } catch (error) {
    banner("err", ["读取详情失败：" + error.message]);
  }
}

function clearSelfhealDetail() {
  state.selfheal.detail = null;
  $("shTitle").textContent = "选择一条报错";
  $("shStatus").className = "sh-badge";
  $("shStatus").textContent = "";
  $("shMeta").innerHTML = "";
  $("shError").style.display = "none";
  $("shImageWrap").style.display = "none";
  $("shNoImage").style.display = "none";
  $("shNote").value = "";
  $("shLogs").textContent = "—";
  $("shRepairState").className = "sh-badge";
  $("shRepairState").textContent = "未处理";
  $("shRepairInfo").textContent = "发现新报错后 AI 会自动读取截图和日志";
  $("shRepairSummary").style.display = "none";
  $("shRepairSummary").textContent = "";
  ["btnShKeep", "btnShPending", "btnShDelete", "btnShPurge", "btnShSaveNote",
   "btnShRepair", "btnShRepairStop", "btnShRepairReset"].forEach((key) => {
    $(key).disabled = true;
  });
}

function renderSelfhealDetail(data) {
  $("shTitle").textContent = data.title || "报错";
  const badge = $("shStatus");
  badge.className = "sh-badge " + data.status;
  badge.textContent = SELFHEAL_STATUS[data.status] || data.status;
  const meta = [
    "时间：" + esc(data.created_at),
    "来源：" + esc(data.source_label || SELFHEAL_SOURCE[data.source] || data.source),
    data.task_name || data.entry ? "任务：" + esc(data.task_name || data.entry) : "",
    data.state ? "状态：" + esc(data.state) : "",
    data.path ? "路径：" + esc(data.path) : "",
    data.width ? "尺寸：" + data.width + "×" + data.height : "",
  ].filter(Boolean);
  $("shMeta").innerHTML = meta.map((line) => `<span>${line}</span>`).join("");

  const errorBox = $("shError");
  errorBox.textContent = data.error || "";
  errorBox.style.display = data.error ? "" : "none";

  const image = $("shImage");
  if (data.has_image && data.image_url) {
    const src = data.image_url + "&t=" + Date.now();
    image.src = src;
    $("shImageLink").href = src;
    $("shImageWrap").style.display = "";
    $("shNoImage").style.display = "none";
  } else {
    image.removeAttribute("src");
    $("shImageWrap").style.display = "none";
    $("shNoImage").style.display = "";
  }

  if (document.activeElement !== $("shNote")) $("shNote").value = data.note || "";
  $("shSaved").textContent = "";
  $("shLogs").textContent = data.log_text || "（没有日志）";
  ["btnShKeep", "btnShPending", "btnShDelete", "btnShPurge", "btnShSaveNote",
   "btnShRepair", "btnShRepairStop", "btnShRepairReset"].forEach((key) => {
    $(key).disabled = false;
  });
  renderSelfhealRepairDetail();
}

function renderSelfhealRepairDetail() {
  const data = state.selfheal.detail;
  if (!data || !$("shRepairState")) return;
  const repair = state.selfheal.repair || {};
  if (repair.enabled !== true) {
    $("selfhealRepairPanel")?.classList.add("hidden");
    return;
  }
  $("selfhealRepairPanel")?.classList.remove("hidden");
  const current = repair.current || {};
  const stateName = data.repair_state || "idle";
  const badge = $("shRepairState");
  badge.className = "sh-badge " + stateName;
  badge.textContent = REPAIR_LABELS[stateName] || stateName;
  const bits = [];
  if (data.repair_attempts) bits.push(`已尝试 ${data.repair_attempts} 次`);
  if (current.id === data.id) bits.push(`正在${current.state === "running" ? "修复" : current.state}`);
  if (data.repair_message) bits.push(data.repair_message);
  if (data.repair_session) bits.push(`会话 ${data.repair_session}`);
  $("shRepairInfo").textContent = bits.join(" · ") || "发现新报错后 AI 会自动读取截图和日志";
  const summary = data.repair_summary || "";
  $("shRepairSummary").textContent = summary;
  $("shRepairSummary").style.display = summary ? "" : "none";
  const busy = stateName === "running" || stateName === "queued";
  $("btnShRepair").disabled = busy;
  $("btnShRepairStop").disabled = !busy && current.id !== data.id;
  $("btnShRepairReset").disabled = busy;
}

async function markSelfheal(status) {
  const detail = state.selfheal.detail;
  if (!detail) { banner("err", ["先选择一条报错"]); return; }
  try {
    await post("/api/selfheal/mark", { id: detail.id, status, note: $("shNote").value });
    banner("ok", ["已标记为：" + SELFHEAL_STATUS[status]]);
    await loadSelfheal(true);
    await selectSelfheal(detail.id);
  } catch (error) {
    banner("err", ["标记失败：" + error.message]);
  }
}

async function saveSelfhealNote() {
  const detail = state.selfheal.detail;
  if (!detail) return;
  try {
    await post("/api/selfheal/note", { id: detail.id, note: $("shNote").value });
    $("shSaved").textContent = "已保存 " + new Date().toLocaleTimeString();
    await loadSelfheal(true);
  } catch (error) {
    banner("err", ["保存备注失败：" + error.message]);
  }
}

async function purgeSelfheal() {
  const detail = state.selfheal.detail;
  if (!detail) return;
  if (!confirm(`彻底删除「${detail.title}」？截图和记录都会消失，不能恢复。`)) return;
  try {
    await post("/api/selfheal/purge", { id: detail.id });
    banner("ok", ["已彻底删除"]);
    state.selfheal.selected = "";
    state.selfheal.detail = null;
    clearSelfhealDetail();
    await loadSelfheal(true);
  } catch (error) {
    banner("err", ["删除失败：" + error.message]);
  }
}

async function purgeSelfhealDeleted() {
  if (!confirm("彻底删除回收站里的全部记录？此操作不能恢复。")) return;
  try {
    const data = await post("/api/selfheal/purge", {});
    banner("ok", [`已彻底删除 ${data.removed || 0} 条`]);
    state.selfheal.selected = "";
    state.selfheal.detail = null;
    clearSelfhealDetail();
    await loadSelfheal();
  } catch (error) {
    banner("err", ["删除失败：" + error.message]);
  }
}

async function cleanupSelfheal() {
  try {
    const data = await post("/api/selfheal/cleanup", {});
    const parts = [`已彻底删除 ${data.removed || 0} 条`];
    if (data.trashed) parts.push(`${data.trashed} 条进入回收站`);
    banner("ok", [parts.join("，")]);
    await loadSelfheal();
  } catch (error) {
    banner("err", ["清理失败：" + error.message]);
  }
}

async function captureSelfheal() {
  $("btnSelfhealCapture").disabled = true;
  try {
    const data = await post("/api/selfheal/capture", {});
    if (!data.item) {
      banner("err", ["没有保存：检查「自动保存报错截图」开关"]);
      return;
    }
    banner("ok", ["已保存现场" + (data.hasImage ? "（含截图）" : "（设备未连接，只有日志）")]);
    await loadSelfheal(true);
    await selectSelfheal(data.item.id);
  } catch (error) {
    banner("err", ["抓取失败：" + error.message]);
  } finally {
    $("btnSelfhealCapture").disabled = false;
  }
}

function applyRepairConfig(config) {
  if (!config) return;
  $("selfhealRepairAuto").checked = config.auto !== false;
  if (config.maxAttempts) $("shRepairAttempts").value = config.maxAttempts;
  if (config.timeoutSeconds) $("shRepairTimeout").value = config.timeoutSeconds;
}

async function repairSelfheal() {
  const detail = state.selfheal.detail;
  if (!detail) { banner("err", ["先选择一条报错"]); return; }
  if (state.selfheal.repair?.enabled !== true) {
    banner("err", ["AI 修复已关闭，当前只保留报错记录"]);
    return;
  }
  try {
    const data = await post("/api/repair/start", { id: detail.id });
    banner("ok", [data.message || "已加入 AI 修复队列"]);
    await loadSelfheal(true);
    await selectSelfheal(detail.id);
  } catch (error) {
    banner("err", ["启动修复失败：" + error.message]);
  }
}

async function stopRepairSelfheal() {
  try {
    await post("/api/repair/stop", {});
    banner("ok", ["已请求停止 AI 修复"]);
    await loadSelfheal(true);
    if (state.selfheal.detail) await selectSelfheal(state.selfheal.detail.id);
  } catch (error) {
    banner("err", ["停止失败：" + error.message]);
  }
}

async function resetRepairSelfheal() {
  const detail = state.selfheal.detail;
  if (!detail) return;
  try {
    await post("/api/repair/reset", { id: detail.id });
    banner("ok", ["已重置这条报错的修复状态"]);
    await loadSelfheal(true);
    await selectSelfheal(detail.id);
  } catch (error) {
    banner("err", ["重置失败：" + error.message]);
  }
}

async function clearAllSelfheal() {
  const stats = state.selfheal.stats || {};
  if (!confirm(`一键清除全部报错截图？\n当前 ${stats.total || 0} 条记录、${stats.mb || 0} MB，含回收站，不能恢复。`)) return;
  try {
    const data = await post("/api/selfheal/clear", { confirm: true });
    banner("ok", [`已清除 ${data.removed || 0} 条报错截图`]);
    state.selfheal.selected = "";
    state.selfheal.detail = null;
    clearSelfhealDetail();
    await loadSelfheal();
  } catch (error) {
    banner("err", ["清除失败：" + error.message]);
  }
}

function applySelfhealConfig(config) {
  if ($("selfhealSettings").contains(document.activeElement)) return;
  $("selfhealAuto").checked = config.autoCapture !== false;
  $("shMaxItems").value = config.maxItems ?? 200;
  $("shMaxMB").value = config.maxMB ?? 400;
  $("shPendingDays").value = config.pendingDays ?? 7;
  $("shTrashDays").value = config.trashDays ?? 3;
  $("shDedupeSeconds").value = config.dedupeSeconds ?? 15;
}

async function saveSelfhealConfig() {
  const payload = {
    autoCapture: $("selfhealAuto").checked,
    maxItems: Number($("shMaxItems").value) || 200,
    maxMB: Number($("shMaxMB").value) || 400,
    pendingDays: Number($("shPendingDays").value) || 0,
    trashDays: Number($("shTrashDays").value) || 0,
    dedupeSeconds: Number($("shDedupeSeconds").value) || 0,
  };
  try {
    const data = await post("/api/selfheal/config", payload);
    const repairData = await post("/api/repair/config", {
      auto: $("selfhealRepairAuto").checked,
      maxAttempts: Number($("shRepairAttempts").value) || 2,
      timeoutSeconds: Number($("shRepairTimeout").value) || 600,
    });
    applySelfhealConfig(data.config || {});
    applyRepairConfig(repairData.config || {});
    banner("ok", ["问题自纠与 AI 修复设置已保存"]);
    await loadSelfheal(true);
  } catch (error) {
    banner("err", ["保存设置失败：" + error.message]);
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
$("btnSceneReload").addEventListener("click", () => loadScenes(false, true));
$("sceneSearch").addEventListener("input", renderScenes);
$("sceneGroup").addEventListener("change", renderScenes);
$("sceneBody").addEventListener("click", (e) => {
  const taskBtn = e.target.closest("button[data-scene-task]");
  if (taskBtn) { gotoSceneTask(taskBtn.dataset.sceneTask); return; }
  const symbolBtn = e.target.closest("button[data-source-symbol]");
  if (symbolBtn) { showSource({ symbol: symbolBtn.dataset.sourceSymbol }); return; }
  const nodeBtn = e.target.closest("button[data-source-node]");
  if (nodeBtn) { showSource({ node: nodeBtn.dataset.sourceNode }); }
});

$("btnUnknownReload").addEventListener("click", () => {
  state.unknowns.key = "";
  loadUnknowns(false);
});
$("btnUnknownClear").addEventListener("click", async () => {
  if (!confirm("清空全部未知界面记录？截图和记录都会删除，不能恢复（返回方式表不受影响）。")) return;
  try {
    const result = await post("/api/unknowns/clear", { confirm: true });
    banner("ok", [`已清空 ${result.removed || 0} 条未知界面记录`]);
    state.unknowns.key = "";
    await loadUnknowns(true);
  } catch (error) {
    banner("err", ["清空失败：" + error.message]);
  }
});
$("unknownList").addEventListener("click", async (e) => {
  const saveBtn = e.target.closest("button[data-unknown-save]");
  if (saveBtn) {
    const id = saveBtn.dataset.unknownSave;
    const select = e.target.closest(".unknown-item")?.querySelector("select[data-unknown-route]");
    await saveUnknownRoute(id, select ? select.value : "");
    return;
  }
  const noteBtn = e.target.closest("button[data-unknown-note]");
  if (noteBtn) {
    const id = noteBtn.dataset.unknownNote;
    const current = (state.unknowns.items || []).find((item) => item.id === id);
    const text = prompt("备注（只给自己看，会写进 debug/unknown）", (current && current.note) || "");
    if (text === null) return;
    try {
      await post("/api/unknowns/note", { id, note: text });
      banner("ok", ["备注已保存"]);
      state.unknowns.key = "";
      await loadUnknowns(true);
    } catch (error) {
      banner("err", ["保存备注失败：" + error.message]);
    }
    return;
  }
  const deleteBtn = e.target.closest("button[data-unknown-delete]");
  if (deleteBtn) {
    if (!confirm("删除这条未知界面记录？截图和记录都会删除，不能恢复。")) return;
    try {
      await post("/api/unknowns/delete", { id: deleteBtn.dataset.unknownDelete });
      banner("ok", ["已删除"]);
      state.unknowns.key = "";
      await loadUnknowns(true);
    } catch (error) {
      banner("err", ["删除失败：" + error.message]);
    }
    return;
  }
  const sourceBtn = e.target.closest("button[data-source-path]");
  if (sourceBtn) {
    showSource({ path: sourceBtn.dataset.sourcePath, line: Number(sourceBtn.dataset.sourceLine || 1) });
  }
});

$("btnSourceOpen").addEventListener("click", openSourceInEditor);
$("btnSourceClose").addEventListener("click", () => {
  const dialog = $("sourceDialog");
  if (dialog && typeof dialog.close === "function") dialog.close();
  else if (dialog) dialog.removeAttribute("open");
});
$("sourceLocations").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-source-path]");
  if (btn) showSource({ path: btn.dataset.sourcePath, line: Number(btn.dataset.sourceLine || 1) });
});
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
$("btnTprecReload").addEventListener("click", () => loadTaskRecords());
$("tprecCity").addEventListener("change", () => {
  state.tprec.city = $("tprecCity").value || "";
  state.tprec.touched = true;
  renderTprecRule();
  renderTprecPool();
  renderTprecBlacklist();
  renderTprecRecords();
});
$("btnTprecSaveRule").addEventListener("click", saveTprecRule);
$("btnTprecBlackAdd").addEventListener("click", addTprecBlacklist);
$("tprecBlackInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") addTprecBlacklist();
});
$("tprecBody").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-black]");
  if (btn) blacklistTask(btn.dataset.black);
});
$("tprecPool").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-black]");
  if (btn) blacklistTask(btn.dataset.black);
});
$("tprecGlobalBlack").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-unblack-global]");
  if (btn) unblacklistTask(btn.dataset.unblackGlobal, "global");
});
$("tprecCityBlack").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-unblack-city]");
  if (btn) unblacklistTask(btn.dataset.unblackCity, "city");
});
$("btnNotifySave").addEventListener("click", saveNotify);
$("btnNotifyTest").addEventListener("click", testNotify);

$("selfhealList").addEventListener("click", (e) => {
  const item = e.target.closest(".sh-item[data-id]");
  if (item) selectSelfheal(item.dataset.id);
});
$("selfhealFilter").addEventListener("change", () => {
  state.selfheal.selected = "";
  state.selfheal.detail = null;
  clearSelfhealDetail();
  loadSelfheal();
});
$("btnSelfhealReload").addEventListener("click", () => loadSelfheal());
$("btnSelfhealCapture").addEventListener("click", captureSelfheal);
$("btnSelfhealReport").addEventListener("click", () => window.open("/api/selfheal/report", "_blank"));
$("btnSelfhealEscalation").addEventListener("click", () => window.open("/api/repair/escalation", "_blank"));
$("btnSelfhealCleanup").addEventListener("click", cleanupSelfheal);
$("btnSelfhealPurgeDeleted").addEventListener("click", purgeSelfhealDeleted);
$("btnSelfhealClearAll").addEventListener("click", clearAllSelfheal);
$("btnSelfhealSettings").addEventListener("click", () => {
  $("selfhealSettings").hidden = !$("selfhealSettings").hidden;
});
$("btnSelfhealSaveConfig").addEventListener("click", saveSelfhealConfig);
$("selfhealAuto").addEventListener("change", saveSelfhealConfig);
$("selfhealRepairAuto").addEventListener("change", saveSelfhealConfig);
$("btnShKeep").addEventListener("click", () => markSelfheal("kept"));
$("btnShPending").addEventListener("click", () => markSelfheal("pending"));
$("btnShDelete").addEventListener("click", () => markSelfheal("deleted"));
$("btnShPurge").addEventListener("click", purgeSelfheal);
$("btnShSaveNote").addEventListener("click", saveSelfhealNote);
$("btnShRepair").addEventListener("click", repairSelfheal);
$("btnShRepairStop").addEventListener("click", stopRepairSelfheal);
$("btnShRepairReset").addEventListener("click", resetRepairSelfheal);

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
  ["pirate", "海盗事件"],
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
if (["home", "tasks", "combos", "shot", "logs", "selfheal", "alerts", "records", "mercs", "tprec", "notify"].includes(startTab)) {
  switchTab(startTab);
}

renderRuleSelects();
clearSelfhealDetail();
refreshState();
setInterval(refreshState, 3000);
loadScenes();
if (!timers.scenes) {
  timers.scenes = setInterval(() => { if (state.activeTab === "home") loadScenes(true); }, 10000);
}
