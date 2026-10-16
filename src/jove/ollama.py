"""The two Ollama calls the OCR passes share: read an image, and free the GPU."""

import base64
import logging

import httpx

log = logging.getLogger(__name__)


def chat(base_url: str, img, model: str, prompt: str, max_tokens: int, timeout: float) -> str:
    """One image + prompt to an Ollama vision model; its reply text."""
    import cv2

    ok, png = cv2.imencode(".png", img)
    resp = httpx.post(f"{base_url}/api/chat", timeout=timeout, json={
        "model": model, "stream": False, "think": False,
        "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 8192},
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(png.tobytes()).decode()]}],
    })
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "")


def unload(base_url: str, models: list[str]) -> None:
    """Ask Ollama to drop models from memory now (keep_alive 0), freeing the GPU for the chooser."""
    for model in dict.fromkeys(models):
        try:
            httpx.post(f"{base_url}/api/generate", timeout=30, json={"model": model, "keep_alive": 0})
        except httpx.HTTPError as e:
            log.info("could not unload %s: %s", model, e)
