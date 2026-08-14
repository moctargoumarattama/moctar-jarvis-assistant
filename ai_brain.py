import os
from functools import lru_cache

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv():
        return False
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


DEFAULT_MODEL = "gpt-4o-mini"


def get_openai_api_key():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to your environment or a local .env file."
        )
    return api_key


@lru_cache(maxsize=1)
def get_openai_client():
    if OpenAI is None:
        raise RuntimeError("openai package is missing")
    return OpenAI(api_key=get_openai_api_key())


def ask_gpt(prompt):
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    response = get_openai_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Tu es Jarvis, assistant intelligent et rapide."},
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content


def safe_ask_gpt(prompt, fallback="Je ne peux pas utiliser l'IA pour le moment."):
    try:
        return ask_gpt(prompt)
    except Exception:
        return fallback
