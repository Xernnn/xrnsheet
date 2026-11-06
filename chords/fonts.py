"""Fonts used to generate chord images, split so test fonts are never trained on.

A model that has seen a font can look better than it is, so accuracy is only reported
on fonts held out of training.

    .venv/bin/python -m chords.fonts      download the fonts into data/fonts/
"""

import shutil
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "data" / "fonts"
GOOGLE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
PETALUMA = "https://raw.githubusercontent.com/steinbergmedia/petaluma/master/redist/otf/"
MUSESCORE = "https://raw.githubusercontent.com/musescore/MuseScore/main/fonts/musejazz/"


@dataclass(frozen=True)
class Font:
    file: str
    split: str  # train, val or test
    style: str  # jazz, hand, serif or sans
    source: str

    @property
    def path(self) -> Path:
        return FONT_DIR / self.file


def google(folder: str, file: str, split: str, style: str) -> Font:
    return Font(file, split, style, GOOGLE + folder + "/" + urllib.parse.quote(file))


FONTS = [
    Font("PetalumaText.otf", "train", "jazz", PETALUMA + "PetalumaText.otf"),
    Font("MuseJazzText.otf", "train", "jazz", MUSESCORE + "MuseJazzText.otf"),
    google("patrickhand", "PatrickHand-Regular.ttf", "train", "hand"),
    google("gloriahallelujah", "GloriaHallelujah.ttf", "train", "hand"),
    google("architectsdaughter", "ArchitectsDaughter-Regular.ttf", "train", "hand"),
    google("shadowsintolight", "ShadowsIntoLight.ttf", "train", "hand"),
    google("indieflower", "IndieFlower-Regular.ttf", "train", "hand"),
    google("reeniebeanie", "ReenieBeanie.ttf", "train", "hand"),
    google("coveredbyyourgrace", "CoveredByYourGrace.ttf", "train", "hand"),
    google("justmeagaindownhere", "JustMeAgainDownHere.ttf", "train", "hand"),
    google("sedgwickave", "SedgwickAve-Regular.ttf", "train", "hand"),
    google("gochihand", "GochiHand-Regular.ttf", "train", "hand"),
    google("handlee", "Handlee-Regular.ttf", "train", "hand"),
    google("neucha", "Neucha.ttf", "train", "hand"),
    google("ebgaramond", "EBGaramond[wght].ttf", "train", "serif"),
    google("crimsontext", "CrimsonText-Bold.ttf", "train", "serif"),
    google("librebaskerville", "LibreBaskerville[wght].ttf", "train", "serif"),
    google("merriweather", "Merriweather[opsz,wdth,wght].ttf", "train", "serif"),
    google("roboto", "Roboto[wdth,wght].ttf", "train", "sans"),
    google("opensans", "OpenSans[wdth,wght].ttf", "train", "sans"),
    google("lato", "Lato-Bold.ttf", "train", "sans"),
    google("oswald", "Oswald[wght].ttf", "train", "sans"),
    google("caveat", "Caveat[wght].ttf", "val", "hand"),
    google("nothingyoucoulddo", "NothingYouCouldDo.ttf", "val", "hand"),
    google("lora", "Lora[wght].ttf", "val", "serif"),
    google("cabin", "Cabin[wdth,wght].ttf", "val", "sans"),
    Font("PetalumaScript.otf", "test", "jazz", PETALUMA + "PetalumaScript.otf"),
    google("kalam", "Kalam-Regular.ttf", "test", "hand"),
    google("arimo", "Arimo[wght].ttf", "test", "sans"),
    Font("LiberationSerif-Regular.ttf", "test", "serif", "/usr/share/fonts/liberation/LiberationSerif-Regular.ttf"),
    Font("NotoSerif-Regular.ttf", "test", "serif", "/usr/share/fonts/noto/NotoSerif-Regular.ttf"),
]

# Used when a font has no glyph for a symbol such as a flat or Δ.
FALLBACK_FONTS = ["/usr/share/fonts/noto/NotoMusic-Regular.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf"]


def split_fonts(split: str) -> list[Font]:
    return [font for font in FONTS if font.split == split]


def download_fonts() -> None:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    for font in FONTS:
        if font.path.exists():
            continue
        if font.source.startswith("/"):
            shutil.copy(font.source, font.path)
            continue
        request = urllib.request.Request(font.source, headers={"User-Agent": "font-download"})
        with urllib.request.urlopen(request, timeout=30) as response:
            font.path.write_bytes(response.read())
        print("downloaded", font.file, file=sys.stderr)


if __name__ == "__main__":
    download_fonts()
    print(f"{len(FONTS)} fonts in {FONT_DIR}")
