import { describe, expect, test } from 'claude-code/testing'

import { denyMessage, findBashWrites, guardedWrites } from './lib/bash-write'
import type { Host, RunOptions, RunResult } from './lib/host'
import { recordFindings, readLedger, summarize } from './lib/ledger'
import { runPrecommit, wholeProjectHookIds } from './lib/precommit'
import { removedIdentifiers, sweepRemoved } from './lib/removed-symbols'
import { parseShell } from './lib/shell'

const CWD = '/repo'

function fakeHost(answer: (argv: readonly string[], options?: RunOptions) => RunResult, files: Record<string, string> = {}): Host & { calls: { argv: readonly string[]; options?: RunOptions }[] } {
  const calls: { argv: readonly string[]; options?: RunOptions }[] = []
  return {
    calls,
    run: async (argv, options) => {
      calls.push({ argv, options })
      return answer(argv, options)
    },
    readFile: async path => files[path],
    exists: async path => path in files,
  }
}

describe('parseShell', () => {
  test('splits commands and keeps heredoc bodies with their command', () => {
    const commands = parseShell("cat > out.txt <<'EOF'\nhello > not-a-redirect\nEOF\necho done 2>&1 | tee -a log.txt")
    expect(commands.map(command => command.words[0])).toEqual(['cat', 'echo', 'tee'])
    expect(commands[0]?.redirects).toEqual([{ op: '>', target: 'out.txt' }])
    expect(commands[0]?.heredoc).toBe('hello > not-a-redirect')
    expect(commands[1]?.redirects).toEqual([])
  })

  test('quotes protect operators', () => {
    const commands = parseShell(`grep -n "a > b; c" file.txt`)
    expect(commands).toHaveLength(1)
    expect(commands[0]?.words).toEqual(['grep', '-n', 'a > b; c', 'file.txt'])
  })
})

describe('bash write guard', () => {
  // Dois repositórios (/repo, a sessão, e /other) e um rascunho fora de qualquer um.
  const repos = fakeHost(() => ({ exitCode: 0, stdout: '', stderr: '' }), { '/repo/.git': '', '/other/.git': '' })
  const blocked = async (command: string): Promise<string[]> =>
    (await guardedWrites(repos, findBashWrites(CWD, command))).map(write => write.target)

  test('in-place editors on repo files are blocked', async () => {
    expect(await blocked(`sed -i 's/a/b/' src/app.ts`)).toEqual(['src/app.ts'])
    expect(await blocked(`sed -Ei -e 's/a/b/' -e 's/c/d/' a.py b.py`)).toEqual(['a.py', 'b.py'])
    expect(await blocked(`perl -pi -e 's/x/y/' lib/x.pm`)).toEqual(['lib/x.pm'])
    expect(await blocked(`find . -name '*.ts' | xargs sed -i 's/a/b/'`)).toEqual(['(stdin)'])
  })

  test('redirects and tee into a repo are blocked; devices and temp dirs are not', async () => {
    expect(await blocked('echo x > notes.md')).toEqual(['notes.md'])
    expect(await blocked('printf x >> /repo/docs/a.md')).toEqual(['/repo/docs/a.md'])
    expect(await blocked('echo x | tee README.md')).toEqual(['README.md'])
    expect(await blocked('make 2>&1 > /dev/null')).toEqual([])
    expect(await blocked('echo x > /tmp/scratch.txt')).toEqual([])
    expect(await blocked('echo x > "$TMPDIR/out"')).toEqual([])
  })

  test('any git repository is guarded, not only the session one', async () => {
    expect(await blocked(`python3 - <<'EOF'\nopen('/other/cfg.json', 'w').write('{}')\nEOF`)).toEqual(['/other/cfg.json'])
    expect(await blocked('echo x > ../other/notes.md')).toEqual(['../other/notes.md'])
  })

  test('variables assigned in the command resolve; unknown ones stay blocked', async () => {
    expect(await blocked('S=/scratch/run; cat > $S/out.json <<EOF\n{}\nEOF')).toEqual([])
    expect(await blocked('export A=/scratch; echo x > "${A}/b.txt"')).toEqual([])
    expect(await blocked('S=/repo/src; echo x > $S/a.ts')).toEqual(['$S/a.ts'])
    expect(await blocked('echo x > "$OUT"')).toEqual(['$OUT'])
    expect(await blocked('echo x > ~/notes.md')).toEqual(['~/notes.md'])
  })

  test('cd moves where relative targets land', async () => {
    expect(await blocked('cd /scratch && sed -i s/a/b/ cfg.ini')).toEqual([])
    expect(await blocked(`A=/scratch; cd $A && python3 -c "open('cfg.ini', 'w').write('')"`)).toEqual([])
    expect(await blocked('cd /scratch && cd /repo/src && echo x > a.ts')).toEqual(['a.ts'])
    expect(await blocked('(cd /scratch && true); echo x > a.ts')).toEqual(['a.ts'])
  })

  test('inline scripts that write files are blocked unless every path is outside', async () => {
    const heredoc = "python3 - <<'EOF'\nfrom pathlib import Path\np = Path('src/a.py')\np.write_text(p.read_text().replace('a', 'b'))\nEOF"
    expect(await blocked(heredoc)).toEqual(['src/a.py'])
    expect(await blocked(`python3 -c "open('/tmp/x.json', 'w').write('{}')"`)).toEqual([])
    expect(await blocked(`node -e "require('fs').writeFileSync(process.argv[1], '')"`)).toEqual(['(script target)'])
    expect(await blocked(`cd /scratch && node -e "require('fs').writeFileSync(process.argv[1], '')"`)).toEqual([])
    expect(await blocked(`python3 -c "print(open('a.txt').read())"`)).toEqual([])
  })

  test('copies and moves into a repo are blocked; out of it they pass', async () => {
    expect(await blocked('cp /tmp/x.ts src/a.ts')).toEqual(['src/a.ts'])
    expect(await blocked('cp -r /scratch/mod/. /other/plugins/mod/')).toEqual(['/other/plugins/mod/'])
    expect(await blocked('cp -t src /tmp/a.ts /tmp/b.ts')).toEqual(['src'])
    expect(await blocked('install -m 644 /tmp/a.cfg etc/a.cfg')).toEqual(['etc/a.cfg'])
    expect(await blocked('rsync -a --exclude node_modules /scratch/mod/ plugins/mod/')).toEqual(['plugins/mod/'])
    expect(await blocked('mv src/old.ts src/new.ts')).toEqual(['src/old.ts', 'src/new.ts'])
    expect(await blocked('mv src/old.ts /tmp/old.ts')).toEqual(['src/old.ts'])
    expect(await blocked('dd if=/tmp/blob of=assets/blob.bin bs=1M')).toEqual(['assets/blob.bin'])
    expect(await blocked('cp src/a.ts /tmp/a.ts')).toEqual([])
    expect(await blocked('dd if=src/a.bin of=/dev/null')).toEqual([])
  })

  test('nested shells are followed', async () => {
    expect(await blocked(`bash -c "sed -i 's/a/b/' x.ts"`)).toEqual(['x.ts'])
    expect(await blocked(`bash -lc "sed -i 's/a/b/' x.ts"`)).toEqual(['x.ts'])
  })

  test('loops, conditionals, subshells and prefixes do not hide the command', async () => {
    expect(await blocked(`for f in *.ts; do sed -i 's/a/b/' "$f"; done`)).toEqual(['$f'])
    expect(await blocked(`if true; then perl -pi -e 's/x/y/' a.pm; fi`)).toEqual(['a.pm'])
    expect(await blocked(`(sed -i 's/a/b/' a.ts)`)).toEqual(['a.ts)'])
    expect(await blocked(`find . -name '*.ts' | xargs -I {} sed -i 's/a/b/' {}`)).toEqual(['{}'])
    expect(await blocked(`ls | xargs -n 1 sed -i 's/a/b/'`)).toEqual(['(stdin)'])
    expect(await blocked(`timeout 10 sed -i 's/a/b/' a.ts`)).toEqual(['a.ts'])
  })

  test('a cd inside a subshell leaves later relative targets unknown', async () => {
    expect(await blocked('cd /scratch && (cd /repo && sed -i s/a/b/ a.ts)')).toEqual(['a.ts)'])
  })

  test('find -exec edits are judged by where the search starts', async () => {
    expect(await blocked(`find src -name '*.ts' -exec sed -i 's/a/b/' {} +`)).toEqual(['{} in src'])
    expect(await blocked(`cd /scratch && find /repo -name '*.ts' -exec sed -i 's/a/b/' {} \\;`)).toEqual(['{} in /repo'])
    expect(await blocked(`find /scratch -name '*.ts' -exec sed -i 's/a/b/' {} +`)).toEqual([])
    expect(await blocked(`find . -name '*.ts' -exec grep -l foo {} +`)).toEqual([])
  })

  test('Path.open in write mode counts as a write', async () => {
    expect(await blocked(`python3 -c "from pathlib import Path; Path('src/a.py').open('w').write('')"`)).toEqual(['src/a.py'])
  })

  test('read-only commands pass', async () => {
    for (const command of ['ls -la', 'git status', 'git diff > /dev/null', 'pytest -q', `sed -n '1,20p' a.ts`, 'grep -rn foo src']) {
      expect(await blocked(command)).toEqual([])
    }
  })

  test('the message names the repo, or says the target is unknown', async () => {
    const message = denyMessage(await guardedWrites(repos, findBashWrites(CWD, 'echo x > a.md; echo y > "$OUT"')))
    expect(message).toContain('a.md (in /repo)')
    expect(message).toContain('$OUT (target unknown')
  })
})

describe('precommit', () => {
  const CONFIG = [
    'repos:',
    '  - repo: local',
    '    hooks:',
    '      - id: ruff',
    '        entry: ruff check --fix',
    '      - id: frontend-tsc',
    '        entry: pnpm run typecheck',
    '        pass_filenames: false',
    '      - id: docs-drift',
    '        always_run: true',
    '  - repo: https://github.com/pre-commit/pre-commit-hooks',
    '    hooks:',
    '      - id: end-of-file-fixer',
  ].join('\n')

  test('whole-project hooks are the ones without per-file arguments', () => {
    expect(wholeProjectHookIds(CONFIG)).toEqual(['frontend-tsc', 'docs-drift'])
  })

  test('per-file run skips whole-project hooks and reports failures', async () => {
    const host = fakeHost(() => ({ exitCode: 1, stdout: 'Ruff.....Failed\nF401 unused import\nEnd of files....Passed', stderr: '' }), {
      '/repo/.pre-commit-config.yaml': CONFIG,
    })
    const outcome = await runPrecommit(host, '/repo', ['a.py'], 'file', 1000)
    expect(host.calls[0]?.argv).toEqual(['pre-commit', 'run', '--files', 'a.py'])
    expect(host.calls[0]?.options?.env).toEqual({ SKIP: 'frontend-tsc,docs-drift' })
    expect(outcome?.kind).toBe('failed')
    expect(outcome?.note).toContain('F401 unused import')
    expect(outcome?.note).not.toContain('Passed')
  })

  test('an autofix that leaves the file clean asks for a re-read only', async () => {
    let run = 0
    const host = fakeHost(() => (run++ === 0 ? { exitCode: 1, stdout: '- files were modified by this hook', stderr: '' } : { exitCode: 0, stdout: '', stderr: '' }), {
      '/repo/.pre-commit-config.yaml': CONFIG,
    })
    const outcome = await runPrecommit(host, '/repo', ['a.py'], 'file', 1000)
    expect(outcome?.kind).toBe('autofixed')
    expect(outcome?.note).toContain('autofixed a.py')
  })

  test('an autofix notice past the output cut still triggers the second run', async () => {
    let run = 0
    const noisy = `${'E501 line too long\n'.repeat(400)}- files were modified by this hook`
    const host = fakeHost(() => (run++ === 0 ? { exitCode: 1, stdout: noisy, stderr: '' } : { exitCode: 0, stdout: '', stderr: '' }), {
      '/repo/.pre-commit-config.yaml': CONFIG,
    })
    expect((await runPrecommit(host, '/repo', ['a.py'], 'file', 1000))?.kind).toBe('autofixed')
    expect(host.calls).toHaveLength(2)
  })

  test('a run that cannot start is unfinished, not a failure', async () => {
    const host: Host = {
      run: async () => {
        throw new Error('spawn pre-commit ENOENT')
      },
      readFile: async path => (path === '/repo/.pre-commit-config.yaml' ? CONFIG : undefined),
      exists: async () => false,
    }
    expect((await runPrecommit(host, '/repo', ['a.py'], 'full', 1000))?.kind).toBe('unfinished')
  })

  test('no config, no run', async () => {
    const host = fakeHost(() => ({ exitCode: 1, stdout: '', stderr: '' }))
    expect(await runPrecommit(host, '/repo', ['a.py'], 'full', 1000)).toBeUndefined()
    expect(host.calls).toHaveLength(0)
  })
})

describe('removed symbols', () => {
  test('only distinctive names that left the file count', () => {
    const before = 'const data = LicenseProvider.load(user_id)\nreturn renderCard(data)'
    const after = 'const data = loadLicense(user_id)\nreturn renderCard(data)'
    expect(removedIdentifiers(before, after)).toEqual(['LicenseProvider'])
  })

  test('the sweep lists repo mentions per name', async () => {
    const host = fakeHost(() => ({ exitCode: 0, stdout: 'docs/a.md:3:Uses LicenseProvider here\nsrc/b.ts:9:import { LicenseProviderX }', stderr: '' }))
    const note = await sweepRemoved(host, '/repo', ['LicenseProvider'])
    expect(host.calls[0]?.argv).toEqual(['git', 'grep', '-n', '-I', '-w', '-F', '-e', 'LicenseProvider'])
    expect(note).toContain('LicenseProvider (1)')
    expect(note).toContain('docs/a.md:3')
    expect(note).not.toContain('LicenseProviderX')
  })

  test('no mentions, no note', async () => {
    const host = fakeHost(() => ({ exitCode: 1, stdout: '', stderr: '' }))
    expect(await sweepRemoved(host, '/repo', ['LicenseProvider'])).toBeUndefined()
  })
})

describe('ledger', () => {
  test('records first reports only and summarizes by category', async () => {
    const values: Record<string, unknown> = {}
    const store = { get: async (key: string) => values[key], set: async (key: string, value: unknown) => void (values[key] = value) }
    await recordFindings(store, '/repo', [
      { file: 'a.ts', summary: 'long', short_summary: 'Stale docstring', category: 'documentation' },
      { file: 'b.ts', summary: 'Missing invalidation', category: 'correctness' },
      { file: 'c.ts', summary: 'Other', category: 'correctness' },
      { file: 'a.ts', summary: 'long', short_summary: 'Stale docstring', category: 'documentation', outcome: 'fixed' },
    ], 1)
    const summary = summarize(await readLedger(store, '/repo'))
    expect(summary.total).toBe(3)
    expect(summary.categories[0]).toEqual({ category: 'correctness', count: 2 })
    expect(summary.recent.map(entry => entry.summary)).toEqual(['Other', 'Missing invalidation', 'Stale docstring'])
  })
})
