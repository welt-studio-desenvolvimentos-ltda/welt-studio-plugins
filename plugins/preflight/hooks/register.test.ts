import type { On, ProcessRunResult } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import { SECTION_ID } from './lib/checklist'

const ROOT = '/repo'
const CONFIG = 'repos:\n  - repo: local\n    hooks:\n      - id: ruff\n      - id: tsc\n        pass_filenames: false\n'
const exited = (exitCode: number, stdout = ''): ProcessRunResult => ({
  exitCode,
  stdout,
  stderr: '',
  isStdoutTruncated: false,
  isStderrTruncated: false,
})
const OK = exited(0)
const COMPOSE = { model: 'test', promptModel: 'test', surfaces: [], tools: [], outputStyle: null, traits: [] }
const prompt = (text: string) => ({ text, wait: false, origin: { kind: 'composer' } as const })

type World = {
  files: Record<string, string>
  precommit: (argv: readonly string[], env?: Record<string, string>) => ProcessRunResult
  grep: ProcessRunResult
  runs: { argv: readonly string[]; env?: Record<string, string> }[]
}

/** O mundo abaixo do plugin: um repo em /repo, com pre-commit e git grep respondidos pela fixture. */
function world(on: On, overrides: Partial<World> = {}): World {
  const state: World = {
    files: { [`${ROOT}/.git`]: '', [`${ROOT}/.pre-commit-config.yaml`]: CONFIG },
    precommit: () => OK,
    grep: exited(1),
    runs: [],
    ...overrides,
  }
  mock.store(on)
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: ROOT }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('prompt.submit', ($, e) => ({ text: e.text }))
  on('prompt.compose', () => ({ sections: [] }))
  on('classic.Stop', () => ({}))
  on('fs.read', ($, e) => {
    const text = state.files[e.path]
    return text === undefined ? { deny: `ENOENT ${e.path}` } : { value: text }
  })
  on('fs.exists', ($, e) => ({ value: e.path in state.files }))
  on('process.run', ($, e) => {
    state.runs.push({ argv: e.argv, env: e.init?.env })
    if (e.argv[0] === 'git' && e.argv[1] === 'rev-parse') return { value: exited(0, `${ROOT}\n`) }
    if (e.argv[0] === 'git' && e.argv[1] === 'grep') return { value: state.grep }
    if (e.argv[0] === 'pre-commit') return { value: state.precommit(e.argv, e.init?.env) }
    return { deny: `unexpected command ${e.argv.join(' ')}` }
  })
  on('tool.call', () => ({ result: {} as never, text: 'ok' }))
  return state
}

async function start($: Engine): Promise<void> {
  await $.session.start({ cwd: ROOT, surface: null, isInteractive: false })
}

describe('bash write guard', () => {
  test('denies sed -i on a repo file and lets read-only commands run', async ($, on) => {
    world(on)
    await start($)
    const denied = await $.tool.call({ tool: 'Bash', command: `sed -i 's/a/b/' src/app.ts` })
    expect(denied.deny).toContain('src/app.ts (in /repo)')
    const allowed = await $.tool.call({ tool: 'Bash', command: 'git status' })
    expect(allowed.deny).toBeUndefined()
  })

  test('lets scratch output outside any git repository through', async ($, on) => {
    world(on)
    await start($)
    const ran = await $.tool.call({ tool: 'Bash', command: 'S=/scratch/run; jq . in.json > $S/out.json' })
    expect(ran.deny).toBeUndefined()
  })

  test('allowBashWrites turns the guard off', { options: { allowBashWrites: true } }, async ($, on) => {
    world(on)
    await start($)
    const ran = await $.tool.call({ tool: 'Bash', command: 'echo x > notes.md' })
    expect(ran.deny).toBeUndefined()
  })
})

describe('edit checks', () => {
  test('per-file pre-commit failure reaches the model as context, whole-project hooks skipped', async ($, on) => {
    const fixture = world(on, { precommit: () => exited(1, 'ruff....Failed\nF401 `os` imported but unused') })
    fixture.files[`${ROOT}/src/a.py`] = 'x = 1\n'
    await start($)
    const ran = await $.tool.call({ tool: 'Edit', file_path: `${ROOT}/src/a.py`, old_string: 'x = 0', new_string: 'x = 1' })
    expect(ran.context?.join('\n')).toContain('F401')
    const precommit = fixture.runs.find(run => run.argv[0] === 'pre-commit')
    expect(precommit?.argv).toEqual(['pre-commit', 'run', '--files', 'src/a.py'])
    expect(precommit?.env).toEqual({ SKIP: 'tsc' })
  })

  test('a removed name still mentioned in the repo is reported', async ($, on) => {
    const fixture = world(on, { grep: exited(0, 'docs/arch.md:12:LicenseProvider resolves the plan') })
    fixture.files[`${ROOT}/src/license.ts`] = 'export const loadLicense = () => null\n'
    await start($)
    const ran = await $.tool.call({
      tool: 'Edit',
      file_path: `${ROOT}/src/license.ts`,
      old_string: 'export class LicenseProvider {}',
      new_string: 'export const loadLicense = () => null',
    })
    expect(ran.context?.join('\n')).toContain('docs/arch.md:12')
  })

  test('a clean edit adds nothing', async ($, on) => {
    const fixture = world(on)
    fixture.files[`${ROOT}/src/a.py`] = 'x = 1\n'
    await start($)
    const ran = await $.tool.call({ tool: 'Edit', file_path: `${ROOT}/src/a.py`, old_string: 'x = 0', new_string: 'x = 1' })
    expect(ran.context ?? []).toEqual([])
  })
})

describe('end-of-turn gate', () => {
  test('blocks Stop while the full pre-commit fails, at most twice per prompt', async ($, on) => {
    const fixture = world(on, {
      precommit: (_argv, env) => (env?.SKIP ? OK : exited(1, 'tsc....Failed\nTS2304 Cannot find name')),
    })
    fixture.files[`${ROOT}/src/a.ts`] = 'const a = 1\n'
    await start($)
    await $.prompt.submit(prompt('change a'))
    await $.tool.call({ tool: 'Edit', file_path: `${ROOT}/src/a.ts`, old_string: 'const a = 0', new_string: 'const a = 1' })

    const stop = () => $.classic.Stop({ stop_hook_active: false })
    expect((await stop()).block).toContain('TS2304')
    expect((await stop()).block).toContain('TS2304')
    expect((await stop()).block).toBeUndefined()
  })

  test('an autofix that passes on the second run does not block', async ($, on) => {
    let fullRuns = 0
    const fixture = world(on, {
      precommit: (_argv, env) => (env?.SKIP || fullRuns++ > 0 ? OK : exited(1, 'prettier....Failed\n- files were modified by this hook')),
    })
    fixture.files[`${ROOT}/src/a.ts`] = 'const a = 1\n'
    await start($)
    await $.prompt.submit(prompt('change a'))
    await $.tool.call({ tool: 'Edit', file_path: `${ROOT}/src/a.ts`, old_string: 'const a = 0', new_string: 'const a = 1' })
    expect((await $.classic.Stop({ stop_hook_active: false })).block).toBeUndefined()
    expect(fullRuns).toBe(2)
  })

  test('nothing touched, nothing run', async ($, on) => {
    const fixture = world(on)
    await start($)
    await $.prompt.submit(prompt('question only'))
    expect((await $.classic.Stop({ stop_hook_active: false })).block).toBeUndefined()
    expect(fixture.runs.some(run => run.argv[0] === 'pre-commit')).toBe(false)
  })
})

describe('findings ledger', () => {
  test('a /code-review report lands in the system prompt of later requests', async ($, on) => {
    world(on)
    await start($)
    await $.tool.call({
      tool: 'ReportFindings',
      findings: [{ file: 'src/a.ts', summary: 'Delete never invalidates the list query', short_summary: 'Delete skips list invalidation', failure_scenario: 'x', category: 'correctness' }],
    })
    const composed = await $.prompt.compose(COMPOSE)
    const section = composed.sections.find(one => one.id === SECTION_ID)
    expect(section?.text).toContain('Delete skips list invalidation')
    expect(section?.text).toContain('loading, error and empty')
  })
})
