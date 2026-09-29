import os
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from google import genai
from google.genai import errors

from chatbot_config import SYSTEM_PROMPT, MODEL_NAME

load_dotenv()

app = Flask(__name__)

api_key = os.getenv("GEMINI_API_KEY")
if not api_key or api_key == "your_gemini_api_key_here":
    raise RuntimeError("GEMINI_API_KEY is missing or not configured.")

client = genai.Client(api_key=api_key)

# Temporary Gemini capacity/rate-limit errors are safe to retry.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 1.5


def generate_with_retry(prompt):
    """Generate a Gemini response with exponential backoff for transient errors."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config={
                    "system_instruction": SYSTEM_PROMPT,
                    "temperature": 0.3,
                },
            )
        except errors.APIError as exc:
            status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)

            if status_code not in RETRYABLE_STATUS_CODES or attempt >= MAX_RETRIES:
                raise

            delay = INITIAL_BACKOFF_SECONDS * (2 ** attempt)
            app.logger.warning(
                "Gemini returned %s. Retrying in %.1f seconds (attempt %s/%s).",
                status_code,
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)

    raise RuntimeError("Gemini request could not be completed.")


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []

    if not message:
        return jsonify({"error": "Please enter a question."}), 400

    recent_history = history[-10:]
    conversation = []

    for item in recent_history:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            conversation.append(f"{role.upper()}: {content}")

    conversation.append(f"USER: {message}")
    prompt = "\n".join(conversation)

    try:
        response = generate_with_retry(prompt)
        answer = (response.text or "").strip()

        if not answer:
            answer = "I couldn't generate a response. Please try your study question again."

        return jsonify({"answer": answer})

    except errors.APIError as exc:
        status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        app.logger.exception("Gemini API request failed with status %s", status_code)

        if status_code == 429:
            message = "Gemini is temporarily rate-limited. Please wait a moment and try again."
        elif status_code in {500, 502, 503, 504}:
            message = "Gemini is temporarily busy. Please try again in a few seconds."
        else:
            message = "The Gemini API could not process the request. Please check your API configuration and try again."

        return jsonify({"error": message}), 503

    except Exception:
        app.logger.exception("Unexpected Gemini request failure")
        return jsonify(
            {"error": "The AI service is temporarily unavailable. Please try again."}
        ), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
