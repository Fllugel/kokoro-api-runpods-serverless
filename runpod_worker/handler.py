import base64
import os
import subprocess
import time
from typing import Any, Dict, Optional, Tuple

import requests
import runpod


KOKORO_BASE_URL = os.environ.get("KOKORO_BASE_URL", "http://127.0.0.1:8880").rstrip("/")
KOKORO_HEALTH_URL = f"{KOKORO_BASE_URL}/health"
KOKORO_SPEECH_URL = f"{KOKORO_BASE_URL}/v1/audio/speech"

_kokoro_proc: Optional[subprocess.Popen] = None


def _is_kokoro_up(timeout_s: float = 1.0) -> bool:
    try:
        r = requests.get(KOKORO_HEALTH_URL, timeout=timeout_s)
        return r.status_code == 200
    except Exception:
        return False


def _start_kokoro_server() -> None:
    """
    Starts Kokoro-FastAPI inside the container.

    We assume the container image already contains Kokoro-FastAPI and an `entrypoint.sh`
    that launches uvicorn on port 8880 (this is true for the official images).
    """
    global _kokoro_proc
    if _kokoro_proc is not None and _kokoro_proc.poll() is None:
        return

    # Best-effort: if the base image provides /app/entrypoint.sh, run it.
    # It will block, so we run it as a subprocess.
    _kokoro_proc = subprocess.Popen(
        ["./entrypoint.sh"],
        cwd="/app",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )


def _ensure_kokoro_ready(wait_timeout_s: float = 120.0) -> None:
    if _is_kokoro_up():
        return

    _start_kokoro_server()

    start = time.time()
    while time.time() - start < wait_timeout_s:
        if _kokoro_proc is not None and _kokoro_proc.poll() is not None:
            raise RuntimeError("Kokoro server process exited during startup")
        if _is_kokoro_up(timeout_s=1.0):
            return
        time.sleep(0.5)

    raise TimeoutError("Timed out waiting for Kokoro server to become ready")


def _call_kokoro_openai_speech(payload: Dict[str, Any]) -> Tuple[bytes, str]:
    # Force non-streaming for queue-based mode.
    payload = dict(payload)
    payload["stream"] = False

    r = requests.post(KOKORO_SPEECH_URL, json=payload, timeout=300)
    if r.status_code != 200:
        raise ValueError(f"Kokoro error {r.status_code}: {r.text}")
    mime = r.headers.get("content-type", "application/octet-stream")
    return r.content, mime


def handler(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    RunPod queue-based handler.

    Input: job['input'] should look like Kokoro-FastAPI's OpenAI-compatible /v1/audio/speech JSON.
    Output: base64 audio + mime type in JSON.
    """
    if not isinstance(job, dict):
        raise ValueError("job must be a dict")
    job_input = job.get("input")
    if not isinstance(job_input, dict):
        raise ValueError("job['input'] must be an object")

    # Basic required fields (let Kokoro perform full validation).
    if not job_input.get("input"):
        raise ValueError("Missing required field: input")
    if not job_input.get("voice"):
        raise ValueError("Missing required field: voice")

    _ensure_kokoro_ready()
    audio_bytes, mime_type = _call_kokoro_openai_speech(job_input)

    response_format = job_input.get("response_format", "mp3")
    return {
        "audio_base64": base64.b64encode(audio_bytes).decode("utf-8"),
        "mime_type": mime_type,
        "format": response_format,
        "sample_rate": 24000,
    }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

