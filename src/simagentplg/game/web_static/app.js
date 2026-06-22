const canvas = document.getElementById("worldCanvas");
const ctx = canvas.getContext("2d");
const tileTip = document.getElementById("tileTip");
const worldMeta = document.getElementById("worldMeta");
const pauseBanner = document.getElementById("pauseBanner");
const factionSelect = document.getElementById("factionSelect");
const resourceSelect = document.getElementById("resourceSelect");
const weatherSelect = document.getElementById("weatherSelect");
const weatherDuration = document.getElementById("weatherDuration");
const resourceAmount = document.getElementById("resourceAmount");
const coordX = document.getElementById("coordX");
const coordY = document.getElementById("coordY");
const factionsEl = document.getElementById("factions");
const petitionsEl = document.getElementById("petitions");
const godChatFactionSelect = document.getElementById("godChatFactionSelect");
const godChatMessages = document.getElementById("godChatMessages");
const godChatInput = document.getElementById("godChatInput");
const godChatButton = document.getElementById("godChatButton");
const eventsEl = document.getElementById("events");

let state = null;
let selectedTile = null;
let requestBusy = false;
let tileSize = 24;
let offsetX = 0;
let offsetY = 0;

const terrainColors = {
  plain: "#4d6849",
  forest: "#2f6844",
  hill: "#756a48",
  water: "#2b5f80",
  mountain: "#696f72",
};

const factionColors = {
  human: "#d8ac55",
  elf: "#76b87b",
  orc: "#c96e5a",
};

const factionNames = {
  human: "人类",
  elf: "精灵",
  orc: "兽人",
};

const leaderNames = {
  "High Steward": "最高执政官",
  "Moon Speaker": "月语者",
  "Iron Chieftain": "铁血酋长",
};

const resourceNames = {
  food: "食物",
  wood: "木材",
  stone: "石料",
};

const weatherNames = {
  clear: "晴朗",
  rain: "降雨",
  drought: "干旱",
  storm: "风暴",
};

const terrainNames = {
  plain: "平原",
  forest: "森林",
  hill: "丘陵",
  water: "水域",
  mountain: "山地",
};

const professionNames = {
  farmer: "农民",
  lumberjack: "伐木工",
  miner: "矿工",
  builder: "建筑工",
  idle: "闲置",
};

const relationNames = {
  neutral: "中立",
  allied: "同盟",
  non_aggression: "互不侵犯",
  trade: "贸易",
  tribute: "纳贡",
  war: "战争",
};

const petitionTypeNames = {
  resources: "资源",
  weather: "天气",
  protection: "庇护",
  territory: "领土",
};

const urgencyNames = {
  low: "低",
  medium: "中",
  high: "高",
};

const eventKindNames = {
  world: "世界",
  tick: "推进",
  god: "神迹",
  rule_reject: "规则拒绝",
  resource: "资源",
  discovery: "发现",
  territory: "领土",
  military: "军事",
  battle: "战斗",
  elimination: "淘汰",
  diplomacy: "外交",
  petition: "祈求",
  god_chat: "私聊",
  decree: "法令",
  leader: "首领",
  population: "人口",
  build: "建造",
  weather: "天气",
  pause: "暂停",
  resume: "恢复",
};

const actionNames = {
  spend: "消耗",
  trade: "贸易",
  tribute: "纳贡",
};

const proposalNames = {
  alliance: "同盟",
  trade: "贸易",
  non_aggression: "互不侵犯",
  tribute: "纳贡",
  peace: "和平",
  war: "开战",
};

async function main() {
  wireControls();
  await refreshState();
}

function wireControls() {
  document.getElementById("tickOne").addEventListener("click", () => tick(1));
  document.getElementById("tickFive").addEventListener("click", () => tick(5));
  document.getElementById("giveButton").addEventListener("click", giveResource);
  document.getElementById("weatherButton").addEventListener("click", setWeather);
  godChatButton.addEventListener("click", sendGodChat);
  godChatFactionSelect.addEventListener("change", renderGodChat);

  canvas.addEventListener("mousemove", onCanvasMove);
  canvas.addEventListener("mouseleave", () => {
    tileTip.hidden = true;
  });
  canvas.addEventListener("click", onCanvasClick);
  window.addEventListener("resize", () => drawMap());
}

async function refreshState() {
  const response = await fetch("/api/state");
  state = await response.json();
  hydrateControls();
  render();
}

async function tick(count) {
  await mutate("/api/tick", { count }, "首领正在思考...");
}

async function giveResource() {
  await mutate("/api/god/give", {
    faction_id: factionSelect.value,
    resource: resourceSelect.value,
    amount: Number(resourceAmount.value),
  });
}

async function setWeather() {
  await mutate("/api/god/weather", {
    x: Number(coordX.value),
    y: Number(coordY.value),
    weather: weatherSelect.value,
    duration: Number(weatherDuration.value),
  });
}

async function answerPetition(petitionId, approve) {
  await mutate("/api/god/answer", {
    petition_id: petitionId,
    approve,
  });
}

async function sendGodChat() {
  const message = godChatInput.value.trim();
  if (!message) return;
  await mutate(
    "/api/god/chat",
    {
      faction_id: godChatFactionSelect.value,
      message,
    },
    "首领正在回应...",
  );
  godChatInput.value = "";
}

async function mutate(path, body, busyLabel = "处理中...") {
  if (requestBusy) return;
  requestBusy = true;
  setButtonsDisabled(true);
  worldMeta.textContent = `${busyLabel} 第 ${state ? state.tick : 0} 刻`;
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      showPause(`错误：${payload.error || response.statusText}`);
      return;
    }
    state = payload;
    hydrateControls();
    render();
  } finally {
    requestBusy = false;
    setButtonsDisabled(false);
  }
}

function setButtonsDisabled(disabled) {
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
  });
}

function hydrateControls() {
  const factionIds = state.factions.map((faction) => faction.faction_id);
  syncOptions(factionSelect, factionIds, factionNames);
  syncOptions(godChatFactionSelect, factionIds, factionNames);
  syncOptions(resourceSelect, state.resources, resourceNames);
  syncOptions(weatherSelect, state.weather_types, weatherNames);
}

function syncOptions(select, values, labels = {}) {
  const current = select.value;
  select.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labels[value] || value;
    select.appendChild(option);
  });
  if (values.includes(current)) {
    select.value = current;
  }
}

function render() {
  worldMeta.textContent = `第 ${state.tick} 刻 · 种子 ${state.seed}`;
  if (state.paused) {
    showPause(`已暂停：${state.pause_reason}`);
  } else {
    pauseBanner.hidden = true;
  }
  drawMap();
  renderFactions();
  renderPetitions();
  renderGodChat();
  renderEvents();
}

function showPause(text) {
  pauseBanner.textContent = text;
  pauseBanner.hidden = false;
}

function drawMap() {
  if (!state) return;
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * scale));
  canvas.height = Math.max(1, Math.floor(rect.height * scale));
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  const availableWidth = rect.width - 24;
  const availableHeight = rect.height - 24;
  tileSize = Math.floor(
    Math.max(8, Math.min(availableWidth / state.width, availableHeight / state.height)),
  );
  offsetX = Math.floor((rect.width - tileSize * state.width) / 2);
  offsetY = Math.floor((rect.height - tileSize * state.height) / 2);

  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = "#0e1114";
  ctx.fillRect(0, 0, rect.width, rect.height);

  for (const tile of state.tiles) {
    const x = offsetX + tile.x * tileSize;
    const y = offsetY + tile.y * tileSize;
    ctx.fillStyle = tile.owner
      ? factionColors[tile.owner] || "#aaa"
      : terrainColors[tile.terrain] || "#555";
    ctx.fillRect(x, y, tileSize, tileSize);

    if (tile.weather === "storm") {
      ctx.fillStyle = "rgba(30, 35, 42, 0.72)";
      ctx.fillRect(x, y, tileSize, tileSize);
      ctx.strokeStyle = "#f0d36a";
      ctx.beginPath();
      ctx.moveTo(x + tileSize * 0.35, y + tileSize * 0.15);
      ctx.lineTo(x + tileSize * 0.55, y + tileSize * 0.45);
      ctx.lineTo(x + tileSize * 0.42, y + tileSize * 0.45);
      ctx.lineTo(x + tileSize * 0.62, y + tileSize * 0.85);
      ctx.stroke();
    } else if (tile.weather === "drought") {
      ctx.fillStyle = "rgba(204, 144, 72, 0.35)";
      ctx.fillRect(x, y, tileSize, tileSize);
    } else if (tile.weather === "rain") {
      ctx.fillStyle = "rgba(82, 154, 204, 0.24)";
      ctx.fillRect(x, y, tileSize, tileSize);
    }

    if (tile.weather !== "clear") {
      ctx.fillStyle = "#f8fbff";
      ctx.font = `${Math.max(9, Math.floor(tileSize * 0.42))}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const label = weatherShortLabel(tile.weather);
      const duration = tile.weather_duration > 0 ? tile.weather_duration : "";
      ctx.fillText(`${label}${duration}`, x + tileSize / 2, y + tileSize / 2);
    }

    if (tile.protected) {
      ctx.strokeStyle = "#f1f2b8";
      ctx.lineWidth = 2;
      ctx.strokeRect(x + 2, y + 2, tileSize - 4, tileSize - 4);
    }

    if (tile.home_of) {
      drawHomeStar(x, y, tileSize);
    }

    if (selectedTile && selectedTile.x === tile.x && selectedTile.y === tile.y) {
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.strokeRect(x + 1, y + 1, tileSize - 2, tileSize - 2);
    }

    ctx.strokeStyle = "rgba(0, 0, 0, 0.22)";
    ctx.lineWidth = 1;
    ctx.strokeRect(x, y, tileSize, tileSize);
  }
}

function drawHomeStar(x, y, size) {
  const cx = x + size * 0.23;
  const cy = y + size * 0.23;
  const outer = Math.max(4, size * 0.16);
  const inner = outer * 0.48;
  ctx.save();
  ctx.beginPath();
  for (let i = 0; i < 10; i += 1) {
    const radius = i % 2 === 0 ? outer : inner;
    const angle = -Math.PI / 2 + (i * Math.PI) / 5;
    const px = cx + Math.cos(angle) * radius;
    const py = cy + Math.sin(angle) * radius;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fillStyle = "#ffe27a";
  ctx.strokeStyle = "#1b1b1b";
  ctx.lineWidth = 1.5;
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function renderFactions() {
  factionsEl.innerHTML = "";
  state.factions.forEach((faction) => {
    const row = document.createElement("div");
    row.className = "faction-row";
    row.innerHTML = `
      <div class="faction-title">
        <span>${displayFaction(faction.faction_id)}</span>
        <span class="tag ${faction.faction_id}">${faction.eliminated ? "已淘汰" : "阵营"}</span>
      </div>
      <div class="metric-line">首领：${displayLeader(faction.leader_name)}</div>
      <div class="metric-line">出生地 ${formatHomeTile(faction.home_tile)}${faction.eliminated ? " · 已淘汰" : ""}</div>
      <div class="metric-line">人口 ${faction.population}/${faction.population_capacity} · 士兵 ${faction.soldiers} · 领土 ${faction.territory_count}</div>
      <div class="metric-line">房屋 ${faction.houses} · 职业 ${formatJobs(faction.jobs)}</div>
      <div class="metric-line">食物 ${faction.resources.food} · 木材 ${faction.resources.wood} · 石料 ${faction.resources.stone}</div>
      <div class="metric-line">已发现 ${formatKnownFactions(faction.known_factions)}</div>
      <div class="metric-line">外交 ${formatDiplomacy(faction.diplomacy)}</div>
      <div class="metric-line">上次计划 ${formatLastPlan(faction.last_plan_snapshot)}</div>
    `;
    factionsEl.appendChild(row);
  });
}

function renderPetitions() {
  petitionsEl.innerHTML = "";
  if (state.petitions.length === 0) {
    petitionsEl.innerHTML = `<div class="metric-line">暂无待处理祈求。</div>`;
    return;
  }
  state.petitions.forEach((petition) => {
    const row = document.createElement("div");
    row.className = "petition-row";
    row.innerHTML = `
      <strong>#${petition.petition_id} ${displayFaction(petition.faction_id)}</strong>
      <div>${displayPetitionType(petition.kind)} · 紧急度 ${displayUrgency(petition.urgency)}</div>
      <div>${petition.reason}</div>
      <div class="petition-actions">
        <button type="button" data-action="approve">批准</button>
        <button type="button" data-action="reject">拒绝</button>
      </div>
    `;
    row.querySelector('[data-action="approve"]').addEventListener("click", () => {
      answerPetition(petition.petition_id, true);
    });
    row.querySelector('[data-action="reject"]').addEventListener("click", () => {
      answerPetition(petition.petition_id, false);
    });
    petitionsEl.appendChild(row);
  });
}

function renderGodChat() {
  const factionId = godChatFactionSelect.value || (state.factions[0] || {}).faction_id;
  const messages = (state.god_chats || []).filter(
    (message) => message.faction_id === factionId,
  );
  godChatMessages.innerHTML = "";
  if (!messages.length) {
    godChatMessages.innerHTML = `<div class="metric-line">暂无私聊。神谕只会影响首领意图，不会自动兑现资源。</div>`;
    return;
  }
  messages.forEach((message) => {
    const row = document.createElement("div");
    row.className = `god-chat-message ${message.speaker}`;
    row.innerHTML = `
      <div class="god-chat-meta">第 ${message.tick} 刻 · ${displayChatSpeaker(message.speaker)}</div>
      <div>${escapeHtml(message.content)}</div>
    `;
    godChatMessages.appendChild(row);
  });
  godChatMessages.scrollTop = godChatMessages.scrollHeight;
}

function renderEvents() {
  eventsEl.innerHTML = "";
  state.events.slice().reverse().forEach((event) => {
    const row = document.createElement("div");
    row.className = "event-row";
    row.textContent = `[第 ${event.tick} 刻] ${displayEventKind(event.kind)}：${formatEvent(event)}`;
    eventsEl.appendChild(row);
  });
}

function onCanvasMove(event) {
  const tile = tileFromEvent(event);
  if (!tile) {
    tileTip.hidden = true;
    return;
  }
  tileTip.innerHTML = tileDetails(tile);
  tileTip.style.left = `${event.clientX + 12}px`;
  tileTip.style.top = `${event.clientY + 12}px`;
  tileTip.hidden = false;
}

function onCanvasClick(event) {
  const tile = tileFromEvent(event);
  if (!tile) return;
  selectedTile = tile;
  coordX.value = tile.x;
  coordY.value = tile.y;
  drawMap();
}

function tileFromEvent(event) {
  if (!state) return null;
  const rect = canvas.getBoundingClientRect();
  const localX = event.clientX - rect.left - offsetX;
  const localY = event.clientY - rect.top - offsetY;
  const x = Math.floor(localX / tileSize);
  const y = Math.floor(localY / tileSize);
  if (x < 0 || y < 0 || x >= state.width || y >= state.height) return null;
  return state.tiles[y * state.width + x];
}

function tileDetails(tile) {
  const pop = Object.entries(tile.population)
    .map(([faction, amount]) => `${displayFaction(faction)}:${amount}`)
    .join(" ") || "无";
  const soldiers = Object.entries(tile.soldiers)
    .map(([faction, amount]) => `${displayFaction(faction)}:${amount}`)
    .join(" ") || "无";
  const jobs = Object.entries(tile.professions || {})
    .map(([faction, entries]) => `${displayFaction(faction)} ${formatJobs(entries)}`)
    .join("<br>") || "无";
  return `
    <strong>(${tile.x}, ${tile.y})</strong><br>
    地形：${displayTerrain(tile.terrain)}<br>
    出生地：${tile.home_of ? displayFaction(tile.home_of) : "无"}<br>
    天气：${displayWeather(tile.weather)}${tile.weather_duration ? `（剩余 ${tile.weather_duration} 刻）` : ""}<br>
    归属：${tile.owner ? displayFaction(tile.owner) : "无"}<br>
    房屋：${tile.houses || 0} · 容量：${tile.capacity || 0}<br>
    人口：${pop}<br>
    士兵：${soldiers}<br>
    职业：<br>${jobs}
  `;
}

function formatHomeTile(homeTile) {
  if (!homeTile) return "未知";
  return `(${homeTile.x}, ${homeTile.y})`;
}

function formatDiplomacy(diplomacy) {
  const text = Object.entries(diplomacy)
    .map(([faction, relation]) => `${displayFaction(faction)}:${displayRelation(relation)}`)
    .join(" ");
  return text || "暂无";
}

function formatJobs(jobs = {}) {
  const text = Object.entries(professionNames)
    .map(([job, label]) => `${label}${Number(jobs[job] || 0)}`)
    .join(" ");
  return text || "无";
}

function formatKnownFactions(factions = []) {
  if (!factions.length) return "无";
  return factions.map(displayFaction).join("、");
}

function formatLastPlan(snapshot = {}) {
  if (!snapshot || snapshot.tick === undefined) return "暂无";
  const resources = snapshot.resources || {};
  const after = snapshot.after_execution || null;
  const summary = snapshot.strategy_summary || "无摘要";
  const beforeText = `提交时 食物 ${resources.food ?? 0} 木材 ${resources.wood ?? 0} 石料 ${resources.stone ?? 0}`;
  if (!after) {
    return `第 ${snapshot.tick} 刻首领原话：${summary}（${beforeText}，尚未执行）`;
  }
  const afterResources = after.resources || {};
  return [
    `第 ${snapshot.tick} 刻首领原话：${summary}`,
    `提交时：食物 ${resources.food ?? 0} 木材 ${resources.wood ?? 0} 石料 ${resources.stone ?? 0}`,
    `执行后：人口 ${after.population ?? 0}/${after.population_capacity ?? 0} · 士兵 ${after.soldiers ?? 0} · 领土 ${after.territory_count ?? 0} · 房屋 ${after.houses ?? 0}`,
    `执行后资源：食物 ${afterResources.food ?? 0} 木材 ${afterResources.wood ?? 0} 石料 ${afterResources.stone ?? 0}`,
    `执行后职业：${formatJobs(after.jobs || {})}`,
  ].join(" ｜ ");
}

function formatEvent(event) {
  const message = event.message || "";
  let match = message.match(/^World created with seed (\d+), (\d+)x(\d+) tiles$/);
  if (match) return `世界以种子 ${match[1]} 创建，地图 ${match[2]} x ${match[3]} 格`;

  match = message.match(/^Tick (\d+) completed$/);
  if (match) return `第 ${match[1]} 刻结算完成`;

  match = message.match(/^God granted (\d+) (\w+) to (\w+)$/);
  if (match) return `上帝赐予 ${displayFaction(match[3])} ${match[1]} ${displayResource(match[2])}`;

  match = message.match(/^God changed weather at \((\d+), (\d+)\) to (\w+)(?: for (\d+) ticks)?$/);
  if (match) return `上帝将（${match[1]}, ${match[2]}）的天气改为${displayWeather(match[3])}${match[4] ? `，持续 ${match[4]} 刻` : ""}`;

  match = message.match(/^God assigned tile \((\d+), (\d+)\) from (.+) to (\w+)(?: with (\d+) moved people)?$/);
  if (match) {
    return `上帝将（${match[1]}, ${match[2]}）从${displayOwner(match[3])}划给${displayFaction(match[4])}${match[5] ? `，迁入 ${match[5]} 人` : ""}`;
  }

  match = message.match(/^God marked tile \((\d+), (\d+)\) as (protected|unprotected)$/);
  if (match) return `上帝将（${match[1]}, ${match[2]}）标记为${match[3] === "protected" ? "庇护" : "未庇护"}`;

  match = message.match(/^God sent (\w+) to \((\d+), (\d+)\)$/);
  if (match) return `上帝向（${match[2]}, ${match[3]}）降下${displayDisaster(match[1])}`;

  match = message.match(/^(god|leader) privately messaged (\w+): (.*)$/);
  if (match) return `${displayChatSpeaker(match[1])} 私聊 ${displayFaction(match[2])}：${match[3]}`;

  match = message.match(/^God rejected petition (\d+) from (\w+)$/);
  if (match) return `上帝拒绝了 ${displayFaction(match[2])} 的 #${match[1]} 祈求`;

  match = message.match(/^God approved petition (\d+) from (\w+)$/);
  if (match) return `上帝批准了 ${displayFaction(match[2])} 的 #${match[1]} 祈求`;

  match = message.match(/^(\w+) used (\d+) (\w+) for (\w+)$/);
  if (match) return `${displayFaction(match[1])} 为${displayAction(match[4])}使用 ${match[2]} ${displayResource(match[3])}`;

  match = message.match(/^(\w+) produced food=(\d+) wood=(\d+) stone=(\d+)$/);
  if (match) return `${displayFaction(match[1])} 产出：食物 ${match[2]}、木材 ${match[3]}、石料 ${match[4]}`;

  match = message.match(/^(\w+) assigned (\d+) (\w+) at \((\d+), (\d+)\)$/);
  if (match) return `${displayFaction(match[1])} 在（${match[4]}, ${match[5]}）安排 ${match[2]} 名${displayProfession(match[3])}`;

  match = message.match(/^(\w+) built (\d+) houses at \((\d+), (\d+)\)$/);
  if (match) return `${displayFaction(match[1])} 在（${match[3]}, ${match[4]}）建造 ${match[2]} 间房屋`;

  match = message.match(/^(\w+) discovered (\w+)$/);
  if (match) return `${displayFaction(match[1])} 发现了 ${displayFaction(match[2])}`;

  match = message.match(/^(\w+) abandoned tile (.+)$/);
  if (match) return `${displayFaction(match[1])} 放弃了 ${formatTarget(match[2])}`;

  match = message.match(/^(\w+) lost tile \((\d+), (\d+)\) because no people remained$/);
  if (match) return `${displayFaction(match[1])} 因无人居住失去（${match[2]}, ${match[3]}）`;

  match = message.match(/^(\w+) failed to settle tile (.+) because no (?:idle people|movable people|movable civilians) were available$/);
  if (match) return `${displayFaction(match[1])} 无法迁入 ${formatTarget(match[2])}：没有可迁平民`;

  match = message.match(/^(\w+) settled tile (.+) from (.+) with (\d+) (\w+)(?: and claimed it)?$/);
  if (match) return `${displayFaction(match[1])} 从 ${formatTarget(match[3])} 迁入 ${formatTarget(match[2])}：${match[4]} 名${displayProfession(match[5])}`;

  match = message.match(/^(\w+) settled tile (.+) with (\d+) people$/);
  if (match) return `${displayFaction(match[1])} 迁入 ${formatTarget(match[2])}，人口 ${match[3]}`;

  match = message.match(/^(\w+) trained (\d+) soldiers at (.+)$/);
  if (match) return `${displayFaction(match[1])} 在 ${formatTarget(match[3])} 训练了 ${match[2]} 名士兵`;

  match = message.match(/^(\w+) captured (.+) from (\w+)(?: with (\d+) (settlers|soldiers) and took (.*))?$/);
  if (match) return `${displayFaction(match[1])} 从 ${displayFaction(match[3])} 手中占领 ${formatTarget(match[2])}${match[4] ? `，${match[5] === "soldiers" ? "驻守士兵" : "迁入"} ${match[4]}，缴获 ${formatLoot(match[6])}` : ""}`;

  match = message.match(/^(\w+) was eliminated when (\w+) captured home tile (.+); resources transferred food=(\d+) wood=(\d+) stone=(\d+)$/);
  if (match) return `${displayFaction(match[2])} 占领 ${displayFaction(match[1])} 出生地 ${formatTarget(match[3])}，${displayFaction(match[1])} 被淘汰，资源转移：食物 ${match[4]}、木材 ${match[5]}、石料 ${match[6]}`;

  match = message.match(/^(\w+) moved (\d+) soldiers from (.+) to (.+)$/);
  if (match) return `${displayFaction(match[1])} 从 ${formatTarget(match[3])} 调动 ${match[2]} 名士兵到 ${formatTarget(match[4])}`;

  match = message.match(/^(\w+) raided (.+) from (\w+) and took (.*)$/);
  if (match) return `${displayFaction(match[1])} 突袭 ${displayFaction(match[3])} 的 ${formatTarget(match[2])}，缴获 ${formatLoot(match[4])}`;

  match = message.match(/^(\w+) attacked (\w+) at (.+) and failed$/);
  if (match) return `${displayFaction(match[1])} 进攻 ${displayFaction(match[2])} 的 ${formatTarget(match[3])} 失败`;

  match = message.match(/^(\w+) proposed (\w+) to (\w+)$/);
  if (match) return `${displayFaction(match[1])} 向 ${displayFaction(match[3])} 提议${displayProposal(match[2])}`;

  match = message.match(/^(\w+) set relation with (\w+) to (\w+)$/);
  if (match) return `${displayFaction(match[1])} 与 ${displayFaction(match[2])} 的关系变为${displayRelation(match[3])}`;

  match = message.match(/^(\w+) decreed: (.*)$/);
  if (match) return `${displayFaction(match[1])} 颁布法令：${match[2]}`;

  match = message.match(/^(\w+) submitted plan: (.*)$/);
  if (match) return `${displayFaction(match[1])} 提交计划：${match[2]}`;

  match = message.match(/^(\w+) petitioned for (\w+): (.*)$/);
  if (match) return `${displayFaction(match[1])} 祈求${displayPetitionType(match[2])}：${match[3]}`;

  match = message.match(/^(\w+) updated petition for (\w+): (.*)$/);
  if (match) return `${displayFaction(match[1])} 更新${displayPetitionType(match[2])}祈求：${match[3]}`;

  match = message.match(/^(\w+) submitted illegal plan on attempt (\d+): (.*)$/);
  if (match) return `${displayFaction(match[1])} 第 ${match[2]} 次提交的计划非法：${match[3]}`;

  match = message.match(/^(\w+) population grew by (\d+)$/);
  if (match) return `${displayFaction(match[1])} 人口增长 ${match[2]}`;

  match = message.match(/^(\w+) lost (\d+) people to starvation$/);
  if (match) return `${displayFaction(match[1])} 因饥荒损失 ${match[2]} 人`;

  match = message.match(/^storm at \((\d+), (\d+)\) cost (\w+) (\d+) people and (\d+) soldiers$/);
  if (match) return `风暴袭击（${match[1]}, ${match[2]}），${displayFaction(match[3])} 损失 ${match[4]} 人、${match[5]} 名士兵`;

  match = message.match(/^drought at \((\d+), (\d+)\) cost (\w+) (\d+) people$/);
  if (match) return `干旱影响（${match[1]}, ${match[2]}），${displayFaction(match[3])} 损失 ${match[4]} 人`;

  if (message === "Simulation resumed") return "模拟已继续";
  return message;
}

function displayEventKind(value) {
  return eventKindNames[value] || value;
}

function displayFaction(value) {
  return factionNames[value] || value;
}

function displayLeader(value) {
  return leaderNames[value] || value;
}

function displayResource(value) {
  return resourceNames[value] || value;
}

function displayWeather(value) {
  return weatherNames[value] || value;
}

function displayTerrain(value) {
  return terrainNames[value] || value;
}

function displayRelation(value) {
  return relationNames[value] || value;
}

function displayPetitionType(value) {
  return petitionTypeNames[value] || value;
}

function displayUrgency(value) {
  return urgencyNames[value] || value;
}

function displayProfession(value) {
  return professionNames[value] || value;
}

function displayOwner(value) {
  if (value === "None" || value === "null" || value === "undefined") return "无";
  return displayFaction(value);
}

function displayAction(value) {
  return actionNames[value] || value;
}

function displayProposal(value) {
  return proposalNames[value] || value;
}

function displayDisaster(value) {
  if (value === "plague") return "瘟疫";
  return displayWeather(value);
}

function displayChatSpeaker(value) {
  if (value === "god") return "上帝";
  if (value === "leader") return "首领";
  return value;
}

function weatherShortLabel(value) {
  if (value === "rain") return "雨";
  if (value === "drought") return "旱";
  if (value === "storm") return "暴";
  return "";
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatLoot(value = "") {
  return String(value)
    .split(",")
    .filter(Boolean)
    .map((item) => {
      const [resource, amount] = item.split("=");
      return `${displayResource(resource)} ${amount}`;
    })
    .join("、") || "无";
}

function formatTarget(value) {
  return String(value).replace("(", "（").replace(")", "）");
}

main().catch((error) => {
  showPause(`启动失败：${error}`);
});
