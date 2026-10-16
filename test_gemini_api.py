import os
from google import genai

api_key = os.getenv("GEMINI_API_KEY", "").strip()

if not api_key:
    raise SystemExit(
        "ERROR: Python cannot read GEMINI_API_KEY. "
        "Set it in the same terminal before running this script."
    )

client = genai.Client(api_key=api_key)

# Use a lightweight model for the connectivity test.
model = "gemini-3.8-flash"

try:
    response = client.models.generate_content(
        model=model,
        contents="Reply with exactly: GEMINI_API_TEST_SUCCESS"
    )

    print("API request succeeded.")
    print("Model:", model)
    print("Response:", response.text)

except Exception as exc:
    print("API request failed.")
    print("Error type:", type(exc).__name__)
    print("Error:", str(exc))