"""Reproduce the Senate reference asset bundle without importing its services/data."""
from pathlib import Path
import json
from hashlib import sha256
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "web" / "senate-assets"
BASE = "https://www.senate.ca.gov"
THEME = BASE + "/sites/senate.ca.gov/themes/custom/senatemigrationtheme"
ASSETS = {
    "background.jpg": THEME + "/images/site-background.jpg",
    "chamber.jpg": BASE + "/sites/senate.ca.gov/files/2024-03/senate-panoramic_0.jpg",
    "session.webp": BASE + "/sites/senate.ca.gov/files/styles/medium_slider/public/limon_swearing_01-05-26-lh-99.jpg.webp?itok=yMKyWiaJ",
    "capitol.jpg": THEME + "/images/capitol.jpg",
    "dome.jpg": THEME + "/images/capdome-lrg.jpg",
    "kids.jpg": THEME + "/images/kids-corner.jpg",
    "quick-agenda.jpg": BASE + "/sites/senate.ca.gov/files/inline-images/daily-file_0.jpg",
    "quick-bills.jpg": BASE + "/sites/senate.ca.gov/files/inline-images/legislation_0.jpg",
    "quick-committees.jpg": BASE + "/sites/senate.ca.gov/files/inline-images/oversight-hearing_0.jpg",
    "quick-senators.jpg": BASE + "/sites/senate.ca.gov/files/inline-images/find-senator_0.jpg",
    "quick-library.jpg": BASE + "/sites/senate.ca.gov/files/inline-images/vote.jpg",
    "opensans.woff2": THEME + "/fonts/Open%20Sans%20Regular.woff2",
    "opensans-bold.woff2": THEME + "/fonts/Open%20Sans%20Bold.woff2",
    "lora.woff2": THEME + "/fonts/Lora%20Regular.woff2",
    "lora-bold.woff2": THEME + "/fonts/Lora%20Bold.woff2",
    "NotoSans-Regular.ttf": "https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf",
    "NotoSans-LICENSE.txt": "https://raw.githubusercontent.com/notofonts/noto-fonts/main/LICENSE",
}


def main():
    TARGET.mkdir(exist_ok=True)
    manifest = []
    for filename, url in ASSETS.items():
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 Senate-reference-development"})
        with urlopen(request, timeout=30) as response:
            data = response.read(15_000_000)
        destination = TARGET / filename
        if not destination.exists() or destination.read_bytes() != data:
            download = destination.with_name(destination.name + '.download')
            download.write_bytes(data)
            download.replace(destination)
        manifest.append({"file": filename, "source": url, "bytes": len(data), "sha256": sha256(data).hexdigest()})
        print(filename, len(data))
    (TARGET / "sources.json").write_text(json.dumps({
        "retrieved_at": "2026-11-05", "reference": BASE,
        "note": "Architectural reference imagery. Session photograph depicts California, not a San Andreas proceeding. No endorsement implied. Review reuse terms before public publication.",
        "assets": manifest,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
