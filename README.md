## RunPod Kokoro Serverless Worker (Queue-Based)

This repo contains a **queue-based RunPod Serverless worker** that runs Kokoro via the official Kokoro-FastAPI Docker images and returns **base64 audio**.

### How it works
- The container starts Kokoro-FastAPI inside the worker (local process).
- The worker calls Kokoro locally at `http://127.0.0.1:8880/v1/audio/speech`.
- RunPod job output is JSON: `{ audio_base64, mime_type, format, sample_rate }`.

### Files
- `runpod_worker/handler.py`: RunPod handler (queue-based)
- `runpod_worker/Dockerfile.cpu`: CPU worker image (extends `ghcr.io/remsky/kokoro-fastapi-cpu`)
- `runpod_worker/Dockerfile.gpu`: GPU worker image (extends `ghcr.io/remsky/kokoro-fastapi-gpu`)
- `runpod_worker/README.md`: Full usage, RunPod deployment, and request examples

### Deploying
Follow the detailed guide in `runpod_worker/README.md`.

### References
- `https://docs.runpod.io/tutorials/sdks/python/101/hello#create-a-basic-serverless-function`
- `https://docs.runpod.io/serverless/endpoints/overview`
- Kokoro upstream: `https://github.com/remsky/Kokoro-FastAPI`

