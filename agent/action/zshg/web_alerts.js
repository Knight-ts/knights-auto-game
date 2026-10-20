"use strict";
/* 好娃提醒面板：紧凑组合列表（点「详情」展开）+ 按种族分类的完整特性表。
 *
 * 网页控制台（web_console.html）和独立网页版（webapp/static/index.html）共用本文件，
 * 两边的差异只在接口地址，通过 window.ALERT_ENDPOINTS 在页面里覆盖：
 *     load : GET  读取条件表 + 特性表
 *     save : POST {conditions:[...]} 保存并同步三处配置
 *     sync : POST {caseNames:[...]} 同步实例勾选（没有就隐藏按钮）
 */
(function () {
  const ATTRS = ["力量", "体质", "技巧", "感知", "敏捷", "意志"];
  const GRADES = ["", "E", "D", "C", "B", "A", "S", "SS"];

  const ENDPOINTS = Object.assign(
    { load: "/api/state", save: "/api/save", sync: "/api/instance/sync" },
    window.ALERT_ENDPOINTS || {}
  );

  const data = {
    conditions: [],
    catalog: [],
    groups: [],
    featureRaces: {},
    raceMeta: {},
    counts: { groups: 0, features: 0 },
    loaded: false,
  };

  const view = {
    open: new Set(),
    target: -1,
    race: "",
    search: "",
    featureSearch: "",
  };

  const $ = (id) => document.getElementById(id);
  const esc = (value) =>
    String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function toast(kind, lines) {
    if (typeof window.banner === "function") window.banner(kind, lines);
    else if (kind === "err") console.error(lines.join("；"));
    else console.log(lines.join("；"));
  }

  function debounce(fn, delay) {
    let timer = null;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(fn, delay);
    };
  }

  /* ------------------------------------------------------------ 接口 */

  async function request(path, payload) {
    const options =
      payload === undefined
        ? {}
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          };
    const response = await fetch(path, options);
    const type = response.headers.get("Content-Type") || "";
    const body = type.includes("json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message =
        (body && (body.error || (body.errors || []).join("；"))) || response.statusText;
      const error = new Error(message);
      error.payload = body;
      throw error;
    }
    return body;
  }

  /* ------------------------------------------------------------ 数据整理 */

  function normalizeCondition(raw) {
    const source = raw || {};
    const grades = {};
    const mins = {};
    ATTRS.forEach((attr) => {
      grades[attr] = (source.grades && source.grades[attr]) || "";
      const value = source.mins ? source.mins[attr] : "";
      mins[attr] = value === null || value === undefined ? "" : String(value);
    });
    return Object.assign({}, source, {
      id: source.id || "",
      name: source.name || "",
      caseName: source.caseName || source.name || "",
      description: source.description || "",
      note: source.note || "",
      node: source.node || "",
      defaultChecked: Boolean(source.defaultChecked),
      features: Array.isArray(source.features) ? source.features.slice() : [],
      orLines: Array.isArray(source.orLines) ? source.orLines.slice() : [],
      grades: grades,
      mins: mins,
    });
  }

  function targetFeatures() {
    const condition = data.conditions[view.target];
    return condition ? condition.features : [];
  }

  function racesOf(condition) {
    const races = [];
    (condition.features || []).forEach((name) => {
      (data.featureRaces[name] || []).forEach((label) => {
        if (!races.some((item) => item.label === label)) {
          races.push({ label: label, kind: (data.raceMeta[label] || {}).kind || "bloodline" });
        }
      });
    });
    return races;
  }

  function potentialSummary(condition) {
    const parts = [];
    ATTRS.forEach((attr) => {
      const grade = (condition.grades || {})[attr];
      const min = (condition.mins || {})[attr];
      if (grade) parts.push(`${attr}≥${grade}`);
      if (min !== "" && min !== null && min !== undefined) parts.push(`${attr}≥${min}`);
    });
    const ors = (condition.orLines || [])
      .map((line) => String(line).replace(/>=/g, "≥").trim())
      .filter(Boolean);
    if (ors.length) parts.push("或 " + ors.join(" / "));
    return parts.length ? parts.join(" · ") : "不看属性";
  }

  function featureHaystack(feature) {
    return [
      feature.name,
      feature.level,
      feature.position,
      feature.effect,
      feature.lifespan,
      (feature.titles || []).join(" "),
      feature.hint,
    ]
      .join(" ")
      .toLowerCase();
  }

  /* ------------------------------------------------------------ 组合行 */

  function featurePills(condition) {
    if (!condition.features.length) return '<span class="combo-attr">不看特性</span>';
    return condition.features
      .map((name) => {
        const unknown = !data.catalog.includes(name);
        return `<span class="feat-pill ${unknown ? "unknown" : ""}" ${unknown ? 'title="不在特性表里，检查是否写错"' : ""}>${esc(name)}</span>`;
      })
      .join("");
  }

  function featureChips(condition, index) {
    if (!condition.features.length) {
      return '<span class="hint">还没有特性，可以从下面的特性表「＋ 加入」</span>';
    }
    return condition.features
      .map((name, featureIndex) => {
        const unknown = !data.catalog.includes(name);
        return `<span class="chip-feat ${unknown ? "unknown" : ""}">${esc(name)}<button title="移除" data-act="rm-feature" data-i="${index}" data-fi="${featureIndex}">×</button></span>`;
      })
      .join("");
  }

  function comboRow(condition, index) {
    const open = view.open.has(index);
    const raceTags = racesOf(condition)
      .map(
        (race) =>
          `<span class="badge race ${race.kind === "commoner" ? "commoner" : ""}">${esc(race.label)}</span>`
      )
      .join("");
    const badges = [];
    if (!condition.nodeExists) badges.push('<span class="badge new">保存时建检测节点</span>');
    if (!condition.caseExists) badges.push('<span class="badge new">保存时建界面选项</span>');
    if (condition.defaultChecked) badges.push('<span class="badge">默认勾选</span>');

    return `
  <article class="combo ${open ? "open" : ""}" data-index="${index}" id="combo-${index}">
    <div class="combo-head" data-i="${index}" title="点一下展开 / 收起详情">
      <span class="combo-caret">▶</span>
      <div class="combo-main">
        <div class="combo-line">
          <span class="combo-title">${esc(condition.caseName || condition.name || "未命名组合")}</span>
          ${raceTags}${badges.join("")}
        </div>
        <div class="combo-line">
          <span class="combo-feats">${featurePills(condition)}</span>
          <span class="combo-attr">属性：${esc(potentialSummary(condition))}</span>
        </div>
      </div>
      <div class="combo-actions">
        <button class="btn small" data-act="toggle" data-i="${index}">${open ? "收起" : "详情"}</button>
        <button class="btn small danger" data-act="remove" data-i="${index}">删除</button>
      </div>
    </div>
    ${open ? `<div class="combo-body">${comboBody(condition, index)}</div>` : ""}
  </article>`;
  }

  function comboBody(condition, index) {
    const attrs = ATTRS.map(
      (attr) => `
      <tr><td>${attr}</td>
        <td><select class="grade" data-i="${index}" data-attr="${attr}" data-f="grade">${GRADES.map(
          (grade) =>
            `<option value="${grade}" ${grade === (condition.grades[attr] || "") ? "selected" : ""}>${grade || "—"}</option>`
        ).join("")}</select></td>
        <td><input class="min" type="number" step="0.01" min="0" max="5" placeholder="不限"
             value="${esc(condition.mins[attr])}" data-i="${index}" data-attr="${attr}" data-f="min"></td>
      </tr>`
    ).join("");

    return `
  <div class="form-grid">
    <label class="field grow">界面显示名（勾选项名字）
      <input type="text" data-i="${index}" data-f="caseName" value="${esc(condition.caseName)}"></label>
    <label class="field">内部 ID
      <input class="mono" type="text" data-i="${index}" data-f="id" value="${esc(condition.id)}"></label>
    <label class="field">条件名
      <input type="text" data-i="${index}" data-f="name" value="${esc(condition.name)}"></label>
    <label class="field">检测节点
      <input class="mono" type="text" data-i="${index}" data-f="node" value="${esc(condition.node)}"></label>
    <label class="field wide">说明（写进 MaaGC 界面选项的描述）
      <input type="text" data-i="${index}" data-f="description" value="${esc(condition.description)}"
             placeholder="例：技巧SS + 力量S，只看特性不看属性"></label>
    <label class="field wide">备注（只给自己看，保存在条件表里）
      <textarea data-i="${index}" data-f="note" placeholder="例：只要男娃 / 活过 3 月 / 优先保留，别急着招募">${esc(condition.note)}</textarea></label>
    <label class="row" style="align-self:end">
      <input type="checkbox" data-i="${index}" data-f="defaultChecked" ${condition.defaultChecked ? "checked" : ""}> 默认勾选
    </label>
  </div>
  <div class="combo-split">
    <div>
      <div class="hint" style="margin-bottom:6px">必需特性（全部命中；留空表示不看特性）</div>
      <div class="feature-chips" id="chips-${index}">${featureChips(condition, index)}</div>
      <div class="row">
        <input type="text" list="catalog" placeholder="输入特性名，如 太阳之子"
               data-i="${index}" data-role="feature-input" style="flex:1 1 220px">
        <button class="btn" data-act="add-feature" data-i="${index}">添加</button>
        <button class="btn" data-act="pick-feature" data-i="${index}">从特性表挑</button>
      </div>
    </div>
    <div>
      <div class="hint" style="margin-bottom:6px">属性门槛（等级为「≥」）</div>
      <table class="attrs">${attrs}</table>
    </div>
  </div>
  <label class="field" style="margin-top:10px">或条件（每行一条，任一满足即可；格式 敏捷&gt;=0.3，同一行用「且」）
    <textarea data-i="${index}" data-f="orLines" placeholder="意志&gt;=0.3&#10;敏捷&gt;=0.3">${esc((condition.orLines || []).join("\n"))}</textarea>
  </label>`;
  }

  /* ------------------------------------------------------------ 组合渲染 / 交互 */

  function renderAlerts() {
    const list = $("alert-list");
    if (!list) return;
    if (!data.conditions.length) {
      list.innerHTML =
        '<div class="empty">还没有组合：点「＋ 新增组合」，或者从上面的特性表里挑特性配一个</div>';
      updateStats();
      return;
    }
    list.innerHTML = data.conditions.map(comboRow).join("");
    updateStats();
    applyComboFilter();
  }

  function updateStats() {
    const stats = $("alert-stats");
    if (!stats) return;
    const total = data.conditions.length;
    const checked = data.conditions.filter((item) => item.defaultChecked).length;
    stats.textContent = `${total} 个组合 · ${checked} 个默认勾选 · 特性表 ${data.counts.features} 项`;
  }

  function applyComboFilter() {
    const query = view.search.trim().toLowerCase();
    document.querySelectorAll("#alert-list .combo").forEach((element) => {
      const index = Number(element.dataset.index);
      const condition = data.conditions[index];
      if (!condition) return;
      const haystack = [
        condition.caseName,
        condition.name,
        condition.description,
        condition.note,
        (condition.features || []).join(" "),
        racesOf(condition)
          .map((race) => race.label)
          .join(" "),
        potentialSummary(condition),
      ]
        .join(" ")
        .toLowerCase();
      element.classList.toggle("hidden", Boolean(query) && !haystack.includes(query));
    });
  }

  function setOpen(index, open) {
    const condition = data.conditions[index];
    if (!condition) return;
    if (open) {
      view.open.add(index);
      view.target = index;
    } else {
      view.open.delete(index);
    }
    const element = document.getElementById(`combo-${index}`);
    if (element) {
      element.classList.toggle("open", open);
      const body = element.querySelector(".combo-body");
      if (open && !body) {
        element.insertAdjacentHTML("beforeend", `<div class="combo-body">${comboBody(condition, index)}</div>`);
      } else if (!open && body) {
        body.remove();
      }
      const button = element.querySelector(".combo-actions button[data-act='toggle']");
      if (button) button.textContent = open ? "收起" : "详情";
    }
    if (open) updateTarget();
  }

  function toggleExpandAll() {
    const button = $("btn-alert-expand");
    const allOpen =
      data.conditions.length > 0 && data.conditions.every((item, index) => view.open.has(index));
    data.conditions.forEach((item, index) => setOpen(index, !allOpen));
    if (button) button.textContent = allOpen ? "展开全部" : "收起全部";
  }

  function addCombo() {
    let seq = data.conditions.length + 1;
    const ids = new Set(data.conditions.map((item) => item.id));
    const cases = new Set(data.conditions.map((item) => item.caseName));
    while (ids.has(`type_${seq}`) || cases.has(`新组合 ${seq}`)) seq += 1;

    data.conditions.push(
      normalizeCondition({
        id: `type_${seq}`,
        name: `新组合 ${seq}`,
        caseName: `新组合 ${seq}`,
        node: `检测_新组合${seq}`,
        description: "",
        note: "",
        defaultChecked: false,
        features: [],
        grades: {},
        mins: {},
        orLines: [],
      })
    );
    const index = data.conditions.length - 1;
    view.open.add(index);
    view.target = index;
    renderAlerts();
    updateTarget();
    focusCombo(index);
  }

  function focusCombo(index) {
    const element = document.getElementById(`combo-${index}`);
    if (!element) return;
    element.scrollIntoView({ behavior: "smooth", block: "center" });
    const input = element.querySelector('input[data-f="caseName"]');
    if (input) {
      input.focus();
      input.select();
    }
  }

  function removeCombo(index) {
    const condition = data.conditions[index];
    if (!condition) return;
    const name = condition.caseName || condition.name || "这个组合";
    if (!window.confirm(`确定删除「${name}」？保存后会同时移除检测节点和界面选项。`)) return;

    data.conditions.splice(index, 1);
    const open = new Set();
    view.open.forEach((key) => {
      if (key < index) open.add(key);
      else if (key > index) open.add(key - 1);
    });
    view.open = open;
    if (view.target === index) view.target = -1;
    else if (view.target > index) view.target -= 1;

    renderAlerts();
    updateTarget();
  }

  function updateField(event) {
    const target = event.target;
    const index = Number(target.dataset.i);
    const field = target.dataset.f;
    if (!Number.isInteger(index) || !field || !data.conditions[index]) return;
    const condition = data.conditions[index];

    if (field === "grade") condition.grades[target.dataset.attr] = target.value;
    else if (field === "min") condition.mins[target.dataset.attr] = target.value;
    else if (field === "orLines")
      condition.orLines = target.value
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    else if (field === "defaultChecked") {
      condition.defaultChecked = target.checked;
      updateStats();
    } else if (field === "caseName") {
      const oldName = condition.caseName;
      condition.caseName = target.value;
      if (condition.node === "检测_" + oldName) {
        condition.node = "检测_" + target.value;
        const nodeInput = document.querySelector(`input[data-f="node"][data-i="${index}"]`);
        if (nodeInput) nodeInput.value = condition.node;
      }
      updateTitle(index);
    } else {
      condition[field] = target.value;
    }

    if (field === "grade" || field === "min" || field === "orLines") updateAttr(index);
  }

  function updateTitle(index) {
    const element = document.getElementById(`combo-${index}`);
    const title = element && element.querySelector(".combo-title");
    if (title) title.textContent = data.conditions[index].caseName || data.conditions[index].name || "未命名组合";
  }

  function updateAttr(index) {
    const element = document.getElementById(`combo-${index}`);
    const attr = element && element.querySelector(".combo-attr");
    if (attr) attr.textContent = "属性：" + potentialSummary(data.conditions[index]);
  }

  function refreshFeatureUI(index) {
    const condition = data.conditions[index];
    if (!condition) return;
    const chips = document.getElementById(`chips-${index}`);
    if (chips) chips.innerHTML = featureChips(condition, index);
    const element = document.getElementById(`combo-${index}`);
    if (element) {
      const pills = element.querySelector(".combo-feats");
      if (pills) pills.innerHTML = featurePills(condition);
    }
  }

  function addFeature(index, name) {
    const condition = data.conditions[index];
    if (!condition) return;
    const value = String(name || "").trim();
    if (!value) return;
    if (condition.features.includes(value)) {
      toast("err", [`「${value}」已经在这个组合里了`]);
      return;
    }
    condition.features.push(value);
    refreshFeatureUI(index);
    setPicked(value, true);
  }

  function removeFeature(index, featureIndex) {
    const condition = data.conditions[index];
    if (!condition) return;
    const [name] = condition.features.splice(featureIndex, 1);
    refreshFeatureUI(index);
    setPicked(name, false);
  }

  function toggleFeature(name) {
    const condition = data.conditions[view.target];
    if (!condition) {
      toast("err", ["先点某个组合的「详情」，再往它里面加特性"]);
      return;
    }
    const at = condition.features.indexOf(name);
    if (at >= 0) condition.features.splice(at, 1);
    else condition.features.push(name);
    refreshFeatureUI(view.target);
    setPicked(name, at < 0);
  }

  function setPicked(name, picked) {
    document.querySelectorAll("#feature-scroll button[data-feature]").forEach((button) => {
      if (button.dataset.feature !== name) return;
      button.textContent = picked ? "已加入" : "＋ 加入";
      button.classList.toggle("primary", picked);
      const row = button.closest("tr");
      if (row) row.classList.toggle("picked", picked);
    });
  }

  function pickFeature(index) {
    setOpen(index, true);
    view.target = index;
    updateTarget();
    toggleFeaturePanel(true);
    const panel = $("feature-panel");
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
    const search = $("feature-search");
    if (search) {
      search.focus();
      search.select();
    }
  }

  /* ------------------------------------------------------------ 特性表 */

  function renderRaceNav() {
    const nav = $("race-nav");
    if (!nav) return;
    const parts = [
      `<button data-race="" class="${view.race ? "" : "active"}"><span>全部种族</span><span class="count">${data.counts.features}</span></button>`,
    ];
    let kind = "";
    data.groups.forEach((group) => {
      if (group.kind !== kind) {
        kind = group.kind;
        parts.push(`<div class="race-kind">${esc(group.kindLabel || "")}</div>`);
      }
      parts.push(
        `<button data-race="${esc(group.id)}" class="${view.race === group.id ? "active" : ""}" title="${esc(group.region || group.kindLabel || "")}"><span>${esc(group.label)}</span><span class="count">${group.features.length}</span></button>`
      );
    });
    nav.innerHTML = parts.join("");
  }

  function featureRow(feature) {
    const picked = targetFeatures().includes(feature.name);
    const alias = (feature.titles || []).length
      ? `<div class="feat-alias">${esc(feature.titles.join(" / "))}</div>`
      : "";
    return `<tr class="${picked ? "picked" : ""}">
      <td class="feat-name">${esc(feature.name)}${alias}</td>
      <td class="feat-level">${esc(feature.level) || "—"}</td>
      <td class="feat-level">${esc(feature.position) || "—"}</td>
      <td class="feat-effect" title="${esc(feature.hint)}">${esc(feature.effect) || "—"}</td>
      <td class="feat-life">${esc(feature.lifespan) || "—"}</td>
      <td class="feat-add"><button class="btn small ${picked ? "primary" : ""}" data-feature="${esc(feature.name)}">${picked ? "已加入" : "＋ 加入"}</button></td>
    </tr>`;
  }

  function renderFeatureTable() {
    const box = $("feature-scroll");
    if (!box) return;
    const query = view.featureSearch.trim().toLowerCase();
    const groups = view.race ? data.groups.filter((group) => group.id === view.race) : data.groups;
    const sections = [];
    let shown = 0;

    groups.forEach((group) => {
      const rows = group.features.filter((feature) => !query || featureHaystack(feature).includes(query));
      if (!rows.length) return;
      shown += rows.length;
      sections.push(`
      <section class="race-block">
        <div class="race-head">
          <span class="badge">${esc(group.kindLabel || "")}</span>
          <span class="race-title">${esc(group.label)}</span>
          ${group.region ? `<span class="hint">${esc(group.region)}</span>` : ""}
          <span class="hint">${rows.length} / ${group.features.length} 个特性</span>
        </div>
        <table class="features">
          <thead><tr><th>特性</th><th>等级</th><th>部位</th><th>效果</th><th>寿命</th><th>加入</th></tr></thead>
          <tbody>${rows.map(featureRow).join("")}</tbody>
        </table>
      </section>`);
    });

    const top = box.scrollTop;
    box.innerHTML = sections.join("") || '<div class="empty">没有匹配的特性，换个关键词试试</div>';
    box.scrollTop = top;

    const count = $("feature-count");
    if (count) {
      count.textContent = query
        ? `匹配 ${shown} 项`
        : `${data.counts.features} 项特性 · ${data.counts.groups} 个分类`;
    }
  }

  function updateTarget() {
    const hint = $("feature-target");
    const goto = $("btn-feature-goto");
    const condition = data.conditions[view.target];
    if (hint) {
      hint.innerHTML = condition
        ? `加入目标：<strong>${esc(condition.caseName || condition.name || "未命名组合")}</strong>`
        : "加入目标：未选择（先展开某个组合的「详情」）";
    }
    if (goto) goto.classList.toggle("hidden", !condition);
    renderFeatureTable();
  }

  function toggleFeaturePanel(force) {
    const layout = $("feature-layout");
    const button = $("btn-feature-toggle");
    if (!layout) return;
    const show = force === undefined ? layout.classList.contains("hidden") : Boolean(force);
    layout.classList.toggle("hidden", !show);
    if (button) button.textContent = show ? "收起" : "展开";
    if (show) renderFeatureTable();
  }

  function updateCatalog() {
    const list = $("catalog");
    if (!list) return;
    list.innerHTML = (data.catalog || [])
      .map((name) => `<option value="${esc(name)}"></option>`)
      .join("");
  }

  /* ------------------------------------------------------------ 保存 / 同步 */

  async function save() {
    const button = $("btn-alert-save");
    if (button) button.disabled = true;
    try {
      const result = await request(ENDPOINTS.save, { conditions: data.conditions });
      if (result && result.ok) {
        data.conditions = (result.conditions || data.conditions).map(normalizeCondition);
        renderAlerts();
        renderFeatureTable();
        toast("ok", ["保存成功"].concat(result.messages || []));
      } else {
        toast("err", ["保存失败"].concat((result && result.errors) || ["未知错误"]));
      }
    } catch (error) {
      const payload = error.payload || {};
      toast("err", ["保存失败：" + error.message].concat(payload.errors || []));
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function syncSelection() {
    if (!ENDPOINTS.sync) return;
    try {
      const names = data.conditions.filter((item) => item.defaultChecked).map((item) => item.caseName);
      const result = await request(ENDPOINTS.sync, { caseNames: names });
      toast(result.ok ? "ok" : "err", [
        result.message || "已同步实例勾选，重启 MaaGC 后生效",
      ]);
    } catch (error) {
      toast("err", ["同步失败：" + error.message]);
    }
  }

  async function load(notify) {
    try {
      const result = await request(ENDPOINTS.load);
      data.conditions = (result.conditions || []).map(normalizeCondition);
      data.catalog = result.catalog || [];
      data.groups = result.featureGroups || [];
      data.featureRaces = result.featureRaces || {};
      const counts = result.featureCounts || {};
      data.counts = {
        groups: counts.groups || data.groups.length,
        features: counts.features || data.catalog.length,
      };
      data.raceMeta = {};
      data.groups.forEach((group) => {
        data.raceMeta[group.label] = { kind: group.kind, id: group.id };
      });
      data.loaded = true;
      if (view.target >= data.conditions.length) view.target = -1;
      updateCatalog();
      renderRaceNav();
      renderAlerts();
      renderFeatureTable();
      updateTarget();
      if (notify) {
        toast("ok", [
          `已重新读取：${data.conditions.length} 个组合，特性表 ${data.counts.features} 项 / ${data.counts.groups} 个分类`,
        ]);
      }
    } catch (error) {
      toast("err", ["读取提醒配置失败：" + error.message]);
    }
  }

  /* ------------------------------------------------------------ 事件绑定 */

  function onListClick(event) {
    const button = event.target.closest("button[data-act]");
    if (button) {
      const index = Number(button.dataset.i);
      const action = button.dataset.act;
      if (action === "toggle") {
        setOpen(index, !view.open.has(index));
      } else if (action === "remove") {
        removeCombo(index);
      } else if (action === "rm-feature") {
        removeFeature(index, Number(button.dataset.fi));
      } else if (action === "add-feature") {
        const input = document.querySelector(`input[data-role="feature-input"][data-i="${index}"]`);
        addFeature(index, input ? input.value : "");
        if (input) input.value = "";
      } else if (action === "pick-feature") {
        pickFeature(index);
      }
      return;
    }
    const head = event.target.closest(".combo-head");
    if (head) {
      const index = Number(head.dataset.i);
      setOpen(index, !view.open.has(index));
    }
  }

  function init() {
    if (!$("alert-list") || !$("feature-scroll")) return;

    const bind = (id, handler) => {
      const element = $(id);
      if (element) element.addEventListener("click", handler);
    };

    bind("btn-alert-reload", () => load(true));
    bind("btn-alert-add", addCombo);
    bind("btn-alert-expand", toggleExpandAll);
    bind("btn-alert-save", save);
    bind("btn-alert-sync", syncSelection);
    bind("btn-feature-toggle", () => toggleFeaturePanel());
    bind("btn-feature-goto", () => {
      if (view.target >= 0) focusCombo(view.target);
    });

    if (!ENDPOINTS.sync) {
      const syncButton = $("btn-alert-sync");
      if (syncButton) syncButton.classList.add("hidden");
    }

    const search = $("alert-search");
    if (search) {
      search.addEventListener("input", () => {
        view.search = search.value;
        applyComboFilter();
      });
    }

    const featureSearch = $("feature-search");
    if (featureSearch) {
      featureSearch.addEventListener(
        "input",
        debounce(() => {
          view.featureSearch = featureSearch.value;
          renderFeatureTable();
        }, 120)
      );
      featureSearch.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          featureSearch.value = "";
          view.featureSearch = "";
          renderFeatureTable();
        }
      });
    }

    const raceNav = $("race-nav");
    if (raceNav) {
      raceNav.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-race]");
        if (!button) return;
        view.race = button.dataset.race || "";
        renderRaceNav();
        renderFeatureTable();
      });
    }

    const featureScroll = $("feature-scroll");
    if (featureScroll) {
      featureScroll.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-feature]");
        if (button) toggleFeature(button.dataset.feature);
      });
    }

    const list = $("alert-list");
    if (list) {
      list.addEventListener("input", updateField);
      list.addEventListener("change", updateField);
      list.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.target.dataset.role === "feature-input") {
          event.preventDefault();
          const index = Number(event.target.dataset.i);
          addFeature(index, event.target.value);
          event.target.value = "";
        }
      });
      list.addEventListener("click", onListClick);
    }

    load(false);
  }

  window.AlertPanel = {
    load: () => load(false),
    reload: () => load(true),
    isLoaded: () => data.loaded,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
