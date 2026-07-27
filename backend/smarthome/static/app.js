const storedSessionId = window.localStorage.getItem("smarthome_session_id");
const sessionId =
  storedSessionId ||
  (window.crypto?.randomUUID?.() ??
    `session-${Date.now()}-${Math.random().toString(16).slice(2)}`);
window.localStorage.setItem("smarthome_session_id", sessionId);

const state = {
  selectedDeviceId: null,
  devices: [],
  weatherUpdatedAt: 0,
};

const icons = {
  fan: "✣",
  humidifier: "◌",
  dehumidifier: "◍",
  light: "☼",
};

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

function addMessage(text, role) {
  const messages = document.querySelector("#messages");
  const element = document.createElement("div");
  element.className = `message ${role}-message`;
  element.textContent = text;
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

async function refreshWeather(settings) {
  if (!settings.latitude || !settings.longitude) return;
  document.querySelector("#locationTitle").textContent = settings.location_name || "当前位置";
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
  refreshWeather(data.settings);

  for (const alarm of data.due_alarms) {
    addMessage(`闹钟提醒：${alarm.label}`, "assistant");
    showToast(`闹钟：${alarm.label}`);
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
    await refreshState();
  } catch (error) {
    addMessage(`操作失败：${error.message}`, "assistant");
  }
}

let mediaRecorder = null;
let recordedChunks = [];

async function toggleRecording() {
  const button = document.querySelector("#voiceButton");
  if (mediaRecorder?.state === "recording") {
    mediaRecorder.stop();
    button.classList.remove("recording");
    return;
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    showToast("当前浏览器不支持录音，请使用文字输入");
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) recordedChunks.push(event.data);
    });
    mediaRecorder.addEventListener("stop", async () => {
      stream.getTracks().forEach((track) => track.stop());
      const audio = new Blob(recordedChunks, { type: mediaRecorder.mimeType });
      const form = new FormData();
      form.append("audio", audio, "recording.webm");
      if (state.selectedDeviceId) {
        form.append("selected_device_id", state.selectedDeviceId);
      }
      form.append("session_id", sessionId);
      addMessage("正在识别语音…", "user");
      try {
        const response = await fetch("/api/voice/transcribe", {
          method: "POST",
          body: form,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "语音识别失败");
        addMessage(`识别结果：${result.transcription.text}`, "assistant");
        addMessage(result.result.reply, "assistant");
        await refreshState();
      } catch (error) {
        addMessage(`语音识别失败：${error.message}`, "assistant");
      }
    });
    mediaRecorder.start();
    button.classList.add("recording");
    showToast("正在聆听，再点一次结束");
  } catch (error) {
    showToast("无法使用麦克风，请检查浏览器权限");
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

document.querySelectorAll("[data-message]").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.message));
});

document.querySelector("#homeSceneButton").addEventListener("click", () => {
  sendMessage("我要下班回家了");
});

document.querySelector("#updateEnvironmentButton").addEventListener("click", async () => {
  try {
    await api("/api/environment", {
      method: "POST",
      body: JSON.stringify({
        temperature: document.querySelector("#temperatureInput").value,
        humidity: document.querySelector("#humidityInput").value,
      }),
    });
    showToast("环境数据已更新，并执行自动联动");
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
        latitude: document.querySelector("#latitude").value,
        longitude: document.querySelector("#longitude").value,
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

refreshState().catch((error) => showToast(error.message));
window.setInterval(() => refreshState().catch(() => {}), 15000);
