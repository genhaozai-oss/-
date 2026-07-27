import json
from urllib.parse import urlencode
from urllib.request import urlopen

from flask import current_app


WEATHER_CODES = {
    0: "晴朗",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴天",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "阵雨",
    81: "较强阵雨",
    82: "强阵雨",
    95: "雷雨",
}


def geocode_location(location_name):
    name = str(location_name or "").strip()
    if not name:
        return {
            "available": True,
            "found": False,
            "summary": "请输入城市名。",
        }

    query = urlencode(
        {
            "name": name,
            "count": 1,
            "language": "zh",
            "format": "json",
        }
    )
    url = f"https://geocoding-api.open-meteo.com/v1/search?{query}"
    try:
        with urlopen(
            url, timeout=current_app.config["WEATHER_TIMEOUT_SECONDS"]
        ) as response:
            data = json.load(response)
    except Exception as error:
        return {
            "available": False,
            "found": False,
            "summary": "城市查询服务暂时不可用，请稍后再试。",
            "error": str(error),
        }

    results = data.get("results") or []
    if not results:
        return {
            "available": True,
            "found": False,
            "summary": f"没有找到“{name}”，请尝试填写完整城市名。",
        }

    result = results[0]
    return {
        "available": True,
        "found": True,
        "location_name": result["name"],
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "admin1": result.get("admin1", ""),
        "country": result.get("country", ""),
    }


def get_weather(settings):
    latitude = settings.get("latitude")
    longitude = settings.get("longitude")
    if latitude in {None, ""} or longitude in {None, ""}:
        return {
            "configured": False,
            "summary": "请先填写城市名，或点击“使用当前位置”。",
        }

    query = urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,relative_humidity_2m,"
                "precipitation,weather_code,wind_speed_10m"
            ),
            "daily": "precipitation_probability_max,temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
            "forecast_days": 1,
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    try:
        with urlopen(
            url, timeout=current_app.config["WEATHER_TIMEOUT_SECONDS"]
        ) as response:
            data = json.load(response)
    except Exception as error:
        return {
            "configured": True,
            "available": False,
            "summary": "天气服务暂时不可用，室内控制不受影响。",
            "error": str(error),
        }

    current = data["current"]
    daily = data["daily"]
    rain_probability = daily["precipitation_probability_max"][0]
    weather_text = WEATHER_CODES.get(current["weather_code"], "天气情况未知")
    advice = "建议带伞。" if rain_probability >= 40 else "降雨概率不高。"
    location_name = settings.get("location_name", "当前位置")
    summary = (
        f"{location_name}现在{weather_text}，{current['temperature_2m']:.1f}℃，"
        f"今日 {daily['temperature_2m_min'][0]:.0f}～"
        f"{daily['temperature_2m_max'][0]:.0f}℃，"
        f"最高降雨概率 {rain_probability}%。{advice}"
    )
    return {
        "configured": True,
        "available": True,
        "summary": summary,
        "current": current,
        "daily": {
            "temperature_max": daily["temperature_2m_max"][0],
            "temperature_min": daily["temperature_2m_min"][0],
            "rain_probability": rain_probability,
        },
    }
