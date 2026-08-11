from pathlib import Path

from PIL import Image, ImageDraw

from scripts.build_brand_assets import build_assets


def test_build_assets_can_use_a_dedicated_icon_source(tmp_path: Path):
    wordmark_source = tmp_path / "wordmark.png"
    icon_source = tmp_path / "icon.png"
    output = tmp_path / "assets"

    wordmark = Image.new("RGB", (1254, 1254), "white")
    ImageDraw.Draw(wordmark).rectangle((450, 500, 1100, 750), fill="#063B70")
    wordmark.save(wordmark_source)

    icon = Image.new("RGB", (1254, 1254), "white")
    ImageDraw.Draw(icon).rounded_rectangle((180, 180, 1074, 1074), radius=120, fill="#F59E0B")
    icon.save(icon_source)

    build_assets(wordmark_source, output, icon_source=icon_source)

    generated = Image.open(output / "docmarshal-icon.png").convert("RGBA")
    assert generated.size == (512, 512)
    assert generated.getpixel((256, 256))[:3] == (245, 158, 11)
    assert generated.getchannel("A").getextrema() == (0, 255)
    assert (output / "docmarshal.ico").is_file()
