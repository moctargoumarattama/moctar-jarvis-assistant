import os

import requests


def get_weather_api_key():
    api_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENWEATHER_API_KEY is missing.")
    return api_key


def get_weather(city_name):
    response = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={
            "q": city_name,
            "appid": get_weather_api_key(),
            "units": "metric",
        },
        timeout=10,
    )
    data = response.json()

    if data.get("cod") != 200:
        print("Error: City not found")
        return None

    weather_description = data["weather"][0]["description"]
    temperature = data["main"]["temp"]
    humidity = data["main"]["humidity"]
    wind_speed = data["wind"]["speed"]

    print(f"Weather in {city_name}:")
    print(f"Description: {weather_description}")
    print(f"Temperature: {temperature} C")
    print(f"Humidity: {humidity}%")
    print(f"Wind Speed: {wind_speed} m/s")
    return temperature
