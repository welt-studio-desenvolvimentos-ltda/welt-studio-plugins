// Os tipos de asset: pasta de destino, fundo padrão, pós-processamento e o que o prompt exige de cada um.

export const KINDS = ['sprite', 'icon', 'ui', 'texture', 'background', 'concept'] as const
export type Kind = (typeof KINDS)[number]

export type KindSpec = {
  /** Subpasta dentro da pasta de arte da engine. */
  folder: string
  /** Fundo transparente quando o pedido não diz. */
  transparent: boolean
  /** Apara a borda vazia depois de recortar o fundo. */
  trim: boolean
  /** Sem tamanho pedido, ajusta para potência de 2 (mipmaps, compressão). */
  powerOfTwo: boolean
  /** Funde as bordas para repetir nos dois eixos; o encaixe de tamanho estica em vez de cortar, para não quebrar a emenda. */
  seamless: boolean
  /** Invariantes que entram no prompt do Codex. */
  rules: string
}

export const KIND_SPECS: Record<Kind, KindSpec> = {
  sprite: {
    folder: 'sprites',
    transparent: true,
    trim: true,
    powerOfTwo: false,
    seamless: false,
    rules:
      'One single game sprite, full body visible, readable silhouette at small size. Production asset tone, not concept art. No scenery, no ground, no cast shadow.',
  },
  icon: {
    folder: 'icons',
    transparent: true,
    trim: true,
    powerOfTwo: false,
    seamless: false,
    rules: 'One single icon, centered, bold silhouette that stays readable at 64 px. No frame or badge unless the subject asks for one.',
  },
  ui: {
    folder: 'ui',
    transparent: true,
    trim: true,
    powerOfTwo: false,
    seamless: false,
    rules: 'One single UI element (button, panel, frame, bar or HUD piece) in flat front view, with clean straight edges.',
  },
  texture: {
    folder: 'textures',
    transparent: false,
    trim: false,
    powerOfTwo: true,
    seamless: true,
    rules:
      'A square seamless tileable texture seen straight on: the edges wrap seamlessly, lighting is even, no perspective, no obvious focal element.',
  },
  background: {
    folder: 'backgrounds',
    transparent: false,
    trim: false,
    powerOfTwo: false,
    seamless: false,
    rules: 'A game background or environment layer that fills the whole canvas. No characters unless the subject asks for them.',
  },
  concept: {
    folder: 'concepts',
    transparent: false,
    trim: false,
    powerOfTwo: false,
    seamless: false,
    rules: 'Concept art for reference, presented cleanly on a plain backdrop.',
  },
}
