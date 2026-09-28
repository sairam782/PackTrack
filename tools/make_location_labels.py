"""Generate printable QR location markers from a measured location configuration."""
import argparse
import json
from pathlib import Path
import sys
import zipfile

from PIL import Image, ImageDraw, ImageFont
import qrcode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phone_scanner import PhoneScanner


def generate(config, output):
    scanner = PhoneScanner(config, "http://127.0.0.1:8000")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    markers = list(scanner.locations.values())
    rows = (len(markers) + 1) // 2
    sheet = Image.new('RGB', (2200, 240 + rows*1000), 'white')
    draw = ImageDraw.Draw(sheet)
    def font(size):
        try:
            return ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', size)
        except OSError:
            return ImageFont.load_default(size=size)
    title = 'PackTrack | Example location markers' if scanner.example_coordinates else 'PackTrack | Location markers'
    draw.text((80,60),title,font=font(58),fill='#173b44')
    draw.text((80,150),'Scan the location first, then the box labels.',font=font(38),fill='#52737c')
    for index, row in enumerate(markers):
        qr=qrcode.make(json.dumps({'type':'packtrack_location','location_id':row['location_id']}, separators=(',',':'))).convert('RGB')
        x,y=60+(index%2)*1100,240+(index//2)*1000
        draw.rectangle((x,y,x+1000,y+930),outline='#bbcdd2',width=3)
        sheet.paste(qr.resize((650,650),Image.Resampling.NEAREST),(x+175,y+20))
        draw.text((x+50,y+695),row['location_id'],font=font(64),fill='#173b44')
        details=f"({row['x']:g}, {row['y']:g}) metres" if row['role']=='placement' else 'TRUCK COLLECTION'
        draw.text((x+50,y+785),details,font=font(44),fill='#117565')
        draw.text((x+50,y+855),row.get('name',row['location_id']),font=font(34),fill='#52737c')
        # Filenames are ordinal, so arbitrary configured IDs cannot become paths.
        qr.save(output/f'location-{index+1}.png')
    sheet.save(output/'location-markers.png',dpi=(300,300))
    (output/'locations.json').write_text(Path(config).read_text())
    with zipfile.ZipFile(output/'location-markers.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.glob('*.png')):
            archive.write(path,path.name)
        archive.write(output/'locations.json','locations.json')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'examples/phone-locations.demo.json')
    parser.add_argument('--out',type=Path,default=ROOT/'assets/locations')
    args=parser.parse_args()
    print(generate(args.config,args.out))
