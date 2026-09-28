import io

import pytest
from PIL import Image

SCHEMA = {
    "type": "object",
    "properties": {
        "client": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Client's full legal name"},
                "dob": {"type": "string", "format": "date"},
            },
        },
        "claim_amount": {"type": "number"},
        "parties": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            },
        },
    },
}


def png(width=50, height=70) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def pdf(pages: int) -> bytes:
    frames = [Image.new("RGB", (60, 80), "white") for _ in range(pages)]
    buf = io.BytesIO()
    frames[0].save(buf, format="PDF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


@pytest.fixture
def schema():
    return SCHEMA
