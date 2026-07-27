# 家庭智能管理助手

这是一个面向本科毕业设计的低成本家庭智能管理系统。系统使用 ESP32-S3
连接传感器和家居模块，Windows 电脑作为边缘计算主机，完成文字/语音理解、
设备记忆、环境联动、天气提醒和闹钟管理。

## 当前能做什么

- 通过中文文字控制灯、风扇、加湿器和抽湿器
- 记住用户为设备设置的新名称
- 根据温湿度自动执行“准备回家”场景
- 获取天气并生成出行提醒
- 用自然语言创建闹钟
- 在没有硬件时使用模拟设备完成整套演示
- 所有高风险家电默认只模拟，不直接控制 220V 市电

## 快速运行

要求 Python 3.11 或更高版本。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

浏览器打开 <http://127.0.0.1:5000>。

运行测试：

```powershell
cd backend
python -m pytest
```

## 启用 ESP32-S3 通信

电脑需先安装并启动 Mosquitto MQTT Broker。边缘服务通过以下环境变量启用 MQTT：

```powershell
$env:SMARTHOME_MQTT_ENABLED="1"
$env:SMARTHOME_MQTT_BROKER="mqtt://127.0.0.1:1883"
python run.py
```

固件采用 ESP-IDF 5.4 或更高版本：

```powershell
cd firmware
idf.py set-target esp32s3
idf.py menuconfig
idf.py build
idf.py -p COM端口 flash monitor
```

在 `menuconfig → 家庭智能助手配置` 中填写 Wi-Fi、运行边缘服务的电脑局域网
IP，以及实际使用的 GPIO。Wi-Fi 密码只保存在本机生成的 `sdkconfig` 中，该文件
已被 Git 忽略，不会上传到仓库。

## 启用本地语音识别

这台项目电脑可以运行 `faster-whisper small`。首次使用会下载模型，普通文字
功能不依赖该模型。

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-voice.txt
python run.py
```

默认使用 CPU `int8`，稳定性优先。后续确认 CUDA 运行库无误后，可以切换显卡：

```powershell
$env:SMARTHOME_SPEECH_DEVICE="cuda"
$env:SMARTHOME_SPEECH_COMPUTE_TYPE="float16"
python run.py
```

## 启用可选大模型

系统支持兼容 Chat Completions 的本地或云端服务。规则解析始终优先；只有规则
无法理解时才请求大模型。大模型只能返回受限意图，最终设备名称和动作仍由本地
代码校验，避免模型直接操作不存在的设备。

```powershell
$env:SMARTHOME_LLM_BASE_URL="http://127.0.0.1:11434/v1"
$env:SMARTHOME_LLM_MODEL="本地模型名称"
# 云端服务需要时再设置：
# $env:SMARTHOME_LLM_API_KEY="你的密钥"
python run.py
```

## 推荐硬件与预算

价格按常见学生购买渠道估算，购买前应再次核对。建议先只购买“首轮必买”，
预计 180～350 元；确认软件与风扇联调成功后再考虑扩展，总预算不超过 1000 元。

| 优先级 | 模块 | 推荐规格 | 估算价格 | 用途 |
| --- | --- | --- | ---: | --- |
| 必买 | 主控 | ESP32-S3，16 MB Flash、8 MB PSRAM，Type-C | 60～100 元 | 控制器 |
| 必买 | 温湿度 | AHT20 I2C 模块，带排针 | 10～25 元 | 室内环境检测 |
| 必买 | 风扇 | 5V 两线低压风扇 | 15～35 元 | 唯一实际家居负载 |
| 必买 | 驱动 | 3.3V 高电平可触发的 MOSFET 开关模块 | 10～25 元 | 隔离 ESP32 与风扇电流 |
| 必买 | 辅材 | 面包板、杜邦线、220Ω 电阻、LED | 30～60 元 | 接线与虚拟设备指示 |
| 必买 | 电源 | 正规 5V USB 电源，建议 2A 或以上 | 30～60 元 | 风扇独立供电 |
| 建议 | 外壳 | 塑料项目盒 | 20～50 元 | 防止裸线被碰到 |
| 后买 | 麦克风 | INMP441 I2S | 10～20 元 | ESP32 离线固定命令 |
| 后买 | 功放与喇叭 | MAX98357A + 3W 喇叭 | 20～35 元 | ESP32 语音提示 |
| 后买 | 窗帘模型 | SG90 舵机 + 模型轨道 | 20～50 元 | 可选演示 |
| 后买 | 时钟 | DS3231 | 10～20 元 | 断网计时 |
| 不买 | 加湿/抽湿 | 网页虚拟设备 + LED | 0～10 元 | 完整演示逻辑，不接触水电 |

首轮不必购买麦克风和喇叭，直接使用电脑自带麦克风、扬声器和本地 Whisper，
先把核心闭环稳定跑通。

## 首轮低压接线

以下 GPIO 与固件默认配置一致，若开发板丝印不同，烧录前在 `menuconfig` 修改。

| ESP32-S3 | 外部模块 | 说明 |
| --- | --- | --- |
| 3V3 | AHT20 VCC | 传感器使用 3.3V |
| GND | AHT20 GND | 共地 |
| GPIO8 | AHT20 SDA | I2C 数据 |
| GPIO9 | AHT20 SCL | I2C 时钟 |
| GPIO4 | MOSFET 模块信号端 | 只接控制信号 |
| GND | MOSFET 模块信号地 | ESP32 与风扇电源共地 |

风扇的工作电流不能从 ESP32 的 GPIO 或 3V3 引脚获取。风扇使用独立 5V
电源，经 MOSFET 模块开关；只把两个电源的 GND 连接起来。购买模块后必须先核对
模块丝印和商家接线图，不能仅按外观猜测端子。

## 安全原则

1. 不使用裸露继电器直接控制 220V 家电。
2. 加湿器只使用完整的成品 USB 设备，控制模块和接头放在防溅外壳中。
3. 抽湿功能优先使用网页虚拟设备或 LED 演示。
4. 空调等市电家电后续使用红外遥控，不拆机、不接市电控制线。
5. 风扇使用独立低压电源，并确保 ESP32 与驱动模块共地。

## 系统结构

```text
网页/语音输入
      │
      ▼
Windows 边缘服务 ── 天气 API / 可选云端大模型
      │
     MQTT
      │
      ▼
ESP32-S3 ── 温湿度、低压风扇、灯光、窗帘模型
```

项目进度和下一步工作记录在 [PROJECT_STATUS.md](PROJECT_STATUS.md)。
