# 中文模块说明：MCP 工具服务入口，为外部智能体调用项目提供的受限工具定义服务端协议。
from mcp.server.fastmcp import FastMCP
import httpx
import asyncio

# 创建 MCP 工具服务器，并把天气查询以工具形式暴露给兼容 MCP 的客户端。
mcp = FastMCP("Weather Service")

@mcp.tool()
async def get_weather(city: str) -> str:
    """通过 Open-Meteo 查询城市天气。

    查询分为两步：先把城市名称解析成经纬度，再按坐标读取当前温度、湿度和风速。
    Args:
        city: 城市名称，例如“北京”或“London”。
    Returns:
        可读的天气摘要；找不到地点或请求失败时返回错误说明字符串。
    """
    async with httpx.AsyncClient() as client:
        try:
            # 第一步：调用地理编码 API，把用户输入的城市名转换为唯一地点坐标。
            geo_url = "https://geocoding-api.open-meteo.com/v1/search"
            geo_params = {
                "name": city,
                "count": 1,
                "language": "en", # 使用英文地点名，减少国际城市的拼写歧义。
                "format": "json"
            }
            
            # 网络请求均配置超时，避免 MCP 工具因外部服务无响应而一直占用调用。
            geo_resp = await client.get(geo_url, params=geo_params, timeout=10.0)
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()
            
            if not geo_data.get("results"):
                return f"Error: Could not find location for '{city}'"
                
            location = geo_data["results"][0]
            lat = location["latitude"]
            lon = location["longitude"]
            name = location["name"]
            country = location.get("country", "")
            
            # 第二步：使用上一步的经纬度请求当前天气字段，时区由 Open-Meteo 自动推断。
            weather_url = "https://api.open-meteo.com/v1/forecast"
            weather_params = {
                "latitude": lat,
                "longitude": lon,
                "current": ["temperature_2m", "relative_humidity_2m", "weather_code", "wind_speed_10m"],
                "timezone": "auto"
            }
            
            weather_resp = await client.get(weather_url, params=weather_params, timeout=10.0)
            weather_resp.raise_for_status()
            weather_data = weather_resp.json()
            
            if "current" not in weather_data:
                return f"Error: Could not retrieve weather data for '{name}'"
                
            current = weather_data["current"]
            temp = current["temperature_2m"]
            humidity = current["relative_humidity_2m"]
            wind_speed = current["wind_speed_10m"]
            
            # 天气代码暂保留在响应解析中；当前摘要展示温度、湿度和风速，后续可映射代码为天气描述。
            weather_code = current["weather_code"]
            
            return (
                f"Weather in {name}, {country}:\n"
                f"Temperature: {temp}°C\n"
                f"Humidity: {humidity}%\n"
                f"Wind Speed: {wind_speed} km/h\n"
            )
            
        except httpx.RequestError as e:
            return f"Network error occurred while fetching weather data: {str(e)}"
        except Exception as e:
            return f"An unexpected error occurred: {str(e)}"

if __name__ == "__main__":
    # MCP 默认使用标准输入/输出传输协议，便于本地 MCP Host 启动并收发工具请求。
    mcp.run()
