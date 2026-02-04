from mcp.server.fastmcp import FastMCP
import httpx
import asyncio

# 创建 MCP 服务器
mcp = FastMCP("Weather Service")

@mcp.tool()
async def get_weather(city: str) -> str:
    """
    Get the current weather for a specific city using Open-Meteo API.
    Args:
        city: The name of the city (e.g., "Beijing", "New York", "London")
    Returns:
        A string containing the weather information (temperature, humidity, wind speed).
    """
    async with httpx.AsyncClient() as client:
        try:
            # 1. Geocoding - Find coordinates for the city
            geo_url = "https://geocoding-api.open-meteo.com/v1/search"
            geo_params = {
                "name": city,
                "count": 1,
                "language": "en", # Or 'zh' if preferred, but 'en' is safer for international cities
                "format": "json"
            }
            
            # Add timeout to avoid hanging
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
            
            # 2. Weather - Get current weather data
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
            
            # Simple weather code interpretation (can be expanded)
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
    # fastmcp run by default uses stdio
    mcp.run()
