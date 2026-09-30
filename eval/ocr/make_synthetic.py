"""Render synthetic Ontario legal pages and degrade them like real captures.

Ground truth is exact because we wrote the text. Deterministic (fixed seeds),
so the benchmark is reproducible:  uv run python eval/ocr/make_synthetic.py
"""

import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "synthetic"
FONTS = {
    "sans": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "serif": "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "mono": "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "italic": "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    "narrow": "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Regular.ttf",
    "serif_bold": "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
}

DOCS = {
    "intake_form": ("sans", """HARPER & LEUNG LLP - CLIENT INTAKE FORM
File No. 24-0117   Date: March 12, 2024

CLIENT INFORMATION
Full name: Jane Alexandra Doe   Date of birth: 1980-04-03
Address: 22 King St W, Unit 4, Toronto ON M5H 1A1
Phone: (416) 555-0199   Email: jane.doe@example.com
ID: Ontario driver's licence D6101-40706-60905, expires 2027-04-03

MATTER
Type: Landlord and tenant - return of deposit
Opposing party: Acme Property Management Inc. (landlord)
Opposing counsel: Smith & Co. LLP, attn. Ms. Chen
Amount claimed: $2,450.00 plus interest

Referral source: Law Society Referral Service"""),
    "demand_letter": ("serif", """Smith & Co. LLP
Barristers and Solicitors
1200 Bay Street, Suite 300, Toronto, Ontario M5R 2A5

WITHOUT PREJUDICE

September 4, 2024

Ms. Jane Doe
22 King Street West, Unit 4
Toronto, ON M5H 1A1

Re: Unpaid rent - 22 King Street West, Unit 4
Our File: ABC-99

Dear Ms. Doe:

We act for Acme Property Management Inc. Our client advises that rent
of $2,450 for each of July and August 2024 remains unpaid, for a total
of $4,900.00. Unless payment is received by September 30, 2024, our
client will file an application with the Landlord and Tenant Board.

Yours truly,
Smith & Co. LLP
per: Wei Chen"""),
    "court_endorsement": ("mono", """ONTARIO SUPERIOR COURT OF JUSTICE
Court File No. CV-24-00712345-0000

BETWEEN:
JANE ALEXANDRA DOE          Plaintiff
- and -
ACME PROPERTY MANAGEMENT INC.   Defendant

ENDORSEMENT
Before: Justice R. Morgan
Heard: November 18, 2024

The motion for an interim order is granted.
The defendant shall pay costs fixed at $3,500
within 30 days. Pre-trial conference is set
for February 10, 2025 at 10:00 a.m."""),
    "pay_stub": ("narrow", """ACME LOGISTICS INC.            PAY STATEMENT
Employee: John Smith            Employee ID: 004417
Position: Warehouse Supervisor  Pay period: 2024-06-01 to 2024-06-15

EARNINGS              HOURS     RATE       AMOUNT
Regular               80.00     27.50    2,200.00
Overtime               6.50     41.25      268.13
Vacation pay                                98.73
GROSS PAY                                2,566.86

DEDUCTIONS
CPP                                        140.23
EI                                          42.10
Income tax                                 388.51
NET PAY                                  1,996.02"""),
    "ltb_notice": ("serif_bold", """Notice to End your Tenancy
For Non-payment of Rent (Form N4)

To: Jane Doe (Tenant)
From: Acme Property Management Inc. (Landlord)
Address of the rental unit: 22 King St W, Unit 4, Toronto ON M5H 1A1

This is a legal notice that could lead to you being evicted.
You owe $4,900.00 in rent. The termination date is October 15, 2024.
You must pay this amount by the termination date or move out.

Landlord and Tenant Board: 1-888-332-3234"""),
    "handwritten_style_note": ("italic", """Client meeting notes - J. Doe
Oct 2, 2024
- Moved out Aug 31, keys returned to super (Raj)
- Landlord kept last month's rent deposit $2,450
- Photos of unit taken on move-out day
- Wants deposit back + interest
- Follow up: request ledger from Acme by Oct 16"""),
}


def render(text: str, font: str, size: int = 34) -> tuple[Image.Image, list[str]]:
    f = ImageFont.truetype(FONTS[font], size)
    lines = text.split("\n")
    width, height = 1700, max(2200, 120 + len(lines) * int(size * 1.6))
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    y = 120
    for line in lines:
        d.text((130, y), line, fill="black", font=f)
        y += int(size * 1.6)
    return img, [l for l in lines if l.strip()]


def photo(img: Image.Image, rng: random.Random, strength: float = 1.0) -> Image.Image:
    """Perspective + rotation + blur + uneven light + noise + JPEG, on a coloured table."""
    a = np.array(img)
    h, w = a.shape[:2]
    pad = int(0.12 * w)
    canvas = np.full((h + 2 * pad, w + 2 * pad, 3), (60, 40, 120), np.uint8)  # reddish table
    canvas[pad:pad + h, pad:pad + w] = a
    H, W = canvas.shape[:2]
    j = lambda: rng.uniform(-1, 1) * 0.06 * W * strength
    src = np.float32([[pad, pad], [pad + w, pad], [pad + w, pad + h], [pad, pad + h]])
    dst = np.float32([[pad + j(), pad + j()], [pad + w + j(), pad + j()], [pad + w + j(), pad + h + j()], [pad + j(), pad + h + j()]])
    M = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(canvas, M, (W, H), borderValue=(60, 40, 120))
    angle = rng.uniform(-4, 4) * strength
    R = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)
    out = cv2.warpAffine(out, R, (W, H), borderValue=(60, 40, 120))
    k = 1 + 2 * int(round(2 * strength))
    out = cv2.GaussianBlur(out, (k, k), 0)
    yy, xx = np.mgrid[0:H, 0:W]
    light = 0.55 + 0.45 * np.exp(-(((xx - W * 0.3) / W) ** 2 + ((yy - H * 0.3) / H) ** 2) * 2)
    out = np.clip(out * light[..., None] * rng.uniform(0.7, 1.0), 0, 255)
    out = np.clip(out + np.random.default_rng(rng.randint(0, 99)).normal(0, 6 * strength, out.shape), 0, 255).astype(np.uint8)
    out = cv2.resize(out, (int(W * 0.6), int(H * 0.6)), interpolation=cv2.INTER_AREA)
    ok, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return Image.fromarray(cv2.cvtColor(cv2.imdecode(enc, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB))


def shake(img: Image.Image, rng: random.Random) -> Image.Image:
    """Hand-shake double exposure plus motion blur, dim and small: like a bar-lit phone photo."""
    a = np.array(img).astype(np.float32)
    k = np.zeros((31, 31), np.float32)
    angle = rng.uniform(0, np.pi)
    length = rng.randint(8, 14)
    for t in np.linspace(-1, 1, 2 * length):
        k[int(15 + t * length / 2 * np.sin(angle)), int(15 + t * length / 2 * np.cos(angle))] += 1
    k[int(15 + length / 2 * np.sin(angle)), int(15 + length / 2 * np.cos(angle))] += length * 0.8  # ghost at one end
    k /= k.sum()
    a = cv2.filter2D(a, -1, k)
    out = photo(Image.fromarray(a.clip(0, 255).astype(np.uint8)), rng, 0.8)
    o = np.array(out).astype(np.float32) * rng.uniform(0.6, 0.8)
    return Image.fromarray(o.clip(0, 255).astype(np.uint8))


def fax(img: Image.Image, rng: random.Random) -> Image.Image:
    small = img.convert("L").resize((img.width // 2, img.height // 2), Image.BILINEAR)
    a = np.array(small).astype(np.float32)
    a += np.random.default_rng(rng.randint(0, 99)).normal(0, 20, a.shape)
    a = np.where(a > 150, 255, 0).astype(np.uint8)
    return Image.fromarray(a).rotate(rng.uniform(-1.5, 1.5), fillcolor=255, expand=True).convert("RGB")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for i, (name, (font, text)) in enumerate(DOCS.items()):
        rng = random.Random(i)
        img, lines = render(text, font)
        variants = {
            "scan": img,
            "photo": photo(img, rng, 1.0),
            "fax": fax(img, rng),
            "hard": photo(img, rng, 1.6),
            "shake": shake(render(text, font, size=26)[0], rng),
        }
        for v, im in variants.items():
            stem = f"{name}.{v}"
            im.save(OUT / f"{stem}.jpg", quality=90)
            (OUT / f"{stem}.txt").write_text("\n".join(lines) + "\n")
    print(f"wrote {len(DOCS) * 5} images to {OUT}")


if __name__ == "__main__":
    main()
