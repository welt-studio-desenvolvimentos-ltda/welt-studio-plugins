// Fiação dos eventos para a lib. Só este arquivo toca no `$`.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, ToolCallResult } from 'claude-code'

import type { PreflightTurn } from '../types'
import { denyMessage, findBashWrites, guardedWrites } from './lib/bash-write'
import { SECTION_ID, checklistSection } from './lib/checklist'
import type { Host, Store } from './lib/host'
import { readLedger, recordFindings, summarize } from './lib/ledger'
import { relativeInside } from './lib/paths'
import { runPrecommit } from './lib/precommit'
import { removedIdentifiers, sweepRemoved } from './lib/removed-symbols'

const COMMAND = 'preflight'
const MAX_STOP_BLOCKS = 2
const EMPTY_TURN: PreflightTurn = { touched: [], stopBlocks: 0, notes: 0 }

const rootAtom = atom({ plugin: 'preflight', key: 'root' } as const, '')
const turnAtom = atom({ plugin: 'preflight', key: 'turn' } as const, EMPTY_TURN)

function hostOf($: EngineInterface): Host {
  return {
    run: (argv, options) => $.process.run(argv, options),
    readFile: async path => {
      try {
        return await $.fs.read(path)
      } catch {
        return undefined
      }
    },
    exists: path => $.fs.exists(path),
  }
}

function storeOf($: EngineInterface): Store {
  return { get: key => $.store.get(key), set: (key, value) => $.store.set(key, value) }
}

async function findRoot($: EngineInterface, cwd: string): Promise<string> {
  try {
    const result = await $.process.run(['git', 'rev-parse', '--show-toplevel'], { cwd })
    return result.exitCode === 0 ? result.stdout.trim() : ''
  } catch {
    return ''
  }
}

function withContext<T extends ToolCallResult>(ran: T, notes: readonly string[]): T {
  if (notes.length === 0) return ran
  return { ...ran, context: [...(ran.context ?? []), ...notes] }
}

function showNotes($: EngineInterface, notes: number): void {
  $.ui.status(notes > 0 ? `preflight: ${notes} note${notes === 1 ? '' : 's'} this turn` : undefined)
}

export const register: Register = (on, options) => {
  const allowBashWrites = options.allowBashWrites === true
  const timeoutMs = typeof options.precommitTimeoutMs === 'number' ? options.precommitTimeoutMs : 120000

  on('session.start', async ($, e, next) => {
    const root = await findRoot($, e.cwd)
    await update($, rootAtom, () => root)
    await $.command.register({
      name: COMMAND,
      description: 'Show what preflight checks here and the /code-review findings it remembers for this repo',
    })
    return next(e)
  })

  // Cada prompt do usuário abre uma rodada nova: arquivos tocados, bloqueios de Stop e avisos.
  on('prompt.submit', async ($, e, next) => {
    await update($, turnAtom, () => EMPTY_TURN)
    showNotes($, 0)
    return next(e)
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    if (allowBashWrites) return next(e)
    const writes = await guardedWrites(hostOf($), findBashWrites(await $.session.cwd(), e.command))
    return writes.length > 0 ? { deny: denyMessage(writes) } : next(e)
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: ['Edit', 'Write', 'NotebookEdit'] }, async ($, e, next) => {
    const root = await read($, rootAtom)
    const path = e.tool === 'NotebookEdit' ? e.notebook_path : e.file_path
    const relative = relativeInside(root, path)
    const host = hostOf($)
    const before =
      e.tool === 'Edit' ? e.old_string : e.tool === 'Write' && relative ? await host.readFile(path) : undefined

    const ran = await next(e)
    if (relative === undefined || ran.deny !== undefined || ran.isError === true) return ran

    const notes: string[] = []
    const precommit = await runPrecommit(host, root, [relative], 'file', timeoutMs)
    if (precommit) notes.push(precommit.note)
    // Sem o arquivo depois da edição (apagado, acima de 4 MiB) não há como saber o que saiu dele.
    const after = before === undefined ? undefined : await host.readFile(path)
    if (before !== undefined && after !== undefined) {
      const swept = await sweepRemoved(host, root, removedIdentifiers(before, after))
      if (swept) notes.push(swept)
    }

    const turn = await update($, turnAtom, current => ({
      ...current,
      touched: current.touched.includes(relative) ? current.touched : [...current.touched, relative],
      notes: current.notes + notes.length,
    }))
    showNotes($, turn.notes)
    return withContext(ran, notes)
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: 'ReportFindings' }, async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny === undefined && ran.isError !== true) {
      const recorded = await recordFindings(storeOf($), await read($, rootAtom), e.findings, Date.now())
      if (recorded > 0) $.ui.toast(`preflight: ${recorded} finding${recorded === 1 ? '' : 's'} recorded for this repo`)
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('prompt.compose', async ($, e, next) => {
    const composed = await next(e)
    const root = await read($, rootAtom)
    if (root === '') return composed
    const text = checklistSection(summarize(await readLedger(storeOf($), root)))
    return { sections: [...composed.sections.filter(section => section.id !== SECTION_ID), { id: SECTION_ID, text, scope: 'session' }] }
  }).catch(($, e, next) => next(e))

  // Gate de fim de turno: o pre-commit completo (tsc, pyright incluídos) nos arquivos tocados.
  on('classic.Stop', async ($, e, next) => {
    const result = await next(e)
    if (result.block !== undefined) return result
    const root = await read($, rootAtom)
    const turn = await read($, turnAtom)
    if (turn.touched.length === 0 || turn.stopBlocks >= MAX_STOP_BLOCKS) return result

    const host = hostOf($)
    const exists = await Promise.all(turn.touched.map(file => host.exists(`${root}/${file}`)))
    const present = turn.touched.filter((_, index) => exists[index])
    // Só reprovação bloqueia: autofix que passou e pre-commit que não rodou não têm o que corrigir.
    const outcome = await runPrecommit(host, root, present, 'full', timeoutMs)
    if (outcome?.kind !== 'failed') return result

    await update($, turnAtom, current => ({ ...current, stopBlocks: current.stopBlocks + 1 }))
    const remaining = MAX_STOP_BLOCKS - turn.stopBlocks - 1
    return {
      ...result,
      block: `${outcome.note}\n\n(preflight end-of-turn gate: the full pre-commit must pass on the files changed since the user's last message. ${remaining} more block${remaining === 1 ? '' : 's'} before it lets the turn end.)`,
    }
  }).catch(($, e, next) => next(e))

  on('command.run', { command: COMMAND }, async $ => {
    const root = await read($, rootAtom)
    if (root === '') return { text: 'preflight: this session is not in a git repo; only the Bash write guard applies.' }
    const host = hostOf($)
    const hasPrecommit = await host.exists(`${root}/.pre-commit-config.yaml`)
    const summary = summarize(await readLedger(storeOf($), root), 15)
    const lines = [
      `preflight in ${root}`,
      `- Bash file writes: ${allowBashWrites ? 'allowed (allowBashWrites)' : 'blocked'}`,
      `- pre-commit per edit and at end of turn: ${hasPrecommit ? 'on' : 'off (no .pre-commit-config.yaml)'}`,
      '- removed-symbol sweep: on',
      `- /code-review findings recorded: ${summary.total}`,
      ...summary.categories.map(({ category, count }) => `    ${category}: ${count}`),
      ...(summary.recent.length > 0 ? ['- most recent:', ...summary.recent.map(entry => `    [${entry.category}] ${entry.summary} (${entry.file})`)] : []),
    ]
    return { text: lines.join('\n') }
  })
}
