// A miniatura do painel: os pixels que o process_image.py grava (thumb-x.json) viram células de terminal (Raster)
// ou um SVG nas superfícies sem Raster (VS Code, desktop).

/** Lado máximo, em pixels, da miniatura que o script grava; no terminal ocupa esse tanto de colunas e metade de linhas. */
export const THUMB_SIDE = 32

export type Thumb = { width: number; height: number; rgba: Uint8Array }

/** `▀`: a metade de cima da célula é o pixel de cima (cor do texto); a de baixo, o de baixo (cor de fundo). */
const UPPER_HALF = 0x2580
/** `▄`: quando só o pixel de baixo aparece, ele vai como cor do texto e o fundo fica o do terminal. */
const LOWER_HALF = 0x2584
/** Célula sem pixel visível: espaço, para o fundo do terminal aparecer (o `▀` pintaria com a cor do texto). */
const BLANK = 0x20
/** A cor padrão do terminal, para pixel transparente. */
const DEFAULT_COLOR = 0x01000000
/** Alpha abaixo do qual o pixel conta como transparente. */
const ALPHA_VISIBLE = 128

function bytesOf(base64: string): Uint8Array {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index)
  return bytes
}

function base64Of(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

/** Lê o thumb-x.json; undefined quando falta ou não confere. */
export function parseThumb(text: string | undefined): Thumb | undefined {
  if (text === undefined) return undefined
  try {
    const { width, height, rgba } = JSON.parse(text) as Record<string, unknown>
    if (typeof width !== 'number' || typeof height !== 'number' || typeof rgba !== 'string') return undefined
    const bytes = bytesOf(rgba)
    return bytes.length === width * height * 4 ? { width, height, rgba: bytes } : undefined
  } catch {
    return undefined
  }
}

/** Reduz por vizinho mais próximo até caber em `maxSide` (pixel art continua nítida; nunca amplia). */
export function fitThumb(thumb: Thumb, maxSide: number): Thumb {
  const scale = Math.min(1, maxSide / Math.max(thumb.width, thumb.height))
  if (scale === 1) return thumb
  const width = Math.max(1, Math.round(thumb.width * scale))
  const height = Math.max(1, Math.round(thumb.height * scale))
  const rgba = new Uint8Array(width * height * 4)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const source = (Math.min(thumb.height - 1, Math.floor(y / scale)) * thumb.width + Math.min(thumb.width - 1, Math.floor(x / scale))) * 4
      rgba.set(thumb.rgba.subarray(source, source + 4), (y * width + x) * 4)
    }
  }
  return { width, height, rgba }
}

function colorAt(thumb: Thumb, x: number, y: number): number {
  if (y >= thumb.height) return DEFAULT_COLOR
  const index = (y * thumb.width + x) * 4
  const [r = 0, g = 0, b = 0, a = 0] = thumb.rgba.subarray(index, index + 4)
  return a < ALPHA_VISIBLE ? DEFAULT_COLOR : (r << 16) | (g << 8) | b
}

/** As células do Raster: uma coluna por pixel, uma linha a cada dois pixels. */
export function rasterOf(thumb: Thumb): { columns: number; rows: number; cells: string } {
  const columns = thumb.width
  const rows = Math.ceil(thumb.height / 2)
  const words = new Uint32Array(columns * rows * 3)
  for (let row = 0; row < rows; row++) {
    for (let x = 0; x < columns; x++) {
      const cell = (row * columns + x) * 3
      const top = colorAt(thumb, x, row * 2)
      const bottom = colorAt(thumb, x, row * 2 + 1)
      // A cor padrão como cor de texto pinta o glifo com a cor do texto do terminal: só a de fundo pode ficar padrão.
      const [glyph, fg, bg] =
        top !== DEFAULT_COLOR ? [UPPER_HALF, top, bottom] : bottom !== DEFAULT_COLOR ? [LOWER_HALF, bottom, DEFAULT_COLOR] : [BLANK, DEFAULT_COLOR, DEFAULT_COLOR]
      words[cell] = glyph
      words[cell + 1] = fg
      words[cell + 2] = bg
    }
  }
  return { columns, rows, cells: base64Of(new Uint8Array(words.buffer)) }
}

/** O mesmo em SVG: um quadrado por pixel visível, sem suavização. */
export function svgOf(thumb: Thumb, pixel: number): string {
  const rects: string[] = []
  for (let y = 0; y < thumb.height; y++) {
    for (let x = 0; x < thumb.width; x++) {
      const color = colorAt(thumb, x, y)
      if (color === DEFAULT_COLOR) continue
      rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="#${color.toString(16).padStart(6, '0')}"/>`)
    }
  }
  const width = thumb.width * pixel
  const height = thumb.height * pixel
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${thumb.width} ${thumb.height}" shape-rendering="crispEdges">${rects.join('')}</svg>`
}

/** O teto do `source` de um Svg; um PNG maior que isso cai no desenho por pixels. */
export const SVG_SOURCE_LIMIT = 131072

/**
 * O PNG de verdade dentro de um SVG (superfícies sem Raster: extensão do VS Code, desktop), no tamanho da miniatura.
 * `pixelated` mantém a pixel art nítida; undefined quando o PNG não cabe no limite do Svg.
 */
export function svgWithPng(png: string, thumb: Thumb, pixel: number): string | undefined {
  const width = thumb.width * pixel
  const height = thumb.height * pixel
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><image href="data:image/png;base64,${png}" width="${width}" height="${height}" style="image-rendering:pixelated"/></svg>`
  return svg.length <= SVG_SOURCE_LIMIT ? svg : undefined
}
