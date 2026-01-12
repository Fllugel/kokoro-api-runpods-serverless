import base64

from runpod_worker.handler import handler


def main() -> None:
    job = {
        "input": {
            "model": "kokoro",
            "input": "Hello world!",
            "voice": "af_bella",
            "response_format": "mp3",
            "speed": 1.0,
            "stream": False,
        }
    }

    out = handler(job)
    audio_b64 = out["audio_base64"]
    audio_bytes = base64.b64decode(audio_b64.encode("utf-8"))

    with open("output.mp3", "wb") as f:
        f.write(audio_bytes)

    print(
        {
            "mime_type": out.get("mime_type"),
            "format": out.get("format"),
            "sample_rate": out.get("sample_rate"),
            "bytes": len(audio_bytes),
        }
    )


if __name__ == "__main__":
    main()

