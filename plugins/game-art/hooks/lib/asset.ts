// Um asset de ponta a ponta: acha o projeto, gera pelo Codex num run da pasta de trabalho, limpa com o process_image.py e grava no destino.
// Com variações, cada uma fica processada no run e só a escolhida (keep_variant) vai para o projeto.

import { backgroundOf, buildPrompt, CHROMA_HEX, codexArgv, codexSaid, generatedImagesDir, threadIdOf } from './codex'
import { ART_DIRS, engineNote, findProject, joinPath } from './engine'
import type { Engine } from './engine'
import type { Host, RunResult } from './host'
import { KIND_SPECS } from './kinds'
import { resolveRequest } from './request'
import type { AssetInput, AssetRequest } from './request'
import { recordSaved } from './gallery'
import { loadStyle, stylePath } from './style'
import { THUMB_SIDE } from './thumb'
import { ensureWorkdir, eventsFile, finalFile, newRunDir, previewFile, PROMPT_FILE, rawFile, RUN_FILE, thumbFile, VARIANTS } from './workdir'
import type { Variant } from './workdir'

/** O script de pixels, relativo à raiz do plugin. */
export const PROCESS_SCRIPT = 'scripts/process_image.py'
/** Quantos Codex geram ao mesmo tempo (o limite que a skill hatch-pet do Codex recomenda). */
export const MAX_PARALLEL = 2

/** O uv instala o Pillow na primeira execução; depois disso o script leva segundos. */
const PROCESS_TIMEOUT_MS = 180000
const MAX_TIMEOUT_MS = 600000
const LOG_TAIL_LINES = 20

export type AssetSettings = {
  pluginRoot: string
  cwd: string
  codexHome: string
  codexModel: string
  generationTimeoutMs: number
  now: () => Date
}

/** O registro do run (run.json): o bastante para o keep_variant processar uma variação depois. */
export type RunRecord = {
  request: AssetRequest
  engine: Engine
  projectRoot: string
  styleGuide?: string
  variants: Variant[]
}

export type AssetReport = {
  path: string
  width: number
  height: number
  engine: Engine
  projectRoot: string
  runDir: string
  preview: string
  /** O guia de estilo aplicado, quando o projeto tem um. */
  styleGuide?: string
  warnings: string[]
  note: string
}

export type VariantResult =
  | { variant: Variant; ok: true; width: number; height: number; preview: string; warnings: string[] }
  | { variant: Variant; ok: false; error: string }

export type VariantsReport = { runDir: string; styleGuide?: string; results: VariantResult[] }

export type AssetOutcome =
  | { ok: true; kind: 'asset'; report: AssetReport }
  | { ok: true; kind: 'variants'; report: VariantsReport }
  | { ok: false; error: string }

function styleLine(styleGuide: string | undefined): string {
  return styleGuide ? `Style guide applied: ${styleGuide}` : 'No style guide (.game-art/style.json); create one with init_style to keep assets consistent.'
}

/** O que o modelo lê: o resultado de uma tool registrada é texto (ou blocos), nunca um objeto. */
export function formatReport(report: AssetReport): string {
  return [
    `Saved ${report.path} (${report.width}x${report.height} PNG).`,
    `Engine: ${report.engine} (project root ${report.projectRoot}).`,
    `Run folder (raw generation, prompt, Codex events): ${report.runDir}`,
    styleLine(report.styleGuide),
    ...report.warnings.map(warning => `WARNING: ${warning}`),
    report.note,
  ].join('\n')
}

export function formatVariant(result: VariantResult): string {
  if (!result.ok) return `Variant ${result.variant}: failed. ${result.error}`
  return [`Variant ${result.variant}: ${result.width}x${result.height}.`, ...result.warnings.map(warning => `WARNING: ${warning}`)].join('\n')
}

export function formatVariants(report: VariantsReport): string {
  const kept = report.results.filter(result => result.ok).map(result => result.variant)
  return [
    `Generated ${kept.length} of ${report.results.length} variants in ${report.runDir}; nothing was saved into the project yet.`,
    styleLine(report.styleGuide),
    kept.length > 0
      ? `Show them to the user and save the chosen one with keep_variant { "run": "${report.runDir}", "variant": "${kept[0]}" }.`
      : 'No variant came out; try again.',
  ].join('\n')
}

/** O resultado em texto puro: o que uma geração em segundo plano deixa na conversa (só blocos de texto cabem lá). */
export function outcomeText(outcome: AssetOutcome): string {
  if (!outcome.ok) return `Failed: ${outcome.error}`
  if (outcome.kind === 'asset') return `${formatReport(outcome.report)}\nPreview: ${outcome.report.preview}`
  return [formatVariants(outcome.report), ...outcome.report.results.map(result => (result.ok ? `${formatVariant(result)} Preview: ${result.preview}` : formatVariant(result)))].join('\n')
}

function tail(text: string): string {
  return text.trim().split('\n').slice(-LOG_TAIL_LINES).join('\n')
}

function failure(error: string): AssetOutcome & { ok: false } {
  return { ok: false, error }
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function absolute(base: string, path: string): string {
  return path.startsWith('/') ? path : joinPath(base, path)
}

/** `name.png`, ou `name-2.png`, `name-3.png`... quando já existe: nunca sobrescreve arte. */
export async function freePath(host: Host, dir: string, name: string): Promise<string> {
  const taken = new Set(await host.list(dir))
  for (let index = 1; ; index++) {
    const file = index === 1 ? `${name}.png` : `${name}-${index}.png`
    if (!taken.has(file)) return joinPath(dir, file)
  }
}

export function processArgv(pluginRoot: string, request: AssetRequest, input: string, output: string, preview: string, thumb: string): string[] {
  const spec = KIND_SPECS[request.kind]
  const background = backgroundOf(request)
  const args = ['--input', input, '--out', output, '--preview', preview, '--thumb', thumb, '--thumb-side', String(THUMB_SIDE)]
  if (background === 'chroma') args.push('--key', CHROMA_HEX)
  if (background === 'native') args.push('--expect-alpha')
  if (request.transparent && spec.trim) args.push('--trim')
  if (spec.seamless) args.push('--seamless')
  const fitMode = spec.seamless ? 'stretch' : request.transparent ? 'contain' : 'cover'
  if (request.size) args.push('--fit', `${request.size.width}x${request.size.height}`, '--fit-mode', fitMode)
  else if (spec.powerOfTwo) args.push('--pot')
  if (request.pixelArt) args.push('--nearest')
  return ['uv', 'run', '--quiet', joinPath(pluginRoot, PROCESS_SCRIPT), ...args]
}

type Processed = { width: number; height: number; warnings: string[] }

function parseProcessed(stdout: string): Processed | undefined {
  const line = stdout.trim().split('\n').pop() ?? ''
  try {
    const parsed: unknown = JSON.parse(line)
    if (typeof parsed !== 'object' || parsed === null) return undefined
    const { width, height, warnings = [] } = parsed as Record<string, unknown>
    if (typeof width !== 'number' || typeof height !== 'number') return undefined
    if (!Array.isArray(warnings) || warnings.some(warning => typeof warning !== 'string')) return undefined
    return { width, height, warnings: warnings as string[] }
  } catch {
    return undefined
  }
}

async function attempt(host: Host, argv: readonly string[], options: Parameters<Host['run']>[1], what: string): Promise<RunResult | string> {
  try {
    return await host.run(argv, options)
  } catch (error) {
    return `${what} did not finish: ${messageOf(error)}`
  }
}

/** O Codex guarda a própria cópia de cada imagem; o bruto já está no run, então a cópia sai. */
async function dropCodexCopy(host: Host, codexHome: string, stdout: string): Promise<void> {
  const threadId = threadIdOf(stdout)
  if (threadId === undefined || codexHome === '') return
  await attempt(host, ['rm', '-rf', '--', generatedImagesDir(codexHome, threadId)], {}, 'rm')
}

/** Gera o bruto de uma variação; undefined quando saiu, ou o motivo. */
async function generateRaw(host: Host, settings: AssetSettings, record: RunRecord, runDir: string, variant: Variant): Promise<string | undefined> {
  const raw = rawFile(variant)
  const prompt = buildPrompt(record.request, record.engine, raw)
  const timeoutMs = Math.min(Math.max(settings.generationTimeoutMs, 1), MAX_TIMEOUT_MS)
  const codex = await attempt(host, codexArgv(runDir, record.request.references, settings.codexModel), { cwd: runDir, stdin: prompt, timeoutMs }, 'codex exec')
  if (typeof codex === 'string') return `${codex} (is the Codex CLI installed and logged in? \`codex login status\`)`
  await host.writeText(joinPath(runDir, eventsFile(variant)), codex.stdout)
  await dropCodexCopy(host, settings.codexHome, codex.stdout)
  if (codex.exitCode === 0 && (await host.exists(joinPath(runDir, raw)))) return undefined
  const said = [...codexSaid(codex.stdout), codex.stderr].filter(text => text.trim() !== '').join('\n')
  return `Codex did not produce ${raw} (exit ${codex.exitCode}). Codex said:\n${tail(said) || '(nothing)'}`
}

/** Limpa o bruto de uma variação para `output`, com a miniatura no run. */
async function processRaw(
  host: Host,
  settings: AssetSettings,
  request: AssetRequest,
  runDir: string,
  variant: Variant,
  output: string,
): Promise<{ ok: true; processed: Processed; preview: string } | { ok: false; error: string }> {
  const raw = joinPath(runDir, rawFile(variant))
  const preview = joinPath(runDir, previewFile(variant))
  const thumb = joinPath(runDir, thumbFile(variant))
  const run = await attempt(host, processArgv(settings.pluginRoot, request, raw, output, preview, thumb), { timeoutMs: PROCESS_TIMEOUT_MS }, 'uv run')
  if (typeof run === 'string') return { ok: false, error: `${run} (is uv installed? https://docs.astral.sh/uv/). The raw image is at ${raw}` }
  const processed = run.exitCode === 0 ? parseProcessed(run.stdout) : undefined
  if (processed === undefined) {
    return { ok: false, error: `processing the image failed (exit ${run.exitCode}). The raw image is at ${raw}.\n${tail([run.stderr, run.stdout].join('\n'))}` }
  }
  return { ok: true, processed, preview }
}

function destinationDir(record: RunRecord, dest: string | undefined): string {
  return dest ? absolute(record.projectRoot, dest) : joinPath(record.projectRoot, ART_DIRS[record.engine], KIND_SPECS[record.request.kind].folder)
}

/** Roda `work` sobre os itens com no máximo `limit` ao mesmo tempo, mantendo a ordem dos resultados. */
export async function inPool<T, R>(items: readonly T[], limit: number, work: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length)
  let next = 0
  const lane = async () => {
    while (next < items.length) {
      const index = next++
      results[index] = await work(items[index] as T)
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane))
  return results
}

async function saveInto(host: Host, settings: AssetSettings, record: RunRecord, runDir: string, variant: Variant, dest: string | undefined): Promise<AssetOutcome> {
  const path = await freePath(host, destinationDir(record, dest), record.request.name)
  const done = await processRaw(host, settings, record.request, runDir, variant, path)
  if (!done.ok) return failure(done.error)
  await recordSaved(host, runDir, { path, width: done.processed.width, height: done.processed.height, variant })
  return {
    ok: true,
    kind: 'asset',
    report: {
      path,
      width: done.processed.width,
      height: done.processed.height,
      engine: record.engine,
      projectRoot: record.projectRoot,
      runDir,
      preview: done.preview,
      styleGuide: record.styleGuide,
      warnings: done.processed.warnings,
      note: engineNote(record.engine, record.request.pixelArt),
    },
  }
}

export async function generateAsset(host: Host, settings: AssetSettings, input: AssetInput): Promise<AssetOutcome> {
  const project = await findProject(host, settings.cwd)
  let guide
  try {
    guide = await loadStyle(host, project.root)
  } catch (error) {
    return failure(messageOf(error))
  }
  const request = resolveRequest(input, guide, settings.cwd, project.root)
  for (const path of request.references) {
    if (!(await host.exists(path))) return failure(`reference image not found: ${path}`)
  }

  await ensureWorkdir(host, project.root)
  const runDir = await newRunDir(host, project.root, request.name, settings.now())
  const record: RunRecord = {
    request,
    engine: project.engine,
    projectRoot: project.root,
    styleGuide: guide ? stylePath(project.root) : undefined,
    variants: VARIANTS.slice(0, input.variants),
  }
  // Gravados antes do Codex: o registro, e o diretório do run, que o `codex -C` exige existir.
  await host.writeText(joinPath(runDir, RUN_FILE), `${JSON.stringify(record, null, 2)}\n`)
  await host.writeText(joinPath(runDir, PROMPT_FILE), `${buildPrompt(request, project.engine, rawFile('a'))}\n`)

  const generated = await inPool(record.variants, MAX_PARALLEL, async variant => ({ variant, error: await generateRaw(host, settings, record, runDir, variant) }))

  if (record.variants.length === 1) {
    const only = generated[0]
    if (only?.error !== undefined) return failure(`${only.error}\nRun folder: ${runDir}`)
    return saveInto(host, settings, record, runDir, 'a', request.dest)
  }

  const results = await Promise.all(
    generated.map(async ({ variant, error }): Promise<VariantResult> => {
      if (error !== undefined) return { variant, ok: false, error }
      const done = await processRaw(host, settings, request, runDir, variant, joinPath(runDir, finalFile(variant)))
      if (!done.ok) return { variant, ok: false, error: done.error }
      return { variant, ok: true, ...done.processed, preview: done.preview }
    }),
  )
  return { ok: true, kind: 'variants', report: { runDir, styleGuide: record.styleGuide, results } }
}

/** Grava no projeto a variação escolhida de um run. */
export async function keepVariant(host: Host, settings: AssetSettings, runDir: string, variant: Variant, dest: string | undefined): Promise<AssetOutcome> {
  const text = await host.readText(joinPath(runDir, RUN_FILE))
  if (text === undefined) return failure(`${runDir} is not a game-art run (no ${RUN_FILE})`)
  let record: RunRecord
  try {
    record = JSON.parse(text) as RunRecord
  } catch (error) {
    return failure(`${joinPath(runDir, RUN_FILE)} is not valid JSON: ${messageOf(error)}`)
  }
  if (!record.variants.includes(variant)) return failure(`this run has variants ${record.variants.join(', ')}, not ${variant}`)
  if (!(await host.exists(joinPath(runDir, rawFile(variant))))) return failure(`variant ${variant} of this run did not come out (no ${rawFile(variant)})`)
  return saveInto(host, settings, record, runDir, variant, dest ?? record.request.dest)
}
