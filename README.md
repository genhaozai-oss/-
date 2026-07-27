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

## 推荐硬件与预算

价格按常见学生购买渠道估算，购买前应再次核对。核心演示预计 300～600 元，
总预算控制在 1000 元以内。

| 模块 | 推荐规格 | 估算价格 | 用途 |
| --- | --- | ---: | --- |
| 主控 | ESP32-S3，16 MB Flash、8 MB PSRAM | 60～100 元 | 控制与离线语音 |
| 麦克风 | INMP441 I2S | 10～20 元 | 语音输入 |
| 功放与喇叭 | MAX98357A + 3W 喇叭 | 20～35 元 | 语音提示 |
| 温湿度 | AHT20 或 SHT30 | 10～25 元 | 室内环境检测 |
| 风扇 | 5V/12V 低压风扇 + MOSFET 模块 | 20～50 元 | 实际联动设备 |
| 加湿器 | 成品 USB 低压加湿器 | 30～80 元 | 可选实际设备 |
| 抽湿演示 | LED 或网页虚拟设备 | 5～15 元 | 避免水电风险 |
| 窗帘模型 | SG90 舵机 + 模型轨道 | 20～50 元 | 可选演示 |
| 时钟 | DS3231 | 10～20 元 | 断网计时 |
| 辅材 | 面包板、线材、电源、外壳 | 80～180 元 | 接线与保护 |

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
