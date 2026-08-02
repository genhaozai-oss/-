const storedSessionId = window.localStorage.getItem("smarthome_session_id");
const voiceReplyStorageKey = "smarthome_voice_reply_enabled";
const ttsVoiceStorageKey = "smarthome_tts_voice";
const sessionId =
  storedSessionId ||
  (window.crypto?.randomUUID?.() ??
    `session-${Date.now()}-${Math.random().toString(16).slice(2)}`);
window.localStorage.setItem("smarthome_session_id", sessionId);

const state = {
  selectedDeviceId: null,
  contextDeviceId: null,
  devices: [],
  autoFlow: null,
  proactive: null,
  weatherUpdatedAt: 0,
  voiceReplyEnabled:
    window.localStorage.getItem(voiceReplyStorageKey) === "1",
  ttsVoice: window.localStorage.getItem(ttsVoiceStorageKey) || "Serena",
};

const icons = {
  fan: "✣",
  humidifier: "◌",
  dehumidifier: "◍",
  light: "☼",
};
let activeSpeechUtterance = null;
let activeSpeechAudio = null;
let speechRequestId = 0;
let activeSpeechRequestId = null;
let systemSpeechWatchdog = null;
let voiceInputMode = "idle";
let notificationClaimInFlight = false;
const displayedNotificationIds = new Set();
let refreshStatePromise = null;
let weatherRequestPromise = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `请求失败：${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2200);
}

function updateVoiceReplyButton() {
  const button = document.querySelector("#voiceReplyButton");
  button.textContent = `语音播报：${state.voiceReplyEnabled ? "开" : "关"}`;
  button.setAttribute("aria-pressed", String(state.voiceReplyEnabled));
  button.classList.toggle("active", state.voiceReplyEnabled);
}

function speechSynthesisSupported() {
  return (
    "speechSynthesis" in window &&
    "SpeechSynthesisUtterance" in window
  );
}

function stopSpeaking() {
  if (speechSynthesisSupported()) window.speechSynthesis.cancel();
  activeSpeechUtterance = null;
  if (systemSpeechWatchdog) {
    window.clearTimeout(systemSpeechWatchdog);
    systemSpeechWatchdog = null;
  }
  if (activeSpeechAudio) {
    activeSpeechAudio.pause();
    activeSpeechAudio.removeAttribute("src");
    activeSpeechAudio = null;
  }
}

function voiceCaptureActive() {
  return (
    ["preparing", "listening", "speech"].includes(voiceInputMode) ||
    Boolean(activeVoiceStream)
  );
}

function cleanSpokenText(text) {
  return String(text)
    .replace(/https?:\/\/\S+/g, "链接")
    .replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu, "")
    .replace(/[\u{1F1E6}-\u{1F1FF}\u20E3]/gu, "")
    .replace(/[*_`#>]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 400);
}

function speakWithSystemVoice(spokenText) {
  if (!speechSynthesisSupported()) return false;
  const utterance = new SpeechSynthesisUtterance(spokenText);
  const voices = window.speechSynthesis.getVoices();
  utterance.voice =
    voices.find((voice) => voice.lang.toLowerCase() === "zh-cn") ||
    voices.find((voice) => voice.lang.toLowerCase().startsWith("zh")) ||
    null;
  utterance.lang = "zh-CN";
  utterance.rate = 1.05;
  utterance.pitch = 1.05;
  activeSpeechUtterance = utterance;
  const finish = () => {
    if (activeSpeechUtterance !== utterance) return;
    activeSpeechUtterance = null;
    if (systemSpeechWatchdog) {
      window.clearTimeout(systemSpeechWatchdog);
      systemSpeechWatchdog = null;
    }
  };
  utterance.addEventListener("end", finish);
  utterance.addEventListener("error", finish);
  try {
    window.speechSynthesis.speak(utterance);
  } catch (_error) {
    finish();
    return false;
  }
  systemSpeechWatchdog = window.setTimeout(() => {
    if (activeSpeechUtterance === utterance) {
      window.speechSynthesis.cancel();
      finish();
    }
  }, Math.min(120000, Math.max(10000, spokenText.length * 500)));
  return true;
}

async function speakAssistant(text, { force = false } = {}) {
  if (voiceCaptureActive()) return false;
  if (!state.voiceReplyEnabled && !force) return false;
  const spokenText = cleanSpokenText(text);
  if (!spokenText) return false;

  const requestId = ++speechRequestId;
  stopSpeaking();
  if (state.ttsVoice === "system") return speakWithSystemVoice(spokenText);

  activeSpeechRequestId = requestId;
  let requestedAudio = null;
  try {
    const response = await fetch("/api/voice/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: spokenText, voice: state.ttsVoice }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "云端语音播报失败");
    if (result.fallback_from === "doubao") {
      showToast("豆包暂时不可用，已改用百炼播报");
    }
    if (
      requestId !== speechRequestId ||
      (!state.voiceReplyEnabled && !force)
    ) return false;

    const audio = new Audio(result.audio_url);
    requestedAudio = audio;
    activeSpeechAudio = audio;
    audio.addEventListener("ended", () => {
      if (activeSpeechAudio === audio) activeSpeechAudio = null;
    });
    audio.addEventListener("error", () => {
      if (activeSpeechAudio === audio) activeSpeechAudio = null;
      showToast("云端语音播放失败");
    });
    await audio.play();
    return true;
  } catch (error) {
    if (requestedAudio && activeSpeechAudio === requestedAudio) {
      requestedAudio.pause();
      requestedAudio.removeAttribute("src");
      activeSpeechAudio = null;
    }
    if (requestId === speechRequestId && speechSynthesisSupported()) {
      showToast("云端音色不可用，已改用系统声音");
      return speakWithSystemVoice(spokenText);
    }
    if (requestId === speechRequestId) {
      showToast(error.message || "云端语音播报失败");
    }
    return false;
  } finally {
    if (activeSpeechRequestId === requestId) activeSpeechRequestId = null;
  }
}

async function previewVoice() {
  const button = document.querySelector("#previewVoiceButton");
  button.disabled = true;
  try {
    const played = await speakAssistant(
      "你好呀，我是栖居，很高兴陪你一起照顾这个家。",
      { force: true },
    );
    if (played) showToast("正在试听当前音色");
  } finally {
    button.disabled = false;
  }
}

function updateTtsVoice() {
  const select = document.querySelector("#ttsVoiceSelect");
  state.ttsVoice = select.value;
  window.localStorage.setItem(ttsVoiceStorageKey, state.ttsVoice);
  showToast(`播报音色已切换为${select.selectedOptions[0].textContent}`);
}

async function loadTtsVoices() {
  const status = await api("/api/voice/status");
  const tts = status.tts || {};
  const select = document.querySelector("#ttsVoiceSelect");
  const provider = document.querySelector("#ttsProviderLabel");
  const voices = Array.isArray(tts.voices) ? tts.voices : [];
  const savedVoice = state.ttsVoice;

  select.replaceChildren();
  voices.forEach((voice) => {
    const option = document.createElement("option");
    option.value = voice.id;
    option.textContent = voice.label;
    select.append(option);
  });
  const systemOption = document.createElement("option");
  systemOption.value = "system";
  systemOption.textContent = "系统免费音色";
  select.append(systemOption);

  const availableValues = [...select.options].map((option) => option.value);
  state.ttsVoice = availableValues.includes(savedVoice)
    ? savedVoice
    : tts.voice || availableValues[0] || "system";
  select.value = state.ttsVoice;
  window.localStorage.setItem(ttsVoiceStorageKey, state.ttsVoice);
  provider.textContent = tts.available
    ? `当前：${tts.provider_label}${tts.fallback_available ? " · 百炼备用" : ""}`
    : "当前：系统免费音色";
}

async function toggleVoiceReply() {
  state.voiceReplyEnabled = !state.voiceReplyEnabled;
  window.localStorage.setItem(
    voiceReplyStorageKey,
    state.voiceReplyEnabled ? "1" : "0",
  );
  updateVoiceReplyButton();
  if (state.voiceReplyEnabled) {
    const played = await speakAssistant("语音播报已开启。");
    if (played) showToast("语音播报已开启");
  } else {
    speechRequestId += 1;
    stopSpeaking();
    showToast("语音播报已关闭");
  }
}

let sceneBannerTimer = null;

function showSceneBanner() {
  const banner = document.querySelector("#sceneBanner");
  window.clearTimeout(sceneBannerTimer);
  banner.classList.add("show");
  sceneBannerTimer = window.setTimeout(
    () => banner.classList.remove("show"),
    5000,
  );
}

function cleanAssistantText(text) {
  return String(text)
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/(^|\n)#{1,6}\s+/g, "$1")
    .replace(/`([^`]+)`/g, "$1");
}

function addMessage(text, role) {
  const messages = document.querySelector("#messages");
  const element = document.createElement("div");
  element.className = `message ${role}-message`;
  element.textContent =
    role === "assistant" ? cleanAssistantText(text) : text;
  messages.append(element);
  messages.scrollTop = messages.scrollHeight;
}

function renderDevices(devices) {
  state.devices = devices;
  if (!devices.some((device) => device.id === state.selectedDeviceId)) {
    state.selectedDeviceId = null;
  }
  if (!devices.some((device) => device.id === state.contextDeviceId)) {
    state.contextDeviceId = null;
  }
  const activeDeviceId = state.selectedDeviceId || state.contextDeviceId;
  const selectedDevice = devices.find(
    (device) => device.id === activeDeviceId,
  );
  document.querySelector("#selectedDeviceText").textContent = selectedDevice
    ? `当前设备：${selectedDevice.name}`
    : "当前未选择设备";
  const container = document.querySelector("#deviceList");
  container.replaceChildren();
  for (const device of devices) {
    const element = document.createElement("div");
    element.className = `device${activeDeviceId === device.id ? " selected" : ""}`;
    element.innerHTML = `
      <span class="device-icon">${icons[device.type] || "⌁"}</span>
      <span>
        <strong></strong>
        <small>${device.room || "未分配房间"} · ${device.is_virtual ? "安全模拟" : "实体设备"}</small>
      </span>
      <button class="switch ${device.state === "on" ? "on" : ""}" aria-label="切换设备"></button>
      <div class="device-capabilities"></div>
    `;
    element.querySelector("strong").textContent = device.name;
    element.addEventListener("click", () => {
      state.selectedDeviceId = device.id;
      state.contextDeviceId = null;
      renderDevices(state.devices);
    });
    element.querySelector(".switch").addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        const result = await api(`/api/devices/${device.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            state: device.state === "on" ? "off" : "on",
            session_id: sessionId,
          }),
        });
        state.selectedDeviceId = null;
        state.contextDeviceId = result.context_device_id || device.id;
        await refreshState();
      } catch (error) {
        showToast(error.message);
      }
    });
    const capabilityContainer = element.querySelector(".device-capabilities");
    for (const capability of device.capabilities || []) {
      const control = document.createElement("label");
      control.className = "capability-control";
      control.innerHTML = `
        <span></span>
        <output></output>
        <input type="range">
      `;
      control.querySelector("span").textContent = capability.display_name;
      const output = control.querySelector("output");
      const updateOutput = (value) => {
        output.textContent = `${Number(value).toFixed(
          capability.step < 1 ? 1 : 0,
        )}${capability.unit}`;
      };
      const input = control.querySelector("input");
      input.min = capability.minimum;
      input.max = capability.maximum;
      input.step = capability.step;
      input.value = capability.value;
      input.ariaLabel = `${device.name}${capability.display_name}`;
      updateOutput(input.value);
      input.addEventListener("click", (event) => event.stopPropagation());
      input.addEventListener("input", () => updateOutput(input.value));
      input.addEventListener("change", async () => {
        try {
          const result = await api(
            `/api/devices/${device.id}/capabilities/${capability.capability}`,
            {
              method: "PATCH",
              body: JSON.stringify({
                value: Number(input.value),
                session_id: sessionId,
              }),
            },
          );
          state.selectedDeviceId = null;
          state.contextDeviceId = result.context_device_id || device.id;
          if (result.learning?.message) {
            showToast(result.learning.message);
          } else if (result.learning) {
            showToast(
              `正在学习${capability.display_name}习惯：${result.learning.progress}/${result.learning.required}`,
            );
          } else {
            showToast(`${device.name}${capability.display_name}已更新`);
          }
          await refreshState();
        } catch (error) {
          showToast(error.message);
          await refreshState();
        }
      });
      capabilityContainer.append(control);
    }
    container.append(element);
  }
}

function renderAlarms(alarms) {
  const container = document.querySelector("#alarmList");
  container.replaceChildren();
  if (!alarms.length) {
    container.innerHTML = '<p class="empty-state">还没有闹钟，可以在对话框里说“明天早上七点设置闹钟”。</p>';
    return;
  }
  for (const alarm of alarms) {
    const time = new Date(alarm.scheduled_at);
    const element = document.createElement("div");
    element.className = "alarm";
    element.innerHTML = `
      <span>⏱</span>
      <span>
        <strong>${time.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</strong>
        <small>${time.toLocaleDateString("zh-CN")} · ${alarm.label}</small>
      </span>
      <button aria-label="删除闹钟">×</button>
    `;
    element.querySelector("button").addEventListener("click", async () => {
      await api(`/api/alarms/${alarm.id}`, { method: "DELETE" });
      await refreshState();
    });
    container.append(element);
  }
}

function notificationIcon(kind) {
  if (kind === "alarm") return "铃";
  if (kind === "sensor") return "感";
  if (kind === "weather_warning") return "警";
  return "天";
}

function updateDesktopNotificationButton() {
  const button = document.querySelector("#desktopNotificationButton");
  if (!("Notification" in window)) {
    button.textContent = "浏览器不支持桌面提醒";
    button.disabled = true;
    return;
  }
  const labels = {
    granted: "桌面提醒：已开启",
    denied: "桌面提醒：已拒绝",
    default: "开启桌面提醒",
  };
  button.textContent = labels[window.Notification.permission];
  button.disabled = window.Notification.permission === "denied";
}

async function requestDesktopNotifications() {
  if (!("Notification" in window)) return;
  const permission = await window.Notification.requestPermission();
  updateDesktopNotificationButton();
  showToast(
    permission === "granted"
      ? "桌面提醒已开启"
      : "未获得桌面提醒权限，站内提醒仍然有效",
  );
}

function renderNotifications(notifications, unreadCount, proactive) {
  state.proactive = proactive;
  const container = document.querySelector("#notificationList");
  const badge = document.querySelector("#notificationUnreadBadge");
  const proactiveBadge = document.querySelector("#proactiveBadge");
  const summary = document.querySelector("#proactiveSummary");
  const toggle = document.querySelector("#proactiveToggleButton");

  badge.textContent = `${unreadCount} 条未读`;
  proactiveBadge.textContent = proactive.enabled ? "主动提醒：开启" : "主动提醒：暂停";
  proactiveBadge.classList.toggle("paused", !proactive.enabled);
  toggle.textContent = proactive.enabled ? "暂停主动提醒" : "开启主动提醒";
  summary.textContent = proactive.enabled
    ? proactive.running
      ? `后台正在持续检查${proactive.last_run_at ? `，最近检查：${new Date(proactive.last_run_at).toLocaleString("zh-CN")}` : ""}`
      : "主动提醒已开启，后台守护器当前未运行。"
    : "天气和传感器主动提醒已暂停，闹钟仍会按时检查。";

  container.replaceChildren();
  if (!notifications.length) {
    container.innerHTML = '<p class="empty-state">还没有主动提醒。闹钟到期、传感器长时间未更新或天气有风险时会显示在这里。</p>';
    return;
  }
  for (const notification of notifications) {
    const element = document.createElement("article");
    element.className = `notification-item${notification.read_at ? " read" : ""}`;
    const icon = document.createElement("span");
    icon.className = "notification-icon";
    icon.textContent = notificationIcon(notification.kind);
    const text = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = notification.title;
    const message = document.createElement("p");
    message.textContent = notification.message;
    const time = document.createElement("small");
    time.textContent = new Date(notification.created_at).toLocaleString("zh-CN");
    text.append(title, message, time);
    element.append(icon, text);
    if (!notification.read_at) {
      const read = document.createElement("button");
      read.textContent = "已读";
      read.ariaLabel = `将${notification.title}标记为已读`;
      read.addEventListener("click", async () => {
        await api(`/api/notifications/${notification.id}`, {
          method: "PATCH",
          body: JSON.stringify({ read: true }),
        });
        await refreshState();
      });
      element.append(read);
    }
    container.append(element);
  }
}

async function claimNotification() {
  if (
    document.visibilityState !== "visible" ||
    notificationClaimInFlight ||
    voiceCaptureActive() ||
    activeSpeechRequestId !== null ||
    activeSpeechAudio ||
    activeSpeechUtterance
  ) {
    return;
  }
  notificationClaimInFlight = true;
  try {
    const result = await api("/api/notifications/claim", { method: "POST" });
    const notification = result.notification;
    if (!notification) return;
    if (
      voiceCaptureActive() ||
      activeSpeechRequestId !== null ||
      activeSpeechAudio ||
      activeSpeechUtterance
    ) {
      return;
    }
    if (!displayedNotificationIds.has(notification.id)) {
      displayedNotificationIds.add(notification.id);
      addMessage(notification.message, "assistant");
      showToast(notification.title);
    }
    const played = await speakAssistant(notification.message, {
      force: notification.kind === "alarm",
    });
    if (notification.kind === "alarm" && !played) return;
    if (
      "Notification" in window &&
      window.Notification.permission === "granted"
    ) {
      try {
        new window.Notification(notification.title, {
          body: notification.message,
          tag: notification.dedupe_key,
        });
      } catch (_error) {
        // 页面内提醒已经显示，桌面通知失败不影响确认。
      }
    }
    await api(`/api/notifications/${notification.id}/ack`, {
      method: "POST",
      body: JSON.stringify({ claim_token: notification.claim_token }),
    });
  } finally {
    notificationClaimInFlight = false;
  }
}

function renderAutomations(automations) {
  const container = document.querySelector("#automationList");
  container.replaceChildren();
  if (!automations.length) {
    container.innerHTML =
      '<p class="empty-state">还没有自定义规则，可以对助手说“湿度超过70%就自动打开抽湿器”。</p>';
    return;
  }
  for (const automation of automations) {
    const element = document.createElement("article");
    element.className = `automation-rule${automation.enabled ? "" : " disabled"}`;

    const condition = document.createElement("div");
    condition.className = "automation-condition";
    const status = document.createElement("span");
    status.className = "automation-status";
    status.textContent = automation.enabled ? "自动" : "暂停";
    const text = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = automation.description;
    const detail = document.createElement("small");
    detail.textContent = automation.last_triggered_at
      ? `最近执行：${new Date(automation.last_triggered_at).toLocaleString("zh-CN")}`
      : "尚未触发";
    text.append(title, detail);
    condition.append(status, text);

    const controls = document.createElement("div");
    controls.className = "automation-controls";
    const toggle = document.createElement("button");
    toggle.textContent = automation.enabled ? "暂停" : "启用";
    toggle.ariaLabel = `${toggle.textContent}自动化`;
    toggle.addEventListener("click", async () => {
      try {
        await api(`/api/automations/${automation.id}`, {
          method: "PATCH",
          body: JSON.stringify({ enabled: !Boolean(automation.enabled) }),
        });
        showToast(automation.enabled ? "自动化已暂停" : "自动化已启用");
        await refreshState();
      } catch (error) {
        showToast(error.message);
      }
    });
    const remove = document.createElement("button");
    remove.textContent = "删除";
    remove.ariaLabel = "删除自动化";
    remove.addEventListener("click", async () => {
      try {
        await api(`/api/automations/${automation.id}`, { method: "DELETE" });
        showToast("自动化已删除");
        await refreshState();
      } catch (error) {
        showToast(error.message);
      }
    });
    controls.append(toggle, remove);
    element.append(condition, controls);
    container.append(element);
  }
}

function renderAutoFlow(autoFlow) {
  if (!autoFlow) return;
  state.autoFlow = autoFlow;
  const badge = document.querySelector("#autoFlowBadge");
  const summary = document.querySelector("#autoFlowSummary");
  const toggle = document.querySelector("#autoFlowToggleButton");
  const steps = document.querySelector("#autoFlowSteps");

  badge.textContent = autoFlow.enabled ? "自动托管：开启" : "自动托管：暂停";
  badge.classList.toggle("paused", !autoFlow.enabled);
  summary.textContent = autoFlow.summary;
  toggle.textContent = autoFlow.enabled ? "暂停自动托管" : "开启自动托管";
  toggle.setAttribute(
    "aria-label",
    autoFlow.enabled ? "暂停 AI 自动托管" : "开启 AI 自动托管",
  );

  steps.replaceChildren();
  for (const [index, step] of (autoFlow.steps || []).entries()) {
    const element = document.createElement("article");
    element.className = `auto-flow-step ${step.status || "idle"}`;
    const number = document.createElement("span");
    number.className = "auto-flow-step-number";
    number.textContent = String(index + 1).padStart(2, "0");
    const text = document.createElement("span");
    const label = document.createElement("strong");
    label.textContent = step.label;
    const detail = document.createElement("small");
    detail.textContent = step.detail;
    text.append(label, detail);
    element.append(number, text);
    steps.append(element);
  }
}

function renderScenes(scenes) {
  const container = document.querySelector("#sceneList");
  container.replaceChildren();
  if (!scenes.length) {
    container.innerHTML =
      '<p class="empty-state">还没有自定义场景，可以说“记住睡眠模式：关灯，风扇调到30%”。</p>';
    return;
  }
  for (const scene of scenes) {
    const element = document.createElement("article");
    element.className = "custom-scene";

    const icon = document.createElement("span");
    icon.className = "scene-icon";
    icon.textContent = "◇";
    const text = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = scene.name;
    const detail = document.createElement("small");
    detail.textContent = scene.description;
    text.append(title, detail);

    const controls = document.createElement("div");
    controls.className = "scene-controls";
    const run = document.createElement("button");
    run.textContent = "运行";
    run.ariaLabel = `运行${scene.name}`;
    run.addEventListener("click", async () => {
      try {
        const result = await api(`/api/scenes/${scene.id}/run`, {
          method: "POST",
        });
        const message = result.errors.length
          ? `场景执行完成，但有提示：${result.errors.join(" ")}`
          : result.actions.length
            ? `已执行“${scene.name}”场景，共 ${result.actions.length} 个动作。`
            : `“${scene.name}”场景中的设备已经是目标状态。`;
        addMessage(message, "assistant");
        speakAssistant(message);
        showToast(`已运行${scene.name}`);
        await refreshState();
      } catch (error) {
        showToast(error.message);
      }
    });
    const remove = document.createElement("button");
    remove.textContent = "删除";
    remove.ariaLabel = `删除${scene.name}`;
    remove.addEventListener("click", async () => {
      try {
        await api(`/api/scenes/${scene.id}`, { method: "DELETE" });
        showToast(`已删除${scene.name}`);
        await refreshState();
      } catch (error) {
        showToast(error.message);
      }
    });
    controls.append(run, remove);
    element.append(icon, text, controls);
    container.append(element);
  }
}

function renderMemories(memories) {
  const container = document.querySelector("#memoryList");
  container.replaceChildren();
  if (!memories.length) {
    container.innerHTML =
      '<p class="empty-state">还没有长期偏好，可以说“记住我的常用风速是60%”。</p>';
    return;
  }
  for (const memory of memories) {
    const element = document.createElement("div");
    element.className = "memory-item";
    const icon = document.createElement("span");
    icon.className = "memory-icon";
    icon.textContent = "忆";
    const text = document.createElement("span");
    const label = document.createElement("small");
    const sourceLabel =
      memory.source_label ||
      (memory.source === "automatic" ? "自动学习" : "用户设定");
    label.textContent = `${memory.label} · ${sourceLabel}`;
    const value = document.createElement("strong");
    value.textContent = memory.display_value;
    text.append(label, value);
    const remove = document.createElement("button");
    remove.textContent = "忘记";
    remove.ariaLabel = `忘记${memory.label}`;
    remove.addEventListener("click", async () => {
      try {
        await api(`/api/memories/${memory.name}`, { method: "DELETE" });
        showToast(`已忘记${memory.label}`);
        await refreshState();
      } catch (error) {
        showToast(error.message);
      }
    });
    element.append(icon, text, remove);
    container.append(element);
  }
}

function renderEvents(events) {
  const container = document.querySelector("#eventList");
  container.replaceChildren();
  if (!events.length) {
    container.innerHTML =
      '<p class="empty-state">系统运行后，设备操作和自动决策会记录在这里。</p>';
    return;
  }
  const kindLabels = {
    automation: "自动化",
    scene: "场景",
    sensor: "环境",
    device: "设备",
    memory: "记忆",
    learning: "学习",
    notification: "提醒",
    alarm: "闹钟",
    undoable: "可撤销",
    undo: "已撤销",
    auto_flow: "自动流",
    manual_override: "手动接管",
  };
  for (const event of events) {
    const element = document.createElement("div");
    element.className = "event-item";
    const kind = document.createElement("span");
    kind.className = `event-kind ${event.kind}`;
    kind.textContent = kindLabels[event.kind] || "系统";
    const text = document.createElement("span");
    const message = document.createElement("strong");
    message.textContent = event.message;
    const time = document.createElement("small");
    time.textContent = new Date(event.created_at).toLocaleString("zh-CN");
    text.append(message, time);
    element.append(kind, text);
    container.append(element);
  }
}

async function refreshWeather(settings) {
  if (settings.latitude == null || settings.longitude == null) return;
  document.querySelector("#locationTitle").textContent = settings.location_name || "当前位置";
  const locationInput = document.querySelector("#locationName");
  if (document.activeElement !== locationInput) {
    locationInput.value = settings.location_name || "";
  }
  if (Date.now() - state.weatherUpdatedAt < 10 * 60 * 1000) return;
  if (weatherRequestPromise) return weatherRequestPromise;
  weatherRequestPromise = (async () => {
    try {
      const weather = await api("/api/weather");
      document.querySelector("#weatherSummary").textContent = weather.summary;
      state.weatherUpdatedAt = weather.available
        ? Date.now()
        : Date.now() - 9 * 60 * 1000;
    } catch (error) {
      document.querySelector("#weatherSummary").textContent = error.message;
    }
  })();
  try {
    await weatherRequestPromise;
  } finally {
    weatherRequestPromise = null;
  }
}

async function refreshStateOnce() {
  const data = await api("/api/state");
  document.querySelector("#temperature").textContent = data.environment.temperature.toFixed(1);
  document.querySelector("#humidity").textContent = Math.round(data.environment.humidity);
  document.querySelector("#temperatureInput").value = data.environment.temperature;
  document.querySelector("#humidityInput").value = data.environment.humidity;
  renderDevices(data.devices);
  renderAlarms(data.alarms);
  renderNotifications(
    data.notifications || [],
    data.unread_notifications || 0,
    data.proactive || {},
  );
  renderAutoFlow(data.auto_flow);
  renderAutomations(data.automations || []);
  renderScenes(data.scenes || []);
  renderMemories(data.memories || []);
  renderEvents(data.events || []);
  await refreshWeather(data.settings);

  await claimNotification();
}

async function refreshState() {
  if (refreshStatePromise) return refreshStatePromise;
  refreshStatePromise = refreshStateOnce();
  try {
    return await refreshStatePromise;
  } finally {
    refreshStatePromise = null;
  }
}

async function sendMessage(message) {
  addMessage(message, "user");
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        selected_device_id: state.selectedDeviceId,
        session_id: sessionId,
      }),
    });
    addMessage(result.reply, "assistant");
    speakAssistant(result.reply);
    if (result.context_device_id) {
      state.selectedDeviceId = null;
      state.contextDeviceId = result.context_device_id;
    }
    if (result.intent === "home_arrival") showSceneBanner();
    await refreshState();
  } catch (error) {
    addMessage(`操作失败：${error.message}`, "assistant");
  }
}

let activeVoiceSession = null;
let activeVoiceStream = null;
let activeVoiceRequestController = null;
let voiceCaptureGeneration = 0;
let voicePageHidden = document.visibilityState === "hidden";
const MAX_RECORDING_MS = 10000;
const VOICE_REQUEST_TIMEOUT_MS = 25000;
const VAD_MIN_START_RMS = 0.018;
const VAD_MAX_START_RMS = 0.06;
const VAD_START_RATIO = 2.2;
const VAD_REQUIRED_LOUD_MS = 120;
const VAD_END_SILENCE_MS = 1000;
const VAD_NO_SPEECH_HINT_MS = 4000;
const VAD_NO_SPEECH_STOP_MS = 5000;
const VAD_NOISE_CALIBRATION_MS = 300;
const VAD_INITIAL_NOISE_FLOOR = 0.01;
const SUPPORTED_RECORDING_MIME_TYPES = new Set([
  "audio/ogg",
  "audio/webm",
  "video/webm",
]);

function setVoiceStatus(message, mode = "idle") {
  voiceInputMode = mode;
  const status = document.querySelector("#voiceStatus");
  if (!status) return;
  status.textContent = message;
  status.dataset.state = mode;
}

function resetVoiceButton() {
  const button = document.querySelector("#voiceButton");
  button.disabled = false;
  button.classList.remove("recording", "processing");
  button.setAttribute("aria-label", "语音输入");
}

function stopVoiceStream(stream) {
  if (!stream) return;
  stream.getTracks().forEach((track) => {
    if (track.readyState !== "ended") track.stop();
  });
  if (activeVoiceStream === stream) activeVoiceStream = null;
}

function cleanupVoiceActivityMonitor(session) {
  if (session.vadFrame !== null) {
    window.cancelAnimationFrame(session.vadFrame);
    session.vadFrame = null;
  }
  try {
    session.vadSource?.disconnect();
    session.vadAnalyser?.disconnect();
  } catch (_error) {
    // 节点可能已经随 AudioContext 一起关闭。
  }
  session.vadSource = null;
  session.vadAnalyser = null;
  if (
    session.audioContext?.state !== "closed" &&
    typeof session.audioContext?.close === "function"
  ) {
    const closing = session.audioContext.close();
    closing?.catch?.(() => {});
  }
  session.audioContext = null;
}

function cleanupVoiceSession(session) {
  window.clearTimeout(session.recordingTimer);
  session.recordingTimer = null;
  cleanupVoiceActivityMonitor(session);
}

function preferredRecordingMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];
  if (typeof MediaRecorder.isTypeSupported !== "function") return "";
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function recordingFileName(mimeType) {
  const baseType = mimeType.split(";", 1)[0];
  const extensions = {
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
    "video/webm": "webm",
  };
  return `recording.${extensions[baseType] || "webm"}`;
}

function finishDiscardedVoiceSession(session) {
  if (activeVoiceSession === session) activeVoiceSession = null;
  resetVoiceButton();
  setVoiceStatus(
    session.discardMessage || "没有听到清晰人声，本次没有提交识别。",
    "idle",
  );
}

async function handleRecorderStopped(session) {
  if (session.stopHandled) return;
  session.stopHandled = true;
  cleanupVoiceSession(session);
  stopVoiceStream(session.stream);

  if (session.discard) {
    finishDiscardedVoiceSession(session);
    return;
  }

  const actualMimeType =
    session.recorder.mimeType || session.requestedMimeType || "audio/webm";
  const audio = new Blob(session.chunks, { type: actualMimeType });
  session.chunks.length = 0;
  if (!audio.size) {
    session.discardMessage = "没有录到声音，请重新说一次。";
    finishDiscardedVoiceSession(session);
    return;
  }

  const button = document.querySelector("#voiceButton");
  button.disabled = true;
  button.classList.add("processing");
  button.setAttribute("aria-label", "语音识别处理中");
  setVoiceStatus("正在识别中文…", "processing");

  const form = new FormData();
  form.append("audio", audio, recordingFileName(actualMimeType));
  form.append("execute", "0");
  const controller = new AbortController();
  activeVoiceRequestController = controller;
  const timeout = window.setTimeout(
    () => controller.abort("timeout"),
    VOICE_REQUEST_TIMEOUT_MS,
  );
  try {
    const response = await fetch("/api/voice/transcribe", {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "语音识别失败");
    const transcription = result.transcription;
    const text = String(transcription.text || "").trim();
    if (!text) throw new Error("没有识别到内容，请重新说一次");
    const seconds = (transcription.latency_ms / 1000).toFixed(1);
    const fallback = transcription.fallback_from
      ? "（云端不可用，已自动转为本地）"
      : "";
    showToast(
      `${transcription.provider_label}${fallback}识别完成，用时 ${seconds} 秒`,
    );
    setVoiceStatus(`已识别：${text}；正在处理指令…`, "processing");
    await sendMessage(text);
    setVoiceStatus(`识别完成：${text}`, "done");
  } catch (error) {
    if (controller.signal.reason === "pagehide") return;
    const message =
      error.name === "AbortError"
        ? "语音识别超过25秒，请重试"
        : error.message;
    setVoiceStatus(`识别失败：${message}`, "error");
    addMessage(`语音识别失败：${message}`, "assistant");
  } finally {
    window.clearTimeout(timeout);
    if (activeVoiceRequestController === controller) {
      activeVoiceRequestController = null;
    }
    if (activeVoiceSession === session) activeVoiceSession = null;
    resetVoiceButton();
  }
}

function abortVoiceSession(session, message) {
  if (!session || session.stopHandled) return;
  session.stopHandled = true;
  session.stopRequested = true;
  session.discard = true;
  cleanupVoiceSession(session);
  try {
    if (session.recorder.state === "recording") session.recorder.stop();
  } catch (_error) {
    // 录音器已经失效时仍需继续释放麦克风。
  }
  stopVoiceStream(session.stream);
  if (activeVoiceSession === session) activeVoiceSession = null;
  resetVoiceButton();
  setVoiceStatus(message, "error");
}

function requestRecordingStop(reason, { discard = false } = {}) {
  const session = activeVoiceSession;
  if (!session || session.stopRequested) return false;
  session.stopRequested = true;
  session.stopReason = reason;
  session.discard = discard;
  if (discard) {
    session.discardMessage = "没有听到清晰人声，本次没有提交识别。";
  }
  cleanupVoiceSession(session);
  const button = document.querySelector("#voiceButton");
  button.classList.remove("recording");
  button.disabled = true;
  button.setAttribute("aria-label", "正在结束语音输入");
  setVoiceStatus(
    discard ? "没有听到清晰人声，正在结束…" : "正在识别中文…",
    "processing",
  );
  try {
    if (session.recorder.state === "recording") {
      session.recorder.stop();
    } else {
      void handleRecorderStopped(session);
    }
  } catch (_error) {
    abortVoiceSession(session, "录音结束失败，请重新尝试。");
  }
  return true;
}

function isObviouslySilentSession(session) {
  return (
    session.vadAvailable &&
    session.vadCoveredFromStart &&
    !session.speechDetected &&
    session.peakRms < VAD_MIN_START_RMS * 0.55
  );
}

async function startVoiceActivityMonitor(session) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return false;
  try {
    const audioContext = new AudioContextClass();
    session.audioContext = audioContext;
    const source = audioContext.createMediaStreamSource(session.stream);
    session.vadSource = source;
    const analyser = audioContext.createAnalyser();
    session.vadAnalyser = analyser;
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.2;
    source.connect(analyser);
    if (audioContext.state !== "running") {
      const resumeResult = audioContext.resume?.();
      if (resumeResult) {
        let resumeTimer = null;
        await Promise.race([
          resumeResult,
          new Promise((resolve) => {
            resumeTimer = window.setTimeout(resolve, 300);
          }),
        ]).finally(() => window.clearTimeout(resumeTimer));
      }
    }
    if (
      audioContext.state !== "running" ||
      activeVoiceSession !== session ||
      session.stopRequested
    ) {
      cleanupVoiceActivityMonitor(session);
      session.vadAvailable = false;
      return false;
    }

    session.vadAvailable = true;
    session.vadCoveredFromStart =
      window.performance.now() - session.recordingStartedAt <= 100;
    session.vadStartedAt = window.performance.now();
    session.noiseFloor = VAD_INITIAL_NOISE_FLOOR;
    session.peakRms = 0;
    session.loudSince = null;
    session.lastVoiceAt = null;
    const samples = new Uint8Array(analyser.fftSize);

    const monitor = () => {
      if (
        activeVoiceSession !== session ||
        session.stopRequested ||
        session.recorder.state !== "recording"
      ) {
        return;
      }
      analyser.getByteTimeDomainData(samples);
      let energy = 0;
      for (const sample of samples) {
        const amplitude = (sample - 128) / 128;
        energy += amplitude * amplitude;
      }
      const rms = Math.sqrt(energy / samples.length);
      const now = window.performance.now();
      const elapsed = now - session.vadStartedAt;
      session.peakRms = Math.max(session.peakRms, rms);

      if (!session.speechDetected) {
        if (elapsed < VAD_NOISE_CALIBRATION_MS) {
          session.noiseFloor = Math.min(session.noiseFloor, rms);
        }
        const startThreshold = Math.min(
          VAD_MAX_START_RMS,
          Math.max(
            VAD_MIN_START_RMS,
            session.noiseFloor * VAD_START_RATIO,
          ),
        );
        if (rms >= startThreshold) {
          session.loudSince ??= now;
          if (now - session.loudSince >= VAD_REQUIRED_LOUD_MS) {
            session.speechDetected = true;
            session.startThreshold = startThreshold;
            session.lastVoiceAt = now;
            setVoiceStatus("已听到声音，说完后会自动识别。", "speech");
          }
        } else {
          session.loudSince = null;
          if (elapsed >= VAD_NOISE_CALIBRATION_MS) {
            session.noiseFloor = session.noiseFloor * 0.98 + rms * 0.02;
          }
        }
        if (
          !session.speechDetected &&
          !session.noSpeechHinted &&
          elapsed >= VAD_NO_SPEECH_HINT_MS
        ) {
          session.noSpeechHinted = true;
          setVoiceStatus(
            "还没检测到清晰声音，可继续说或点击麦克风结束。",
            "listening",
          );
        }
        if (!session.speechDetected && elapsed >= VAD_NO_SPEECH_STOP_MS) {
          requestRecordingStop("no_speech", {
            discard: isObviouslySilentSession(session),
          });
          return;
        }
      } else {
        const continueThreshold = Math.max(
          0.012,
          session.noiseFloor * 1.5,
          session.startThreshold * 0.55,
        );
        if (rms >= continueThreshold) {
          session.lastVoiceAt = now;
        } else if (now - session.lastVoiceAt >= VAD_END_SILENCE_MS) {
          requestRecordingStop("silence");
          return;
        }
      }
      session.vadFrame = window.requestAnimationFrame(monitor);
    };
    session.vadFrame = window.requestAnimationFrame(monitor);
    return true;
  } catch (_error) {
    cleanupVoiceActivityMonitor(session);
    session.vadAvailable = false;
    return false;
  }
}

async function toggleRecording() {
  const button = document.querySelector("#voiceButton");
  if (activeVoiceSession?.recorder.state === "recording") {
    requestRecordingStop("manual");
    showToast("录音结束，正在识别…");
    return;
  }
  if (voiceInputMode === "preparing") {
    showToast("正在获取麦克风，请稍候");
    return;
  }
  if (voiceInputMode === "processing") {
    showToast("上一段语音还在识别，请稍候");
    return;
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    showToast("当前浏览器不支持录音，请使用文字输入");
    return;
  }

  let stream = null;
  const captureGeneration = ++voiceCaptureGeneration;
  try {
    speechRequestId += 1;
    stopSpeaking();
    setVoiceStatus("正在请求麦克风权限…", "preparing");
    button.disabled = true;
    button.setAttribute("aria-label", "正在获取麦克风");
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    if (
      captureGeneration !== voiceCaptureGeneration ||
      voicePageHidden
    ) {
      stopVoiceStream(stream);
      return;
    }
    activeVoiceStream = stream;
    const mimeType = preferredRecordingMimeType();
    const recorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);
    const actualMimeType = String(recorder.mimeType || mimeType)
      .split(";", 1)[0]
      .toLowerCase();
    if (!SUPPORTED_RECORDING_MIME_TYPES.has(actualMimeType)) {
      stopVoiceStream(stream);
      resetVoiceButton();
      setVoiceStatus(
        "当前浏览器录音格式暂不支持，请使用 Chrome 或 Edge。",
        "error",
      );
      showToast("当前浏览器录音格式暂不支持，请使用 Chrome 或 Edge");
      return;
    }
    const session = {
      recorder,
      stream,
      requestedMimeType: mimeType,
      recordingStartedAt: null,
      chunks: [],
      recordingTimer: null,
      vadFrame: null,
      vadAvailable: false,
      vadCoveredFromStart: false,
      audioContext: null,
      vadSource: null,
      vadAnalyser: null,
      speechDetected: false,
      peakRms: 0,
      noSpeechHinted: false,
      stopRequested: false,
      stopHandled: false,
      discard: false,
    };
    activeVoiceSession = session;
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) session.chunks.push(event.data);
    });
    recorder.addEventListener("stop", () => {
      void handleRecorderStopped(session);
    });
    recorder.addEventListener("error", () => {
      abortVoiceSession(session, "录音发生错误，请检查麦克风后重试。");
    });
    for (const track of stream.getTracks()) {
      track.addEventListener("ended", () => {
        if (!session.stopRequested && !session.stopHandled) {
          abortVoiceSession(session, "麦克风连接已断开，请重新尝试。");
        }
      });
    }
    session.recordingStartedAt = window.performance.now();
    recorder.start();
    session.recordingTimer = window.setTimeout(() => {
      if (recorder.state === "recording") {
        const obviousSilence = isObviouslySilentSession(session);
        requestRecordingStop("max_duration", { discard: obviousSilence });
        showToast(
          obviousSilence
            ? "没有听到清晰声音，本次未提交识别"
            : "已录满10秒，正在识别…",
        );
      }
    }, MAX_RECORDING_MS);
    button.disabled = false;
    button.setAttribute("aria-label", "结束语音输入");
    button.classList.add("recording");
    setVoiceStatus("正在聆听；可再次点击麦克风结束。", "listening");
    const vadEnabled = await startVoiceActivityMonitor(session);
    if (
      activeVoiceSession !== session ||
      session.stopRequested ||
      recorder.state !== "recording"
    ) {
      return;
    }
    setVoiceStatus(
      vadEnabled
        ? "正在聆听，说完后会自动识别。"
        : "正在聆听；请再次点击麦克风结束。",
      "listening",
    );
    showToast(
      vadEnabled
        ? "正在聆听，说完后会自动结束"
        : "正在聆听，请再次点击麦克风结束",
    );
  } catch (error) {
    if (captureGeneration !== voiceCaptureGeneration) {
      stopVoiceStream(stream);
      return;
    }
    if (activeVoiceSession) {
      abortVoiceSession(activeVoiceSession, "无法使用麦克风，请检查浏览器权限。");
    } else {
      stopVoiceStream(stream);
      resetVoiceButton();
      setVoiceStatus("无法使用麦克风，请检查浏览器权限。", "error");
    }
    showToast("无法使用麦克风，请检查浏览器权限");
  }
}

function releaseVoiceResources() {
  voicePageHidden = true;
  voiceCaptureGeneration += 1;
  speechRequestId += 1;
  stopSpeaking();
  activeVoiceRequestController?.abort("pagehide");
  activeVoiceRequestController = null;
  const session = activeVoiceSession;
  if (session) {
    session.discard = true;
    session.stopRequested = true;
    session.stopHandled = true;
    cleanupVoiceSession(session);
    try {
      if (session.recorder.state === "recording") session.recorder.stop();
    } catch (_error) {
      // 页面退出时不再显示错误。
    }
    stopVoiceStream(session.stream);
    activeVoiceSession = null;
  } else {
    stopVoiceStream(activeVoiceStream);
  }
  resetVoiceButton();
  setVoiceStatus("点击麦克风开始说话；说完后会自动识别。", "idle");
}

function restoreVoicePage() {
  voicePageHidden = false;
  if (!activeVoiceSession) {
    resetVoiceButton();
    setVoiceStatus("点击麦克风开始说话；说完后会自动识别。", "idle");
  }
}

function handleVoiceVisibilityChange() {
  if (document.visibilityState === "hidden") {
    if (voiceCaptureActive()) releaseVoiceResources();
  } else if (voicePageHidden) {
    restoreVoicePage();
  }
}

document.querySelector("#chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  await sendMessage(message);
});

document.querySelector("#voiceButton").addEventListener("click", toggleRecording);
window.addEventListener("pagehide", releaseVoiceResources);
window.addEventListener("pageshow", restoreVoicePage);
document.addEventListener("visibilitychange", handleVoiceVisibilityChange);
document.querySelector("#voiceReplyButton").addEventListener("click", toggleVoiceReply);
document.querySelector("#ttsVoiceSelect").addEventListener("change", updateTtsVoice);
document.querySelector("#previewVoiceButton").addEventListener("click", previewVoice);
document.querySelector("#desktopNotificationButton").addEventListener(
  "click",
  requestDesktopNotifications,
);

document.querySelectorAll("[data-message]").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.message));
});

document.querySelector("#homeSceneButton").addEventListener("click", () => {
  showSceneBanner();
  sendMessage("我要下班回家了");
});

document.querySelector("#updateEnvironmentButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/environment", {
      method: "POST",
      body: JSON.stringify({
        temperature: document.querySelector("#temperatureInput").value,
        humidity: document.querySelector("#humidityInput").value,
      }),
    });
    showToast(result.auto_flow?.summary || "环境数据已更新");
    await refreshState();
  } catch (error) {
    showToast(error.message);
  }
});

document.querySelector("#autoFlowToggleButton").addEventListener("click", async () => {
  const button = document.querySelector("#autoFlowToggleButton");
  button.disabled = true;
  try {
    const result = await api("/api/auto-flow", {
      method: "PATCH",
      body: JSON.stringify({ enabled: !Boolean(state.autoFlow?.enabled) }),
    });
    renderAutoFlow(result.auto_flow);
    showToast(result.auto_flow.summary);
    await refreshState();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#autoFlowRunButton").addEventListener("click", async () => {
  const button = document.querySelector("#autoFlowRunButton");
  button.disabled = true;
  try {
    const result = await api("/api/auto-flow/run", { method: "POST" });
    renderAutoFlow(result.auto_flow);
    addMessage(result.auto_flow.summary, "assistant");
    speakAssistant(result.auto_flow.summary);
    showToast("AI 巡检完成");
    await refreshState();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#proactiveToggleButton").addEventListener("click", async () => {
  const button = document.querySelector("#proactiveToggleButton");
  button.disabled = true;
  try {
    const result = await api("/api/proactive", {
      method: "PATCH",
      body: JSON.stringify({ enabled: !Boolean(state.proactive?.enabled) }),
    });
    state.proactive = result.proactive;
    showToast(result.proactive.enabled ? "主动提醒已开启" : "主动提醒已暂停");
    await refreshState();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#proactiveRunButton").addEventListener("click", async () => {
  const button = document.querySelector("#proactiveRunButton");
  button.disabled = true;
  try {
    const result = await api("/api/proactive/run", { method: "POST" });
    const count = result.result.created.length;
    showToast(count ? `新生成 ${count} 条提醒` : "检查完成，没有新的风险提醒");
    await refreshState();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#notificationReadAllButton").addEventListener("click", async () => {
  const result = await api("/api/notifications/read-all", { method: "POST" });
  showToast(`已将 ${result.updated} 条提醒标记为已读`);
  await refreshState();
});

document.querySelector("#locationForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/settings/location", {
      method: "PUT",
      body: JSON.stringify({
        location_name: document.querySelector("#locationName").value,
      }),
    });
    state.weatherUpdatedAt = 0;
    showToast("位置已保存");
    await refreshState();
  } catch (error) {
    showToast(error.message);
  }
});

const currentHour = new Date().getHours();
document.querySelector("#greeting").textContent =
  currentHour < 11 ? "早上好" : currentHour < 18 ? "下午好" : "晚上好";

updateVoiceReplyButton();
updateDesktopNotificationButton();
loadTtsVoices().catch(() => {
  document.querySelector("#ttsProviderLabel").textContent = "音色状态读取失败";
});
refreshState().catch((error) => showToast(error.message));
window.setInterval(() => refreshState().catch(() => {}), 5000);
