import gzip
import json
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import current_app


_WEATHER_CACHE = {}
_WEATHER_WARNING_CACHE = {}
_WEATHER_LOCK = threading.Lock()
_WEATHER_WARNING_LOCK = threading.Lock()


def _request_error_message(error):
    status = getattr(error, "code", None)
    if status in {401, 403}:
        return (
            f"和风天气接口拒绝访问（HTTP {status}），"
            "请检查 API Host 与所选 API 权限"
        )
    return str(error)


def _failure_cache_seconds(error):
    if getattr(error, "code", None) in {401, 403}:
        return current_app.config["WEATHER_CACHE_SECONDS"]
    return current_app.config["WEATHER_FAILURE_CACHE_SECONDS"]


def clear_weather_cache():
    with _WEATHER_LOCK:
        _WEATHER_CACHE.clear()
    with _WEATHER_WARNING_LOCK:
        _WEATHER_WARNING_CACHE.clear()


def _credentials():
    host = str(current_app.config.get("WEATHER_API_HOST", "")).strip()
    host = host.removeprefix("https://").removeprefix("http://").strip("/")
    api_key = str(current_app.config.get("WEATHER_API_KEY", "")).strip()
    return host, api_key


def _request_json(path, parameters):
    host, api_key = _credentials()
    if not host or not api_key:
        raise RuntimeError("和风天气尚未配置")

    url = f"https://{host}{path}?{urlencode(parameters)}"
    request = Request(
        url,
        headers={
            "X-QW-Api-Key": api_key,
            "User-Agent": "SmartHome-Graduation-Project/1.0",
        },
    )
    with urlopen(
        request, timeout=current_app.config["WEATHER_TIMEOUT_SECONDS"]
    ) as response:
        payload = response.read()
        headers = getattr(response, "headers", {})
        if headers.get("Content-Encoding") == "gzip":
            payload = gzip.decompress(payload)
        data = json.loads(payload)
    if str(data.get("code")) != "200":
        raise RuntimeError(f"和风天气返回错误码 {data.get('code', 'unknown')}")
    return data


def geocode_location(location_name):
    name = str(location_name or "").strip()
    if not name:
        return {
            "available": True,
            "found": False,
            "summary": "请输入城市名。",
        }

    host, api_key = _credentials()
    if not host or not api_key:
        return {
            "available": False,
            "found": False,
            "summary": "请先运行 configure_weather.py 配置和风天气。",
        }

    try:
        data = _request_json(
            "/geo/v2/city/lookup",
            {
                "location": name,
                "range": "cn",
                "number": 1,
                "lang": "zh",
            },
        )
    except Exception as error:
        return {
            "available": False,
            "found": False,
            "summary": "城市查询失败，请检查和风天气 Key 与 API Host。",
            "error": str(error),
        }

    results = data.get("location") or []
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
        "provider": "qweather",
        "location_id": result["id"],
        "location_name": result["name"],
        "latitude": float(result["lat"]),
        "longitude": float(result["lon"]),
        "admin1": result.get("adm1", ""),
        "country": result.get("country", ""),
    }


def _location_query(settings):
    location_id = settings.get("weather_location_id")
    if location_id:
        return str(location_id)

    latitude = settings.get("latitude")
    longitude = settings.get("longitude")
    if latitude in {None, ""} or longitude in {None, ""}:
        return None
    return f"{float(longitude):.2f},{float(latitude):.2f}"


def _cached_weather(cache_key):
    cached = _WEATHER_CACHE.get(cache_key)
    if not cached:
        return None
    cache_seconds = cached.get(
        "cache_seconds",
        current_app.config["WEATHER_CACHE_SECONDS"],
    )
    if time.monotonic() - cached["saved_at"] >= cache_seconds:
        return None
    return cached["value"]


def _cached_weather_warnings(cache_key):
    cached = _WEATHER_WARNING_CACHE.get(cache_key)
    if not cached:
        return None
    cache_seconds = cached.get(
        "cache_seconds",
        current_app.config["WEATHER_CACHE_SECONDS"],
    )
    if time.monotonic() - cached["saved_at"] >= cache_seconds:
        return None
    return cached["value"]


def _cache_weather_failure(cache, cache_key, result, error):
    cache[cache_key] = {
        "saved_at": time.monotonic(),
        "cache_seconds": _failure_cache_seconds(error),
        "value": result,
    }
    return result


def get_weather_warnings(settings):
    host, api_key = _credentials()
    if not host or not api_key:
        return {
            "configured": False,
            "available": False,
            "warnings": [],
        }
    location = _location_query(settings)
    if not location:
        return {
            "configured": True,
            "available": False,
            "warnings": [],
        }

    cache_key = (host, location)
    cached = _cached_weather_warnings(cache_key)
    if cached:
        return cached
    with _WEATHER_WARNING_LOCK:
        cached = _cached_weather_warnings(cache_key)
        if cached:
            return cached
        try:
            data = _request_json(
                "/v7/warning/now",
                {"location": location, "lang": "zh"},
            )
            warnings = [
                {
                    "id": str(item.get("id") or ""),
                    "sender": item.get("sender", ""),
                    "title": item.get("title", "天气预警"),
                    "severity": item.get("severity", ""),
                    "type": item.get("typeName", item.get("type", "")),
                    "text": item.get("text", ""),
                    "start_time": item.get("startTime", ""),
                    "end_time": item.get("endTime", ""),
                    "status": item.get("status", ""),
                }
                for item in data.get("warning") or []
                if item.get("status") != "Cancel"
            ]
        except Exception as error:
            result = {
                "configured": True,
                "available": False,
                "warnings": [],
                "error": _request_error_message(error),
            }
            return _cache_weather_failure(
                _WEATHER_WARNING_CACHE,
                cache_key,
                result,
                error,
            )
        result = {
            "configured": True,
            "available": True,
            "warnings": warnings,
        }
        _WEATHER_WARNING_CACHE[cache_key] = {
            "saved_at": time.monotonic(),
            "value": result,
        }
        return result


def get_weather(settings):
    host, api_key = _credentials()
    if not host or not api_key:
        return {
            "configured": False,
            "available": False,
            "provider": "qweather",
            "summary": "请先运行 configure_weather.py 配置和风天气。",
        }

    location = _location_query(settings)
    if not location:
        return {
            "configured": True,
            "available": False,
            "provider": "qweather",
            "summary": "请先填写城市名。",
        }

    location_name = settings.get("location_name", "当前城市")
    cache_key = (host, location, location_name)
    cached = _cached_weather(cache_key)
    if cached:
        return cached
    with _WEATHER_LOCK:
        cached = _cached_weather(cache_key)
        if cached:
            return cached
        try:
            current_data = _request_json(
                "/v7/weather/now", {"location": location, "lang": "zh"}
            )
            daily_data = _request_json(
                "/v7/weather/3d", {"location": location, "lang": "zh"}
            )
            hourly_data = _request_json(
                "/v7/weather/24h", {"location": location, "lang": "zh"}
            )
            current = current_data["now"]
            daily = daily_data["daily"][0]
            hourly = hourly_data.get("hourly") or []
            rain_probability = max(
                (int(item.get("pop", 0)) for item in hourly),
                default=0,
            )
            advice = (
                "建议带伞。" if rain_probability >= 40 else "降雨概率不高。"
            )
            summary = (
                f"{location_name}现在{current['text']}，{current['temp']}℃"
                f"（体感{current['feelsLike']}℃），湿度{current['humidity']}%，"
                f"{current['windDir']}{current['windScale']}级。"
                f"今日 {daily['tempMin']}～{daily['tempMax']}℃，"
                f"未来24小时最高降雨概率 {rain_probability}%。{advice}"
            )
            result = {
                "configured": True,
                "available": True,
                "provider": "qweather",
                "source": "和风天气",
                "summary": summary,
                "updated_at": current_data.get("updateTime"),
                "current": {
                    "temperature": float(current["temp"]),
                    "feels_like": float(current["feelsLike"]),
                    "humidity": int(current["humidity"]),
                    "weather": current["text"],
                    "wind_direction": current["windDir"],
                    "wind_scale": current["windScale"],
                    "precipitation": float(current["precip"]),
                },
                "daily": {
                    "temperature_max": float(daily["tempMax"]),
                    "temperature_min": float(daily["tempMin"]),
                    "rain_probability": rain_probability,
                },
            }
        except Exception as error:
            result = {
                "configured": True,
                "available": False,
                "provider": "qweather",
                "summary": "和风天气暂时不可用，室内控制不受影响。",
                "error": _request_error_message(error),
            }
            return _cache_weather_failure(
                _WEATHER_CACHE,
                cache_key,
                result,
                error,
            )
        _WEATHER_CACHE[cache_key] = {
            "saved_at": time.monotonic(),
            "value": result,
        }
        return result
