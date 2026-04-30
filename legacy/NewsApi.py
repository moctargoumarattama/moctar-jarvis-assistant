import os

import pyttsx3
import requests


def get_news_api_key():
    api_key = os.getenv("NEWS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("NEWS_API_KEY is missing.")
    return api_key


def fetch_news(source):
    response = requests.get(
        "https://newsapi.org/v2/top-headlines",
        params={"sources": source, "apiKey": get_news_api_key()},
        timeout=10,
    )
    return response.json()


def speak(text):
    engine = pyttsx3.init()
    voices = engine.getProperty("voices")
    engine.setProperty("voice", voices[1].id)
    engine.say(text)
    engine.runAndWait()


def main():
    news_source = "the-hindu"
    news_data = fetch_news(news_source)

    if news_data["status"] == "ok":
        articles = news_data["articles"]
        if articles:
            speak("Here are the latest headlines.")
            for index, article in enumerate(articles, 1):
                title = article["title"]
                description = article["description"]
                speak(f"Headline {index}: {title}. {description}")
        else:
            speak("No news articles found.")
    else:
        speak("Sorry, I couldn't fetch the news at the moment.")


if __name__ == "__main__":
    main()
