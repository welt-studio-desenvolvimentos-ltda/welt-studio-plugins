# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=12"]
# ///
"""Turns one image from the Codex generator into a game-ready PNG.

Steps, in order, each optional: key out a flat chroma background, trim the empty
border, blend the edges so the image tiles, fit into an exact canvas (contain pads
with transparency, cover crops, stretch keeps a tiling texture tiling), or round
each side to a power of two. Writes the result to --out (creating its
folder) and prints {"width": W, "height": H, "warnings": [...]} on the last line.

Run with `uv run scripts/process_image.py --input raw.png --out sprite.png ...`.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from PIL import Image

# Distância de cor (soma das diferenças) até a qual o pixel é fundo puro; entre as
# duas é borda contaminada, que fica semitransparente e perde o tom do chroma.
CHROMA_HARD = 120
CHROMA_SOFT = 260
# Alpha mínimo para um pixel de pixel art contar como sólido.
ALPHA_SOLID = 128
# Quanto os canais altos do chroma podem passar dos baixos antes do despill para o
# pixel contar como tingido pelo fundo.
TINT = 40
# Acima desta fração dos pixels mantidos tingidos, o fundo vazou para a arte (não só
# para o contorno, que é sempre uma fração pequena).
TINTED_SHARE = 0.05
# Acima desta fração da borda ainda opaca depois do recorte, o fundo não era chapado.
OPAQUE_BORDER_SHARE = 0.5
# Fração de cada lado que a emenda de uma textura repetível usa para fundir as bordas.
SEAM_BLEND = 0.12
# Lado máximo do preview que volta para o modelo; pixel art pequena é ampliada por
# um fator inteiro até perto disso, para os pixels continuarem nítidos e legíveis.
PREVIEW_SIDE = 512


def parse_hex(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    if len(text) != 6:
        raise argparse.ArgumentTypeError(f"expected a colour like FF00FF, got {value!r}")
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected a colour like FF00FF, got {value!r}") from error


def parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {value!r}") from error
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError(f"sides must be positive, got {value!r}")
    return width, height


def key_out(image: Image.Image, chroma: tuple[int, int, int], hard_alpha: bool) -> tuple[Image.Image, float]:
    """Remove o fundo chroma e tira o tom do chroma que vazou para as bordas. Devolve
    também a fração dos pixels mantidos que estavam tingidos antes do despill."""
    rgba = image.convert("RGBA")
    kept = 0
    tinted = 0
    pixels = rgba.load()
    # Despill no eixo do chroma: os canais altos do chroma subindo juntos acima dos baixos.
    high = [index for index, channel in enumerate(chroma) if channel >= 128]
    low = [index for index, channel in enumerate(chroma) if channel < 128]
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            distance = abs(r - chroma[0]) + abs(g - chroma[1]) + abs(b - chroma[2])
            if distance <= CHROMA_HARD:
                pixels[x, y] = (0, 0, 0, 0)
                continue
            if distance < CHROMA_SOFT:
                a = int(a * (distance - CHROMA_HARD) / (CHROMA_SOFT - CHROMA_HARD))
            if high and low:
                rgb = [r, g, b]
                spill = min(rgb[i] for i in high) - max(rgb[i] for i in low)
                if a:
                    kept += 1
                    tinted += spill > TINT
                if spill > 0:
                    for i in high:
                        rgb[i] -= spill
                    r, g, b = rgb
            if hard_alpha:
                a = 255 if a >= ALPHA_SOLID else 0
            pixels[x, y] = (r, g, b, a) if a else (0, 0, 0, 0)
    return rgba, (tinted / kept if kept else 0.0)


def opaque_border_share(image: Image.Image) -> float:
    alpha = image.getchannel("A")
    width, height = image.size
    border = [(x, 0) for x in range(width)] + [(x, height - 1) for x in range(width)]
    border += [(0, y) for y in range(1, height - 1)] + [(width - 1, y) for y in range(1, height - 1)]
    return sum(1 for point in border if alpha.getpixel(point)) / len(border)


def has_transparency(image: Image.Image) -> bool:
    if "A" not in image.getbands() and "transparency" not in image.info:
        return False
    low, _ = image.convert("RGBA").getchannel("A").getextrema()
    return low < 255


def trim(image: Image.Image) -> Image.Image:
    if image.mode != "RGBA":
        return image
    box = image.getchannel("A").getbbox()
    return image.crop(box) if box else image


def seamless(image: Image.Image, hard: bool) -> Image.Image:
    """Torna a imagem repetível nos dois eixos: as últimas colunas (e depois as últimas
    linhas) são fundidas por cima das primeiras e cortadas, então cada borda continua
    na oposta. Mesmo método do sprite_tool.py do kingdom-redux, aplicado também na vertical.

    `hard` (pixel art): a fusão é pontilhada em vez de suave, cada pixel vem inteiro de um
    dos lados, então nenhuma cor nova (fora da paleta) aparece na emenda."""
    return _seam_rows(_seam_columns(image, hard), hard)


def _seam_columns(image: Image.Image, hard: bool) -> Image.Image:
    overlap = max(1, int(image.width * SEAM_BLEND))
    width = image.width - overlap
    result = image.crop((0, 0, width, image.height))
    tail = image.crop((width, 0, image.width, image.height))
    head = result.crop((0, 0, overlap, image.height))
    # 0 (só a cauda, que segue a última coluna) na borda esquerda, 255 (só o começo) no fim da emenda.
    ramp = Image.linear_gradient("L").rotate(90).resize((overlap, image.height))
    if hard:
        # Floyd-Steinberg: a proporção de pixels de cada lado segue a rampa, sem misturar cores.
        ramp = ramp.convert("1").convert("L")
    result.paste(Image.composite(head, tail, ramp), (0, 0))
    return result


def _seam_rows(image: Image.Image, hard: bool) -> Image.Image:
    transposed = image.transpose(Image.Transpose.TRANSPOSE)
    return _seam_columns(transposed, hard).transpose(Image.Transpose.TRANSPOSE)


def fit(image: Image.Image, size: tuple[int, int], mode: str, resample: Image.Resampling) -> Image.Image:
    width, height = size
    if mode == "stretch":
        return image.resize(size, resample)
    scale = (min if mode == "contain" else max)(width / image.width, height / image.height)
    scaled = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), resample)
    if mode == "cover":
        left = (scaled.width - width) // 2
        top = (scaled.height - height) // 2
        return scaled.crop((left, top, left + width, top + height))
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(scaled.convert("RGBA"), ((width - scaled.width) // 2, (height - scaled.height) // 2))
    return canvas


def nearest_power_of_two(value: int) -> int:
    lower = 1 << (value.bit_length() - 1)
    upper = lower << 1
    return lower if value - lower <= upper - value else upper


def power_of_two(image: Image.Image, resample: Image.Resampling) -> Image.Image:
    size = (nearest_power_of_two(image.width), nearest_power_of_two(image.height))
    return image if size == image.size else image.resize(size, resample)


def preview(image: Image.Image, pixel_art: bool) -> Image.Image:
    longest = max(image.size)
    if longest > PREVIEW_SIDE:
        scale = PREVIEW_SIDE / longest
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        return image.resize(size, Image.Resampling.NEAREST if pixel_art else Image.Resampling.LANCZOS)
    factor = PREVIEW_SIDE // longest
    if pixel_art and factor > 1:
        return image.resize((image.width * factor, image.height * factor), Image.Resampling.NEAREST)
    return image


def thumb_json(image: Image.Image, side: int, pixel_art: bool) -> str:
    """Os pixels da miniatura do painel, até `side` de lado, em RGBA base64 (o mod desenha no terminal ou em SVG)."""
    rgba = image.convert("RGBA")
    scale = min(1.0, side / max(rgba.size))
    if scale < 1:
        size = (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale)))
        rgba = rgba.resize(size, Image.Resampling.NEAREST if pixel_art else Image.Resampling.BOX)
    return json.dumps({"width": rgba.width, "height": rgba.height, "rgba": base64.b64encode(rgba.tobytes()).decode("ascii")})


def process(args: argparse.Namespace) -> tuple[Image.Image, list[str]]:
    """A imagem pronta e os avisos sobre ela: não bloqueiam, quem decide é quem pediu."""
    image = Image.open(args.input)
    image.load()
    warnings: list[str] = []
    resample = Image.Resampling.NEAREST if args.nearest else Image.Resampling.LANCZOS
    if args.expect_alpha and not has_transparency(image):
        warnings.append("transparency was requested but the generated image is fully opaque")
    if args.key is not None:
        image, tinted = key_out(image, args.key, hard_alpha=args.nearest)
        border = opaque_border_share(image)
        if border > OPAQUE_BORDER_SHARE:
            warnings.append(f"{border:.0%} of the image border is still opaque after keying: the background was not a flat chroma colour")
        if tinted > TINTED_SHARE:
            warnings.append(f"{tinted:.0%} of the kept pixels carried the chroma tint: the background bled into the art")
    if args.trim:
        image = trim(image)
    if args.seamless:
        image = seamless(image, hard=args.nearest)
    if args.fit is not None:
        image = fit(image, args.fit, args.fit_mode, resample)
    elif args.pot:
        image = power_of_two(image, resample)
    return image, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Turns one image from the Codex generator into a game-ready PNG.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--key", type=parse_hex, help="chroma colour to key out, e.g. FF00FF")
    parser.add_argument("--trim", action="store_true", help="crop the transparent border")
    parser.add_argument("--fit", type=parse_size, help="final canvas, WIDTHxHEIGHT")
    parser.add_argument("--fit-mode", choices=("contain", "cover", "stretch"), default="contain")
    parser.add_argument("--seamless", action="store_true", help="blend the edges so the image tiles in both axes")
    parser.add_argument("--pot", action="store_true", help="round each side to the nearest power of two")
    parser.add_argument("--preview", type=Path, help="also write a preview of at most 512 px here")
    parser.add_argument("--thumb", type=Path, help="also write the panel thumbnail pixels (JSON) here")
    parser.add_argument("--thumb-side", type=int, default=32, help="largest side of the thumbnail, in pixels")
    parser.add_argument("--expect-alpha", action="store_true", help="warn when the input has no transparency")
    parser.add_argument("--nearest", action="store_true", help="pixel art: nearest-neighbour scaling, hard alpha")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.input.is_file():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 1
    image, warnings = process(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out, format="PNG")
    if args.preview is not None:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        preview(image, args.nearest).save(args.preview, format="PNG")
    if args.thumb is not None:
        args.thumb.parent.mkdir(parents=True, exist_ok=True)
        args.thumb.write_text(thumb_json(image, args.thumb_side, args.nearest))
    print(json.dumps({"width": image.width, "height": image.height, "warnings": warnings}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
