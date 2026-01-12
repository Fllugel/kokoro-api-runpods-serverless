## RunPod Serverless (Queue-Based) Kokoro Worker

This folder contains a **queue-based RunPod Serverless worker** that uses the official **Kokoro-FastAPI Docker image** and accepts **OpenAI-compatible speech JSON** (non-streaming), returning **base64 audio**.

### What you call (important)

Queue-based RunPod endpoints do **not** expose `/v1/audio/speech` as a URL path. Clients call RunPod’s fixed endpoints:
- `POST https://api.runpod.ai/v2/{ENDPOINT_ID}/run`
- `POST https://api.runpod.ai/v2/{ENDPOINT_ID}/runsync`

But the payload you pass under `input` is the same shape as Kokoro-FastAPI’s OpenAI-compatible `/v1/audio/speech` request.

### Input / Output

- **Input**: `job["input"]` must match `OpenAISpeechRequest` from Kokoro-FastAPI.
- **Constraint**: `stream` must be `false` (queue-based is single-response).
- **Output**: JSON:
  - `audio_base64`: base64-encoded bytes
  - `mime_type`: e.g. `audio/mpeg`
  - `format`: e.g. `mp3`
  - `sample_rate`: e.g. `24000`

### Local testing (RunPod SDK)

**Recommended:** Docker-based testing (matches RunPod deployment).

RunPod supports running the handler with a local HTTP test server:

```bash
python runpod_worker/handler.py --rp_serve_api
```

Then call:

```bash
curl -X POST http://localhost:8000/runsync ^
  -H "Content-Type: application/json" ^
  -d "{\"input\": {\"model\":\"kokoro\",\"input\":\"Hello world!\",\"voice\":\"af_bella\",\"response_format\":\"mp3\",\"speed\":1.0,\"stream\":false}}"
```

**Alternative: Docker-based testing**
```bash
# Build and test
docker build -f runpod_worker/Dockerfile.cpu -t kokoro-runpod-test .
docker run --rm kokoro-runpod-test python handler.py --test_input '{"input": {"model":"kokoro","input":"Hello world!","voice":"af_bella","response_format":"mp3","speed":1.0,"stream":false}}'
```

Docs: `https://docs.runpod.io/serverless/development/local-testing`

### Build + run locally with Docker

CPU:

```bash
docker build -f runpod_worker/Dockerfile.cpu -t kokoro-runpod-worker:cpu .
docker run --rm -p 8000:8000 kokoro-runpod-worker:cpu
```

GPU (requires NVIDIA Docker runtime):

```bash
docker build -f runpod_worker/Dockerfile.gpu -t kokoro-runpod-worker:gpu .
docker run --rm --gpus all -p 8000:8000 kokoro-runpod-worker:gpu
```

### Deploy to RunPod

#### 1) Build + push images

You will typically publish **two images** (CPU + GPU) so you can create separate RunPod endpoints.

CPU image:

```bash
docker build -f runpod_worker/Dockerfile.cpu -t YOUR_DOCKER_USER/kokoro-runpod-worker:cpu .
docker push YOUR_DOCKER_USER/kokoro-runpod-worker:cpu
```

GPU image:

```bash
docker build -f runpod_worker/Dockerfile.gpu -t YOUR_DOCKER_USER/kokoro-runpod-worker:gpu .
docker push YOUR_DOCKER_USER/kokoro-runpod-worker:gpu
```

#### Alternative: Build + push automatically from GitHub (recommended)

This repo includes a GitHub Actions workflow: `.github/workflows/publish-ghcr.yml`

If you push this repo to GitHub, it will publish images to **GitHub Container Registry (GHCR)**:
- `ghcr.io/<OWNER>/<REPO>:cpu`
- `ghcr.io/<OWNER>/<REPO>:gpu`

Steps:
1. Push to GitHub (default branch `main`).
2. Go to the repo → **Actions** → run `publish-ghcr` (or push to `main`).
3. After it runs, use the image tags above in the RunPod endpoint “Container Image”.

#### 2) Create RunPod Serverless endpoints

Create **two endpoints** (recommended):

- **CPU endpoint**
  - **Type**: Serverless → Queue-Based
  - **Container Image**: `YOUR_DOCKER_USER/kokoro-runpod-worker:cpu`
  - **Min workers**: `0`
  - **Max workers**: start `1–3`
  - **Idle timeout**: `30–60s`
  - **Container disk**: start `20GB+` (leave headroom for image layers + model assets)
  - **FlashBoot**: enabled

- **GPU endpoint**
  - **Type**: Serverless → Queue-Based
  - **Container Image**: `YOUR_DOCKER_USER/kokoro-runpod-worker:gpu`
  - Choose a **GPU** in the endpoint configuration (any supported NVIDIA GPU)
  - **Min workers**: `0`
  - **Max workers**: start `1–3`
  - **Idle timeout**: `30–60s`
  - **Container disk**: start `30GB+`
  - **FlashBoot**: enabled

Notes:
- This worker starts the Kokoro-FastAPI server **inside the container** and calls it over `http://127.0.0.1:8880`.
- If you want to override the internal URL, set `KOKORO_BASE_URL` env var (rare; default is correct).

RunPod docs:
- `https://docs.runpod.io/tutorials/sdks/python/101/hello#create-a-basic-serverless-function`
- `https://docs.runpod.io/serverless/endpoints/overview`

### Calling the endpoint (RunPod /runsync and /run)

RunPod gives you a base URL like:

- `https://api.runpod.ai/v2/{ENDPOINT_ID}`

You call either:
- **Synchronous** (best for short requests): `POST .../runsync`
- **Asynchronous** (best for long requests): `POST .../run` then poll `.../status/{job_id}`

#### Synchronous request example (/runsync)

The request body must be:
- `{"input": { ... } }`

Where `input` is compatible with Kokoro-FastAPI’s OpenAI-style `/v1/audio/speech` JSON. Important: set `stream: false`.

```bash
curl -X POST "https://api.runpod.ai/v2/{ENDPOINT_ID}/runsync" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "model": "kokoro",
      "input": "Hello world!",
      "voice": "af_bella",
      "response_format": "mp3",
      "speed": 1.0,
      "stream": false
    }
  }'
```

The result (inside RunPod’s response envelope) will contain:
- `audio_base64`
- `mime_type`
- `format`
- `sample_rate`

#### Python client example (decode audio)

```python
import base64
import requests

RUNPOD_API_KEY = "YOUR_KEY"
ENDPOINT_ID = "YOUR_ENDPOINT_ID"

resp = requests.post(
    f"https://api.runpod.ai/v2/{ENDPOINT_ID}/runsync",
    headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
    json={
        "input": {
            "model": "kokoro",
            "input": "Hello world!",
            "voice": "af_bella",
            "response_format": "mp3",
            "speed": 1.0,
            "stream": False,
        }
    },
    timeout=300,
)
resp.raise_for_status()
payload = resp.json()

# RunPod wraps outputs; depending on SDK/version you’ll see `output` or `output` inside `output`.
out = payload.get("output") or payload
audio_b64 = out["audio_base64"]
audio_bytes = base64.b64decode(audio_b64.encode("utf-8"))

with open("out.mp3", "wb") as f:
    f.write(audio_bytes)
print("wrote out.mp3", len(audio_bytes))
```

#### Async request example (/run + /status)

```bash
curl -X POST "https://api.runpod.ai/v2/{ENDPOINT_ID}/run" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input":{"model":"kokoro","input":"Hello","voice":"af_bella","response_format":"mp3","speed":1.0,"stream":false}}'
```

This returns a `job_id`. Poll:

```bash
curl -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  "https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{JOB_ID}"
```

