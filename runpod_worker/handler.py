import base64
import os
import subprocess
import time
import threading
import sys
from typing import Any, Dict, Optional, Tuple

import requests
import runpod

# Minimal imports to check environment if possible, otherwise we rely on subprocess/logs
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


KOKORO_BASE_URL = os.environ.get("KOKORO_BASE_URL", "http://127.0.0.1:8880").rstrip("/")
KOKORO_HEALTH_URL = f"{KOKORO_BASE_URL}/health"
KOKORO_SPEECH_URL = f"{KOKORO_BASE_URL}/v1/audio/speech"

_kokoro_proc: Optional[subprocess.Popen] = None


def log(msg: str):
    """Simple logging helper to ensure flush."""
    print(f"[RunPodWorker] {msg}", flush=True)


def _print_system_diagnostics():
    log("--- System Diagnostics ---")
    
    # 1. Check NVIDIA-SMI
    try:
        log("Running nvidia-smi...")
        smi = subprocess.check_output(["nvidia-smi"], encoding="utf-8")
        print(smi, flush=True)
    except Exception as e:
        log(f"Error running nvidia-smi: {e}")

    # 2. Check PyTorch CUDA
    if TORCH_AVAILABLE:
        try:
            log(f"Torch version: {torch.__version__}")
            cuda_available = torch.cuda.is_available()
            log(f"Torch CUDA Available: {cuda_available}")
            if cuda_available:
                log(f"CUDA Device Count: {torch.cuda.device_count()}")
                log(f"Current Device Name: {torch.cuda.get_device_name(0)}")
            else:
                log("WARNING: Torch detects NO CUDA devices.")
        except Exception as e:
            log(f"Error checking torch: {e}")
    else:
        log("Torch not imported in handler script (might be in venv).")

    # 3. Check logs environment
    log(f"Environment LD_LIBRARY_PATH: {os.environ.get('LD_LIBRARY_PATH', 'Not Set')}")
    log(f"Environment NVIDIA_VISIBLE_DEVICES: {os.environ.get('NVIDIA_VISIBLE_DEVICES', 'Not Set')}")
    log(f"Environment USE_GPU: {os.environ.get('USE_GPU', 'Not Set')}")
    log(f"Environment DEVICE_TYPE: {os.environ.get('DEVICE_TYPE', 'Not Set')}")
    log("-------------------------")


def _stream_logs(process: subprocess.Popen):
    """Reads stdout from the child process and logs it."""
    if process.stdout is None:
        return
    
    # Iterate lines
    for line in iter(process.stdout.readline, b""):
        line_str = line.decode("utf-8", errors="replace").strip()
        if line_str:
            print(f"[KokoroServer] {line_str}", flush=True)


def _is_kokoro_up(timeout_s: float = 1.0) -> bool:
    try:
        r = requests.get(KOKORO_HEALTH_URL, timeout=timeout_s)
        return r.status_code == 200
    except Exception:
        return False


def _start_kokoro_server() -> None:
    """
    Starts Kokoro-FastAPI inside the container with fallback methods.
    """
    global _kokoro_proc
    if _kokoro_proc is not None and _kokoro_proc.poll() is None:
        return

    _print_system_diagnostics()

    # Enforce GPU usage in the environment
    os.environ["USE_GPU"] = "true"
    os.environ["DEVICE_TYPE"] = "cuda"
    os.environ["PYTHONPATH"] = "/app:/app/api"

    log("Starting internal Kokoro-FastAPI server...")
    
    # Try entrypoint script first
    try:
        if os.path.exists("/app/entrypoint.sh"):
            log("Executing /app/entrypoint.sh...")
            os.chmod("/app/entrypoint.sh", 0o755)
            _kokoro_proc = subprocess.Popen(
                ["/app/entrypoint.sh"],
                cwd="/app",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
            )
        else:
            raise FileNotFoundError("entrypoint.sh not found")
            
    except Exception as e:
        log(f"Entrypoint failed or missing: {e}. Falling back to direct uvicorn...")
        # Fallback: Run uvicorn directly using the same command as the other user's stable version
        _kokoro_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.src.main:app", "--host", "0.0.0.0", "--port", "8880"],
            cwd="/app",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
        
    # Start log streaming
    t = threading.Thread(target=_stream_logs, args=(_kokoro_proc,), daemon=True)
    t.start()


def _ensure_kokoro_ready(wait_timeout_s: float = 300.0) -> None:
    """Waits for the Kokoro server to be healthy, allowing time for model downloads."""
    if _is_kokoro_up():
        return

    _start_kokoro_server()

    log("Waiting for Kokoro server to be healthy (this may take a few minutes if seeds are being downloaded)...")
    start = time.time()
    while time.time() - start < wait_timeout_s:
        if _kokoro_proc is not None:
            ret = _kokoro_proc.poll()
            if ret is not None:
                log(f"CRITICAL: Kokoro server process exited unexpectedly with code {ret}")
                # Log last few lines if possible
                raise RuntimeError(f"Kokoro server process exited during startup with code {ret}")
        
        if _is_kokoro_up(timeout_s=2.0):
            log("Kokoro server is ready!")
            return
        
        # Sleep slightly longer to avoid spamming logs while waiting for massive downloads
        time.sleep(2.0)

    log("Timed out waiting for Kokoro server to start.")
    raise TimeoutError("Timed out waiting for Kokoro server to become ready")


# Global session for connection pooling
_session = requests.Session()


def _call_kokoro_openai_speech(payload: Dict[str, Any]) -> Tuple[bytes, str]:
    # Force non-streaming for queue-based mode.
    payload = dict(payload)
    payload["stream"] = False

    t0 = time.time()
    r = _session.post(KOKORO_SPEECH_URL, json=payload, timeout=300)
    dur = time.time() - t0
    
    if r.status_code != 200:
        log(f"Kokoro API Error ({r.status_code}): {r.text}")
        raise ValueError(f"Kokoro error {r.status_code}: {r.text}")
    
    mime = r.headers.get("content-type", "application/octet-stream")
    log(f"Generated audio in {dur:.2f}s | Size: {len(r.content)} bytes | Mime: {mime}")
    return r.content, mime


def handler(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    RunPod queue-based handler.
    """
    log(f"Received jobID: {job.get('id')}")
    
    if not isinstance(job, dict):
        raise ValueError("job must be a dict")
    job_input = job.get("input")
    if not isinstance(job_input, dict):
        raise ValueError("job['input'] must be an object")

    # Accept both OpenAI-compatible `input` and a more intuitive alias `text`.
    text = job_input.get("input") or job_input.get("text")
    if not text:
        raise ValueError("Missing required field: input (or 'text' alias)")
    
    # Ensure background server is running
    _ensure_kokoro_ready()

    # Prepare payload
    kokoro_payload = dict(job_input)
    kokoro_payload["input"] = text
    kokoro_payload.pop("text", None)
    kokoro_payload.setdefault("model", "kokoro")

    # Call internal API
    audio_bytes, mime_type = _call_kokoro_openai_speech(kokoro_payload)

    response_format = job_input.get("response_format", "mp3")
    return {
        "audio_base64": base64.b64encode(audio_bytes).decode("utf-8"),
        "mime_type": mime_type,
        "format": response_format,
        "sample_rate": 24000,
    }


# Ensure background server is running during provision
_ensure_kokoro_ready()

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
