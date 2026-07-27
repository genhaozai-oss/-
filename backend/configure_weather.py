import gzip
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import set_key

try:
    import msvcrt
except ImportError:
    msvcrt = None


ENV_PATH = Path(__file__).resolve().parent / ".env"


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


def normalize_host(host):
    normalized = str(host or "").strip().lower()
    normalized = normalized.removeprefix("https://").removeprefix("http://")
    return normalized.strip("/")


def validate_host(host):
    if not host:
        return "没有读取到 API Host。"
    if "/" in host or " " in host:
        return "只需输入域名，不要包含路径或空格。"
    if not host.endswith(".qweatherapi.com"):
        return "API Host 应以 .qweatherapi.com 结尾。"
    return None


def validate_key(api_key):
    if not api_key:
        return "没有读取到 API Key。"
    if len(api_key) < 10 or any(character.isspace() for character in api_key):
        return "API Key 格式不完整，请重新粘贴。"
    return None


def test_credentials(host, api_key):
    query = urlencode(
        {"location": "北京", "range": "cn", "number": 1, "lang": "zh"}
    )
    request = Request(
        f"https://{host}/geo/v2/city/lookup?{query}",
        headers={
            "X-QW-Api-Key": api_key,
            "User-Agent": "SmartHome-Graduation-Project/1.0",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read()
            headers = getattr(response, "headers", {})
            if headers.get("Content-Encoding") == "gzip":
                payload = gzip.decompress(payload)
            data = json.loads(payload)
    except Exception as error:
        return False, f"连接失败：{error}"
    if str(data.get("code")) != "200":
        return False, f"和风天气返回错误码 {data.get('code', 'unknown')}。"
    if not data.get("location"):
        return False, "凭据可连接，但城市查询没有返回结果。"
    return True, "连接成功"


def masked_key(api_key):
    return f"{api_key[:4]}{'*' * 8}{api_key[-4:]}（共 {len(api_key)} 个字符）"


def main():
    print("配置和风天气。Host 会显示，API Key 只显示为星号。")
    while True:
        host = normalize_host(input("API Host: "))
        host_error = validate_host(host)
        if host_error:
            print(f"Host 无效：{host_error}")
            continue

        api_key = read_secret("API Key: ").strip()
        key_error = validate_key(api_key)
        if key_error:
            print(f"Key 无效：{key_error}")
            continue

        print("正在验证和风天气连接……")
        connected, message = test_credentials(host, api_key)
        if connected:
            break
        print(f"验证失败：{message}")
        print("请重新输入，或按 Ctrl+C 退出。")

    set_key(ENV_PATH, "SMARTHOME_WEATHER_API_HOST", host)
    set_key(ENV_PATH, "SMARTHOME_WEATHER_API_KEY", api_key)
    print(f"已保存 Host：{host}")
    print(f"已保存 Key：{masked_key(api_key)}")
    print(f"配置文件：{ENV_PATH}")
    print("请重新启动 run.py 使配置生效。")


if __name__ == "__main__":
    main()
