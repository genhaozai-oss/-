from pathlib import Path

from dotenv import set_key

try:
    import msvcrt
except ImportError:
    msvcrt = None


ENV_PATH = Path(__file__).resolve().parent / ".env"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen-plus"


def read_secret(prompt):
    if msvcrt is None:
        from getpass import getpass

        return getpass(prompt)

    print(prompt, end="", flush=True)
    characters = []
    while True:
        character = msvcrt.getwch()
        if character in {"\r", "\n"}:
            print()
            return "".join(characters)
        if character == "\003":
            raise KeyboardInterrupt
        if character == "\b":
            if characters:
                characters.pop()
                print("\b \b", end="", flush=True)
            continue
        if character in {"\x00", "\xe0"}:
            msvcrt.getwch()
            continue
        if character.isprintable():
            characters.append(character)
            print("*", end="", flush=True)


def validate_key(api_key):
    if not api_key:
        return "没有读取到任何字符，请粘贴完整 API Key 后再按回车。"
    if api_key.startswith("sk-sp-"):
        return "这是 Token Plan Key，不能用于本项目后端。请使用 sk-ws- 开头的普通百炼 Key。"
    if not api_key.startswith("sk-"):
        return "必须粘贴完整 Key，包括开头的 sk-ws-，不能只从 ws 开始。"
    if len(api_key) < 20:
        return f"只读取到 {len(api_key)} 个字符，Key 明显不完整，请重新粘贴。"
    return None


def masked_key(api_key):
    return f"{api_key[:6]}{'*' * 8}{api_key[-4:]}（共 {len(api_key)} 个字符）"


def main():
    print("请粘贴完整百炼 API Key。输入内容会显示为星号，Key 本身不会显示。")
    while True:
        api_key = read_secret("API Key: ").strip()
        validation_error = validate_key(api_key)
        if not validation_error:
            break
        print(f"输入无效：{validation_error}")

    set_key(ENV_PATH, "SMARTHOME_LLM_BASE_URL", BASE_URL)
    set_key(ENV_PATH, "SMARTHOME_LLM_MODEL", MODEL)
    set_key(ENV_PATH, "SMARTHOME_LLM_API_KEY", api_key)
    print(f"已读取并保存：{masked_key(api_key)}")
    print(f"配置文件：{ENV_PATH}")
    print("以后直接运行 python run.py 即可，不需要重新输入 API Key。")


if __name__ == "__main__":
    main()
