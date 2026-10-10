# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=12"]
# ///
"""Testes do process_image.py (unittest da stdlib).

Run with `uv run scripts/test_process_image.py`.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

import process_image

MAGENTA = (255, 0, 255)
FIGURE = (40, 120, 200)


def figure_on(background: tuple[int, int, int], size: tuple[int, int] = (300, 200)) -> Image.Image:
    image = Image.new("RGB", size, background)
    ImageDraw.Draw(image).rectangle((100, 50, 159, 149), fill=FIGURE)
    return image


def gradient(size: int = 300) -> Image.Image:
    image = Image.new("RGB", (size, size))
    image.putdata([(x * 255 // (size - 1), y * 255 // (size - 1), 128) for y in range(size) for x in range(size)])
    return image


def seam_gap(image: Image.Image) -> float:
    """Diferença média entre a borda esquerda e a direita: o que se vê na emenda ao repetir."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    left = rgb.crop((0, 0, 1, height))
    right = rgb.crop((width - 1, 0, width, height))
    return sum(ImageStat.Stat(ImageChops.difference(left, right)).mean) / 3


class KeyOut(unittest.TestCase):
    def test_flat_chroma_becomes_transparent_and_the_figure_stays(self) -> None:
        keyed, tinted = process_image.key_out(figure_on(MAGENTA), MAGENTA, hard_alpha=True)
        self.assertEqual(keyed.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(keyed.getpixel((120, 100)), (*FIGURE, 255))
        self.assertEqual(tinted, 0.0)

    def test_hard_alpha_leaves_no_half_transparent_pixel(self) -> None:
        image = figure_on(MAGENTA)
        ImageDraw.Draw(image).line((100, 40, 160, 40), fill=(200, 60, 200))  # borda contaminada
        keyed, _ = process_image.key_out(image, MAGENTA, hard_alpha=True)
        histogram = keyed.getchannel("A").histogram()
        self.assertEqual(sum(histogram[1:255]), 0)


class Warnings(unittest.TestCase):
    def run_process(self, image: Image.Image, *flags: str) -> list[str]:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "raw.png"
            image.save(source)
            args = process_image.build_parser().parse_args(["--input", str(source), "--out", str(Path(folder) / "out.png"), *flags])
            _, warnings = process_image.process(args)
            return warnings

    def test_clean_pixel_art_has_no_warning(self) -> None:
        self.assertEqual(self.run_process(figure_on(MAGENTA), "--key", "FF00FF", "--trim", "--nearest"), [])

    def test_background_that_is_not_the_chroma_is_reported(self) -> None:
        warnings = self.run_process(figure_on((90, 90, 90)), "--key", "FF00FF")
        self.assertTrue(any("border is still opaque" in warning for warning in warnings), warnings)

    def test_chroma_bleeding_into_the_art_is_reported(self) -> None:
        image = Image.new("RGB", (200, 200), MAGENTA)
        ImageDraw.Draw(image).ellipse((50, 50, 150, 150), fill=(200, 60, 200))
        warnings = self.run_process(image, "--key", "FF00FF")
        self.assertTrue(any("chroma tint" in warning for warning in warnings), warnings)

    def test_missing_transparency_is_reported(self) -> None:
        warnings = self.run_process(figure_on((255, 255, 255)), "--expect-alpha")
        self.assertEqual(warnings, ["transparency was requested but the generated image is fully opaque"])

    def test_real_transparency_is_not_reported(self) -> None:
        image = figure_on(MAGENTA).convert("RGBA")
        image.putpixel((0, 0), (0, 0, 0, 0))
        self.assertEqual(self.run_process(image, "--expect-alpha"), [])


class Seamless(unittest.TestCase):
    def test_soft_seam_closes_the_gap_between_opposite_edges(self) -> None:
        source = gradient()
        self.assertGreater(seam_gap(source), 80)
        self.assertLess(seam_gap(process_image.seamless(source, hard=False)), 2)

    def test_hard_seam_adds_no_colour_that_was_not_there(self) -> None:
        source = Image.new("RGB", (200, 200), (30, 30, 30))
        ImageDraw.Draw(source).rectangle((150, 0, 199, 199), fill=(220, 180, 40))  # só a borda direita é amarela
        tiled = process_image.seamless(source, hard=True)
        self.assertEqual({colour for _, colour in tiled.getcolors()}, {(30, 30, 30), (220, 180, 40)})
        # A soft one blends them into colours of neither.
        soft = process_image.seamless(source, hard=False)
        self.assertGreater(len(soft.getcolors()), 2)


class Fit(unittest.TestCase):
    def test_contain_pads_to_the_exact_canvas(self) -> None:
        fitted = process_image.fit(Image.new("RGBA", (60, 100), (*FIGURE, 255)), (32, 48), "contain", Image.Resampling.NEAREST)
        self.assertEqual(fitted.size, (32, 48))
        self.assertEqual(fitted.getpixel((0, 24))[3], 0)

    def test_cover_crops_and_stretch_keeps_everything(self) -> None:
        image = Image.new("RGB", (200, 100), FIGURE)
        self.assertEqual(process_image.fit(image, (64, 64), "cover", Image.Resampling.LANCZOS).size, (64, 64))
        self.assertEqual(process_image.fit(image, (64, 64), "stretch", Image.Resampling.LANCZOS).size, (64, 64))

    def test_power_of_two_picks_the_nearest(self) -> None:
        self.assertEqual([process_image.nearest_power_of_two(v) for v in (1, 3, 100, 1254, 1536)], [1, 2, 128, 1024, 1024])


class Preview(unittest.TestCase):
    def test_small_pixel_art_is_enlarged_by_a_whole_factor(self) -> None:
        self.assertEqual(process_image.preview(Image.new("RGBA", (32, 32)), pixel_art=True).size, (512, 512))

    def test_large_images_shrink_to_the_limit(self) -> None:
        self.assertEqual(process_image.preview(Image.new("RGB", (1254, 627)), pixel_art=False).size, (512, 256))


class Thumb(unittest.TestCase):
    def test_small_pixel_art_keeps_every_pixel(self) -> None:
        image = Image.new("RGBA", (2, 1), (255, 0, 0, 255))
        image.putpixel((1, 0), (0, 0, 0, 0))
        thumb = json.loads(process_image.thumb_json(image, 32, pixel_art=True))
        self.assertEqual((thumb["width"], thumb["height"]), (2, 1))
        self.assertEqual(list(base64.b64decode(thumb["rgba"])), [255, 0, 0, 255, 0, 0, 0, 0])

    def test_large_images_shrink_to_the_side(self) -> None:
        thumb = json.loads(process_image.thumb_json(Image.new("RGB", (256, 128), FIGURE), 32, pixel_art=False))
        self.assertEqual((thumb["width"], thumb["height"]), (32, 16))
        self.assertEqual(len(base64.b64decode(thumb["rgba"])), 32 * 16 * 4)


class Main(unittest.TestCase):
    def test_writes_the_png_and_prints_size_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "raw.png"
            figure_on(MAGENTA).save(source)
            out = Path(folder) / "nested" / "sprite.png"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                code = process_image.main(["--input", str(source), "--out", str(out), "--key", "FF00FF", "--trim", "--fit", "32x48", "--nearest"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(printed.getvalue().strip().splitlines()[-1]), {"width": 32, "height": 48, "warnings": []})
            with Image.open(out) as saved:
                self.assertEqual(saved.size, (32, 48))


if __name__ == "__main__":
    unittest.main()
