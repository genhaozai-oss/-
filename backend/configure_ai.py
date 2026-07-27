from getpass import getpass
from pathlib import Path

from dotenv import set_key


ENV_PATH = Path(__file__).resolve().parent / ".env"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen-plus"


def main():
    api_key = getpass("请输入百炼 API Key（输入不会显示）: ").strip()
    if not api_key.startswith("sk-"):
        raise SystemExit("API Key 格式不正确，应以 sk- 开头。")

    set_key(ENV_PATH, "SMARTHOME_LLM_BASE_URL", BASE_URL)
    set_key(ENV_PATH, "SMARTHOME_LLM_MODEL", MODEL)
    set_key(ENV_PATH, "SMARTHOME_LLM_API_KEY", api_key)
    print(f"配置已安全保存到 {ENV_PATH}")
    print("以后直接运行 python run.py 即可，不需要重新输入 API Key。")


if __name__ == "__main__":
    main()
