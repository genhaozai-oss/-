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
  devices: [],
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
  if (activeSpeechAudio) {
    activeSpeechAudio.pause();
    activeSpeechAudio.removeAttribute("src");
    activeSpeechAudio = null;
  }
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
  utterance.addEventListener("end", () => {
    if (activeSpeechUtterance === utterance) activeSpeechUtterance = null;
  });
  window.speechSynthesis.speak(utterance);
  return true;
}

async function speakAssistant(text, { force = false } = {}) {
  if (!state.voiceReplyEnabled && !force) return false;
  const spokenText = cleanSpokenText(text);
  if (!spokenText) return false;

  const requestId = ++speechRequestId;
  stopSpeaking();
  if (state.ttsVoice === "system") return speakWithSystemVoice(spokenText);

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
    ) return;

    const audio = new Audio(result.audio_url);
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
    if (requestId === speechRequestId && speechSynthesisSupported()) {
      showToast("云端音色不可用，已改用系统声音");
      return speakWithSystemVoice(spokenText);
    }
    if (requestId === speechRequestId) {
      showToast(error.message || "云端语音播报失败");
    }
    return false;
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
  const container = document.querySelector("#deviceList");
  container.replaceChildren();
  for (const device of devices) {
    const element = document.createElement("div");
    element.className = `device${state.selectedDeviceId === device.id ? " selected" : ""}`;
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
      document.querySelector("#selectedDeviceText").textContent = `已选择：${device.name}`;
      renderDevices(state.devices);
    });
    element.querySelector(".switch").addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        await api(`/api/devices/${device.id}`, {
          method: "PATCH",
          body: JSON.stringify({ state: device.state === "on" ? "off" : "on" }),
        });
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
          await api(
            `/api/devices/${device.id}/capabilities/${capability.capability}`,
            {
              method: "PATCH",
              body: JSON.stringify({ value: Number(input.value) }),
            },
          );
          showToast(`${device.name}${capability.display_name}已更新`);
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
    label.textContent = memory.label;
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
    alarm: "闹钟",
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
  try {
    const weather = await api("/api/weather");
    document.querySelector("#weatherSummary").textContent = weather.summary;
    state.weatherUpdatedAt = Date.now();
  } catch (error) {
    document.querySelector("#weatherSummary").textContent = error.message;
  }
}

async function refreshState() {
  const data = await api("/api/state");
  document.querySelector("#temperature").textContent = data.environment.temperature.toFixed(1);
  document.querySelector("#humidity").textContent = Math.round(data.environment.humidity);
  document.querySelector("#temperatureInput").value = data.environment.temperature;
  document.querySelector("#humidityInput").value = data.environment.humidity;
  renderDevices(data.devices);
  renderAlarms(data.alarms);
  renderAutomations(data.automations || []);
  renderScenes(data.scenes || []);
  renderMemories(data.memories || []);
  renderEvents(data.events || []);
  refreshWeather(data.settings);

  const dueAlarmMessages = [];
  for (const alarm of data.due_alarms) {
    const reminder = `闹钟提醒：${alarm.label}`;
    addMessage(reminder, "assistant");
    dueAlarmMessages.push(reminder);
    showToast(`闹钟：${alarm.label}`);
  }
  if (dueAlarmMessages.length) {
    speakAssistant(dueAlarmMessages.join("；"));
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
    if (result.intent === "home_arrival") showSceneBanner();
    await refreshState();
  } catch (error) {
    addMessage(`操作失败：${error.message}`, "assistant");
  }
}

let mediaRecorder = null;
let recordedChunks = [];
let recordingTimer = null;
let voicePreparing = false;
let voiceProcessing = false;
const MAX_RECORDING_MS = 10000;
const VOICE_REQUEST_TIMEOUT_MS = 25000;

function preferredRecordingMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function recordingFileName(mimeType) {
  const baseType = mimeType.split(";", 1)[0];
  const extensions = {
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
  };
  return `recording.${extensions[baseType] || "webm"}`;
}

function stopRecording() {
  window.clearTimeout(recordingTimer);
  recordingTimer = null;
  if (mediaRecorder?.state === "recording") {
    mediaRecorder.stop();
  }
  const button = document.querySelector("#voiceButton");
  button.classList.remove("recording");
  button.disabled = true;
}

async function toggleRecording() {
  const button = document.querySelector("#voiceButton");
  if (voicePreparing) {
    showToast("正在获取麦克风，请稍候");
    return;
  }
  if (voiceProcessing) {
    showToast("上一段语音还在识别，请稍候");
    return;
  }
  if (mediaRecorder?.state === "recording") {
    stopRecording();
    showToast("录音结束，正在识别…");
    return;
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    showToast("当前浏览器不支持录音，请使用文字输入");
    return;
  }

  let stream = null;
  try {
    voicePreparing = true;
    button.disabled = true;
    button.setAttribute("aria-label", "正在获取麦克风");
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    recordedChunks = [];
    const mimeType = preferredRecordingMimeType();
    if (!mimeType) {
      throw new Error("当前浏览器不支持 WebM 或 Ogg 录音");
    }
    const recorder = new MediaRecorder(stream, { mimeType });
    mediaRecorder = recorder;
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) recordedChunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      window.clearTimeout(recordingTimer);
      recordingTimer = null;
      stream.getTracks().forEach((track) => track.stop());
      voiceProcessing = true;
      button.disabled = true;
      button.classList.add("processing");
      button.setAttribute("aria-label", "语音识别处理中");

      const actualMimeType = recorder.mimeType || mimeType || "audio/webm";
      const audio = new Blob(recordedChunks, { type: actualMimeType });
      const form = new FormData();
      form.append("audio", audio, recordingFileName(actualMimeType));
      form.append("execute", "0");
      showToast("正在识别中文…");

      const controller = new AbortController();
      const timeout = window.setTimeout(
        () => controller.abort(),
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
        const seconds = (transcription.latency_ms / 1000).toFixed(1);
        const fallback = transcription.fallback_from
          ? "（云端不可用，已自动转为本地）"
          : "";
        showToast(
          `${transcription.provider_label}${fallback}识别完成，用时 ${seconds} 秒`,
        );
        await sendMessage(transcription.text);
      } catch (error) {
        const message =
          error.name === "AbortError"
            ? "语音识别超过25秒，请重试"
            : error.message;
        addMessage(`语音识别失败：${message}`, "assistant");
      } finally {
        window.clearTimeout(timeout);
        voiceProcessing = false;
        button.disabled = false;
        button.classList.remove("processing");
        button.setAttribute("aria-label", "语音输入");
        if (mediaRecorder === recorder) mediaRecorder = null;
      }
    });
    recorder.start(250);
    voicePreparing = false;
    button.disabled = false;
    button.setAttribute("aria-label", "语音输入");
    button.classList.add("recording");
    recordingTimer = window.setTimeout(() => {
      if (recorder.state === "recording") {
        stopRecording();
        showToast("已录满10秒，正在识别…");
      }
    }, MAX_RECORDING_MS);
    showToast("正在聆听，再点一次结束；最多录10秒");
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    voicePreparing = false;
    button.disabled = false;
    button.classList.remove("recording", "processing");
    button.setAttribute("aria-label", "语音输入");
    showToast(
      error.message.includes("WebM")
        ? `${error.message}，请使用 Edge 或 Chrome`
        : "无法使用麦克风，请检查浏览器权限",
    );
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
document.querySelector("#voiceReplyButton").addEventListener("click", toggleVoiceReply);
document.querySelector("#ttsVoiceSelect").addEventListener("change", updateTtsVoice);
document.querySelector("#previewVoiceButton").addEventListener("click", previewVoice);

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
    showToast(
      result.actions.length
        ? `环境已更新，自动执行了 ${result.actions.length} 个动作`
        : "环境数据已更新，没有规则需要执行",
    );
    await refreshState();
  } catch (error) {
    showToast(error.message);
  }
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
loadTtsVoices().catch(() => {
  document.querySelector("#ttsProviderLabel").textContent = "音色状态读取失败";
});
refreshState().catch((error) => showToast(error.message));
window.setInterval(() => refreshState().catch(() => {}), 15000);
