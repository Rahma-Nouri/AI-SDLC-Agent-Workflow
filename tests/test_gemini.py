import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_GEMINI_TESTS") != "1",
    reason="Live Gemini check is opt-in; set RUN_LIVE_GEMINI_TESTS=1 to run it.",
)


def test_gemini_live_smoke():
    from dotenv import load_dotenv
    from google import genai

    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    assert api_key, "GEMINI_API_KEY was not found."

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        contents="Explain in one sentence what an AI agent is.",
    )

    assert response.text and response.text.strip()