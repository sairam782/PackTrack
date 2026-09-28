"""Generate crisp QR labels and a printable test sheet."""
import json
from pathlib import Path
import zipfile

from PIL import Image, ImageDraw, ImageFont
import qrcode

ROOT = Path(__file__).resolve().parents[1]
LABELS = [
    {"box_id": "TEST-001", "supplier": "Acme Components", "part": "M6 Bolts"},
    {"box_id": "TEST-002", "supplier": "Acme Components", "part": "M8 Washers"},
    {"box_id": "TEST-003", "supplier": "Northstar Supply", "part": "Brackets"},
    {"box_id": "TEST-004", "supplier": "Northstar Supply", "part": "Gaskets"},
    {"box_id": "TEST-005", "supplier": "Summit Industrial", "part": "Spacers"},
    {"box_id": "TEST-006", "supplier": "Summit Industrial", "part": "Hex Nuts"},
]


def generate(output=None):
    output = Path(output or ROOT / "assets/qr")
    output.mkdir(parents=True, exist_ok=True)
    def font(size):
        try:
            return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", size)
        except OSError:
            return ImageFont.load_default(size=size)
    sheet = Image.new("RGB", (2400, 3300), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((150, 80), "PackTrack | Test box labels", fill="#152d3b", font=font(70))
    draw.text((150, 185), "Cut out one label per box. Keep the white border intact.", fill="#536878", font=font(36))
    for index, payload in enumerate(LABELS):
        code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=14, border=4)
        code.add_data(json.dumps(payload, separators=(",", ":")))
        code.make(fit=True)
        qr = code.make_image(fill_color="black", back_color="white").convert("RGB")
        qr.save(output / f"{payload['box_id']}.png", dpi=(300, 300))
        x, y = 150 + (index % 2) * 1120, 320 + (index // 2) * 960
        draw.rounded_rectangle((x, y, x + 1000, y + 900), radius=16, outline="#bdcbd3", width=3)
        preview = qr.resize((680, 680), Image.Resampling.NEAREST)
        sheet.paste(preview, (x + 160, y + 25))
        draw.text((x + 70, y + 710), payload["box_id"], fill="#152d3b", font=font(55))
        draw.text((x + 70, y + 785), payload["supplier"], fill="#536878", font=font(34))
        draw.text((x + 70, y + 835), payload["part"], fill="#536878", font=font(30))
    sheet.save(output / "test-labels.png", dpi=(300, 300))
    (output / "payloads.json").write_text(json.dumps(LABELS, indent=2) + "\n")
    with zipfile.ZipFile(output / "test-labels.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.glob("*.png")):
            archive.write(path, path.name)
        archive.write(output / "payloads.json", "payloads.json")
    return output


if __name__ == "__main__":
    print(generate())
