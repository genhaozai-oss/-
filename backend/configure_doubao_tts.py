from pathlib import Path

from dotenv import set_key


ENV_PATH = Path(__file__).resolve().parent / ".env"
DEFAULT_VOICE = "zh_female_vv_uranus_bigtts"


def main():
    print("请粘贴豆包语音控制台生成的 API Key。")
    print("输入会显示在当前窗口中，请确认旁边没有其他人。")
    api_key = input("豆包语音 API Key: ").strip()
    if len(api_key) < 16:
        print("API Key 看起来不完整，没有保存。")
        return 1

    set_key(ENV_PATH, "SMARTHOME_DOUBAO_TTS_API_KEY", api_key)
    set_key(ENV_PATH, "SMARTHOME_DOUBAO_TTS_RESOURCE_ID", "seed-tts-2.0")
    set_key(ENV_PATH, "SMARTHOME_DOUBAO_TTS_VOICE", DEFAULT_VOICE)
    print("豆包 TTS 2.0 配置完成，默认音色：Vivi 2.0。")
    print("重新运行 run.py 后生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
