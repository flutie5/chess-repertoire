"""Generate Opening Explorer wordmark PNG with transparent background."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "webapp" / "static" / "opening-explorer-logo.png"
ASSETS = Path(
    r"C:\Users\fluti\.cursor\projects\c-Users-fluti-2000\assets\opening-explorer-logo.png"
)

W, H = 1600, 400
WHITE = (255, 255, 255, 255)
BLUE = (77, 143, 217, 255)  # #4d8fd9


def main() -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    candidates = [
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\calibrib.ttf"),
        Path(r"C:\Windows\Fonts\seguisb.ttf"),
    ]
    font_path = next((p for p in candidates if p.exists()), None)
    if font_path is None:
        raise SystemExit("No bold TTF found")

    text1, text2 = "Opening ", "Explorer"
    size = 140
    font = ImageFont.truetype(str(font_path), size)
    while size > 40:
        font = ImageFont.truetype(str(font_path), size)
        b1 = draw.textbbox((0, 0), text1, font=font)
        b2 = draw.textbbox((0, 0), text2, font=font)
        tw = (b1[2] - b1[0]) + (b2[2] - b2[0])
        th = max(b1[3] - b1[1], b2[3] - b2[1])
        if tw <= W - 80 and th <= H - 40:
            break
        size -= 4

    b1 = draw.textbbox((0, 0), text1, font=font)
    w1 = b1[2] - b1[0]
    h1 = b1[3] - b1[1]
    b2 = draw.textbbox((0, 0), text2, font=font)
    w2 = b2[2] - b2[0]
    total_w = w1 + w2
    x = (W - total_w) // 2
    y = (H - h1) // 2 - b1[1]

    draw.text((x, y), text1, font=font, fill=WHITE)
    draw.text((x + w1, y), text2, font=font, fill=BLUE)

    bbox = img.getbbox()
    if bbox:
        pad = 24
        l, t, r, b = bbox
        img = img.crop((
            max(0, l - pad),
            max(0, t - pad),
            min(W, r + pad),
            min(H, b + pad),
        ))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG")
    ASSETS.parent.mkdir(parents=True, exist_ok=True)
    img.save(ASSETS, "PNG")
    print(f"saved {OUT} {img.size} {img.mode}")
    print(f"saved {ASSETS}")


if __name__ == "__main__":
    main()
