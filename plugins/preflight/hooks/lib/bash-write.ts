// Trava de escrita por Bash: edição em massa por sed/heredoc/redirect escapa do pre-commit e da
// varredura de símbolos, e gera os findings de "sobra da substituição em massa". Vale para escrita
// dentro de qualquer repositório git; fora deles (rascunho, /tmp) a escrita passa.

import type { Host } from './host'
import { dirname, resolvePath } from './paths'
import { parseShell, type SimpleCommand } from './shell'

/** `path` é o alvo absoluto; undefined quando o comando não deixa saber onde cai. */
export type BashWrite = { how: string; target: string; path: string | undefined }

/** Escrita que cai num repositório (`repo`), ou de alvo desconhecido (`repo` undefined). */
export type GuardedWrite = BashWrite & { repo: string | undefined }

const WRAPPERS = new Set(['sudo', 'env', 'command', 'nohup', 'time', 'exec', 'nice', 'timeout', 'xargs'])
// Opções dos prefixos que consomem o argumento seguinte (`xargs -I {}`, `nice -n 10`).
const WRAPPER_VALUED: Record<string, ReadonlySet<string>> = {
  sudo: new Set(['-u', '-g', '-C', '-D', '-h', '-p', '-r', '-t', '-U']),
  env: new Set(['-u', '-C', '-S']),
  nice: new Set(['-n']),
  timeout: new Set(['-s', '-k']),
  xargs: new Set(['-a', '-d', '-E', '-I', '-L', '-n', '-P', '-s']),
}
// Palavras que abrem um comando composto: o comando de fato vem depois delas (`do sed -i ...`).
const KEYWORDS = new Set(['if', 'then', 'elif', 'else', 'while', 'until', 'do', '!', '{', '('])
const FIND_EXEC = new Set(['-exec', '-execdir', '-ok', '-okdir'])
const SHELLS = new Set(['bash', 'sh', 'zsh', 'dash'])
const INTERPRETER = /^(python[\d.]*|node|ruby|perl|deno|bun|php)$/
const FILE_DEVICES = /^\/dev\/(null|stdout|stderr|tty|fd\/\d+)$/
const SAFE_TEMP = /^\$\{?(TMPDIR|TMP|TEMP)\}?\//
const ASSIGNMENT = /^([A-Za-z_]\w*)=(.*)$/s

// Chamadas que gravam arquivo num script inline.
const WRITE_CALL =
  /\.write_text\(|\.write_bytes\(|\.writelines?\(|\bopen\(.*?,\s*(mode\s*=\s*)?['"][wax]|\.open\(\s*(mode\s*=\s*)?['"][wax]|writeFile(Sync)?\(|appendFile(Sync)?\(|\bFile\.write|shutil\.(copy\w*|move)\(|os\.(replace|rename)\(|\.rename\(|\bfs\.(rm|unlink)/
const PATH_LITERAL = /(['"`])([^'"`\n]+?)\1/g

/** O que o comando já fixou antes do ponto atual: variáveis atribuídas e o diretório do `cd`. */
type Scope = { cwd: string | undefined; vars: Map<string, string> }

function basename(word: string): string {
  return word.slice(word.lastIndexOf('/') + 1)
}

/** Expande `$NAME`/`${NAME}` atribuídos no próprio comando; undefined se sobra algo que não se sabe. */
function expand(scope: Scope, word: string): string | undefined {
  const expanded = word.replace(
    /\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)/g,
    (whole: string, braced: string | undefined, bare: string | undefined) => scope.vars.get(braced ?? bare ?? '') ?? whole,
  )
  return expanded.includes('$') || expanded.includes('`') || expanded.startsWith('~') ? undefined : expanded
}

/** Caminho absoluto do alvo, ou undefined quando não dá para saber. */
function resolveTarget(scope: Scope, target: string): string | undefined {
  const expanded = expand(scope, target)
  if (expanded === undefined) return undefined
  if (expanded.startsWith('/')) return resolvePath('/', expanded)
  return scope.cwd === undefined ? undefined : resolvePath(scope.cwd, expanded)
}

/** Nunca é escrita em arquivo de projeto: dispositivos e o diretório temporário do sistema. */
function isHarmless(target: string): boolean {
  return FILE_DEVICES.test(target) || SAFE_TEMP.test(target)
}

function write(scope: Scope, how: string, target: string): BashWrite[] {
  return isHarmless(target) ? [] : [{ how, target, path: resolveTarget(scope, target) }]
}

/** As palavras a partir do comando de fato: sem `(`, palavra reservada, atribuição e prefixo. */
function stripPrefix(words: readonly string[]): string[] {
  const rest = [...words]
  let index = 0
  while (index < rest.length) {
    // `(sed ...`: o parêntese do subshell vem grudado na primeira palavra.
    const word = (rest[index] as string).replace(/^\(+/, '')
    rest[index] = word
    if (word === '' || KEYWORDS.has(word) || /^[A-Za-z_]\w*=/.test(word)) {
      index += 1
      continue
    }
    if (!WRAPPERS.has(word)) break
    index += 1
    const valued = WRAPPER_VALUED[word]
    while (index < rest.length && (rest[index] as string).startsWith('-')) index += valued?.has(rest[index] as string) ? 2 : 1
    // `timeout 10 cmd`: a duração vem antes do comando.
    if (word === 'timeout') index += 1
  }
  return rest.slice(index)
}

/** Argumentos posicionais, pulando opções; `valued` lista as opções que consomem o próximo. */
function positionals(args: string[], valued: Set<string>): { positional: string[]; scripts: string[] } {
  const positional: string[] = []
  const scripts: string[] = []
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index] as string
    if (arg === '--') {
      positional.push(...args.slice(index + 1))
      break
    }
    if (valued.has(arg)) {
      scripts.push(args[index + 1] ?? '')
      index += 1
    } else if (!arg.startsWith('-') || arg === '-') {
      positional.push(arg)
    }
  }
  return { positional, scripts }
}

function inPlaceTargets(name: string, args: string[]): string[] | undefined {
  if (name === 'sed') {
    // `-i` sozinho ou agrupado com as chaves sem valor do sed (`-Ei`, `-ni`, `-i.bak`).
    const inPlace = args.some(arg => arg === '--in-place' || arg.startsWith('--in-place=') || /^-[Ernszu]*i/.test(arg))
    if (!inPlace) return undefined
    const { positional, scripts } = positionals(args, new Set(['-e', '-f', '--expression', '--file', '-l']))
    return scripts.length > 0 ? positional : positional.slice(1)
  }
  if (name === 'perl' || name === 'ruby') {
    // `-i` sozinho ou agrupado com as chaves sem valor (`-pi`, `-npi`, `-pli.bak`).
    const inPlace = args.some(arg => /^-[nplaws]*i/.test(arg))
    if (!inPlace) return undefined
    const { positional, scripts } = positionals(args, new Set(['-e', '-E']))
    return scripts.length > 0 ? positional : positional.slice(1)
  }
  if (name === 'awk' || name === 'gawk') {
    const index = args.indexOf('-i')
    if (index === -1 || args[index + 1] !== 'inplace') return undefined
    const rest = [...args.slice(0, index), ...args.slice(index + 2)]
    const { positional } = positionals(rest, new Set(['-f', '-v', '-F']))
    return rest.includes('-f') ? positional : positional.slice(1)
  }
  return undefined
}

// Opções de cópia que consomem o argumento seguinte; `-t`/`--target-directory` é o destino.
const COPY_VALUED: Record<string, ReadonlySet<string>> = {
  cp: new Set(['-t', '--target-directory', '-S', '--suffix']),
  mv: new Set(['-t', '--target-directory', '-S', '--suffix']),
  install: new Set(['-t', '--target-directory', '-S', '--suffix', '-m', '--mode', '-o', '--owner', '-g', '--group']),
  rsync: new Set(['-e', '--rsh', '-f', '--filter', '--exclude', '--include', '--exclude-from', '--include-from', '--files-from', '-T', '--temp-dir']),
}

/**
 * Arquivos que uma cópia ou movimentação grava: o destino (o último operando, ou o de `-t`). No `mv`
 * a origem também conta, porque some de onde estava.
 */
function copyTargets(name: string, args: string[]): string[] {
  if (name === 'dd') {
    const output = args.find(arg => arg.startsWith('of='))
    return output === undefined ? [] : [output.slice(3)]
  }
  const valued = COPY_VALUED[name]
  if (valued === undefined) return []
  const { positional } = positionals(args, new Set(valued))
  const directory = args.flatMap((arg, index) => {
    if (arg === '-t' || arg === '--target-directory') return [args[index + 1] ?? '']
    return arg.startsWith('--target-directory=') ? [arg.slice('--target-directory='.length)] : []
  })
  if (directory.length > 0) return name === 'mv' ? [...directory, ...positional] : directory
  if (positional.length < 2) return []
  return name === 'mv' ? positional : positional.slice(-1)
}

function inlineScript(args: string[], heredoc: string): string | undefined {
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index] as string
    if (arg === '-c' || arg === '-e' || arg === '--eval') return args[index + 1]
  }
  const { positional } = positionals(args, new Set(['-m', '-W', '-X', '-r']))
  const readsStdin = positional.length === 0 || positional[0] === '-'
  return readsStdin && heredoc !== '' ? heredoc : undefined
}

function scriptWrites(scope: Scope, how: string, script: string): BashWrite[] {
  if (!WRITE_CALL.test(script)) return []
  const literals = [...script.matchAll(PATH_LITERAL)]
    .map(match => match[2] as string)
    .filter(text => text.includes('/') || /\.\w{1,8}$/.test(text))
  // Sem literal de caminho não há como saber o alvo: julga pelo diretório onde o script roda.
  if (literals.length === 0) return [{ how, target: '(script target)', path: scope.cwd }]
  return literals.flatMap(text => write(scope, how, text))
}

/** Atribuições soltas (`X=1`, `export X=1`) e `cd` mudam o escopo dos comandos seguintes. */
function updateScope(scope: Scope, words: readonly string[]): void {
  const assignments = words[0] === 'export' ? words.slice(1) : words
  if (assignments.length > 0 && assignments.every(word => ASSIGNMENT.test(word))) {
    for (const word of assignments) {
      const [, name, value] = ASSIGNMENT.exec(word) as RegExpExecArray
      const expanded = expand(scope, value as string)
      if (expanded === undefined) scope.vars.delete(name as string)
      else scope.vars.set(name as string, expanded)
    }
    return
  }
  const command = stripPrefix(words)
  if (command[0] !== 'cd') return
  // `cd` solto move o resto do comando. Dentro de subshell ou de if/laço (`(cd x && ...)`,
  // `then cd x`) não se sabe onde o resto cai sem seguir o `)` ou o `fi`: o diretório vira
  // desconhecido, e alvo relativo dali em diante é barrado.
  const target = command[1]
  scope.cwd = words[0] === 'cd' && target !== undefined ? resolveTarget(scope, target) : undefined
}

function commandWrites(scope: Scope, command: SimpleCommand, depth: number): BashWrite[] {
  const found: BashWrite[] = []
  for (const { op, target } of command.redirects) found.push(...write(scope, `redirect ${op}`, target))
  const words = stripPrefix(command.words)
  const name = basename(words[0] ?? '')
  const args = words.slice(1)

  if (name === 'tee') {
    for (const target of positionals(args, new Set()).positional) found.push(...write(scope, 'tee', target))
  }
  for (const target of copyTargets(name, args)) found.push(...write(scope, name, target))
  const inPlace = inPlaceTargets(name, args)
  if (inPlace !== undefined) {
    // Sem arquivo na linha (`xargs sed -i`), os alvos vêm da entrada: julga pelo diretório.
    if (inPlace.length === 0) found.push({ how: `${name} in-place`, target: '(stdin)', path: scope.cwd })
    for (const target of inPlace) found.push(...write(scope, `${name} in-place`, target))
  } else if (INTERPRETER.test(name)) {
    const script = inlineScript(args, command.heredoc)
    if (script !== undefined) found.push(...scriptWrites(scope, `${name} script`, script))
  }
  if (SHELLS.has(name) && depth < 3) {
    // `-c` sozinho ou agrupado (`-lc`, `-ec`).
    const index = args.findIndex(arg => /^-[A-Za-z]*c$/.test(arg))
    const inner = index === -1 ? (args.length === 0 ? command.heredoc : undefined) : args[index + 1]
    if (inner) found.push(...scanSource({ cwd: scope.cwd, vars: new Map(scope.vars) }, inner, depth + 1))
  }
  if (name === 'find' && depth < 3) found.push(...findExecWrites(scope, args, depth))
  updateScope(scope, command.words)
  return found
}

/**
 * `find ... -exec cmd {} ;`: o comando de cada `-exec`, até o `;` ou `+` que o fecha. Os `{}`
 * são os arquivos achados, então um alvo `{}` é julgado pelos pontos de partida da busca.
 */
function findExecWrites(scope: Scope, args: readonly string[], depth: number): BashWrite[] {
  const expression = args.findIndex(arg => /^[-(!]/.test(arg))
  const starts = expression === -1 ? args : args.slice(0, expression)
  const roots = starts.length > 0 ? starts : ['.']
  const found: BashWrite[] = []
  for (let index = 0; index < args.length; index += 1) {
    if (!FIND_EXEC.has(args[index] as string)) continue
    const begin = index + 1
    while (index < args.length && args[index] !== ';' && args[index] !== '+') index += 1
    const exec: SimpleCommand = { words: args.slice(begin, index), redirects: [], heredoc: '' }
    for (const one of commandWrites({ cwd: scope.cwd, vars: new Map(scope.vars) }, exec, depth + 1)) {
      if (!one.target.includes('{}')) found.push(one)
      else for (const root of roots) found.push(...write(scope, one.how, root).map(hit => ({ ...hit, target: `{} in ${root}` })))
    }
  }
  return found
}

function scanSource(scope: Scope, source: string, depth: number): BashWrite[] {
  return parseShell(source).flatMap(command => commandWrites(scope, command, depth))
}

/** Escritas em arquivo que o comando faria, com o alvo resolvido quando dá para saber. */
export function findBashWrites(cwd: string, source: string): BashWrite[] {
  return scanSource({ cwd: resolvePath('/', cwd), vars: new Map() }, source, 0)
}

/** Raiz do repositório git que contém `path` (o próprio `path` incluso), ou undefined. */
async function repoOf(host: Host, path: string): Promise<string | undefined> {
  for (let dir = path; ; dir = dirname(dir)) {
    if (await host.exists(`${dir}/.git`)) return dir
    if (dir === '/') return undefined
  }
}

/** As escritas que a trava barra: dentro de um repositório git, ou de alvo desconhecido. */
export async function guardedWrites(host: Host, writes: readonly BashWrite[]): Promise<GuardedWrite[]> {
  const guarded: GuardedWrite[] = []
  for (const one of writes) {
    if (one.path === undefined) {
      guarded.push({ ...one, repo: undefined })
      continue
    }
    const repo = await repoOf(host, one.path)
    if (repo !== undefined) guarded.push({ ...one, repo })
  }
  return guarded
}

export function denyMessage(writes: readonly GuardedWrite[]): string {
  const list = writes
    .slice(0, 5)
    .map(one => `${one.how} → ${one.target} (${one.repo === undefined ? 'target unknown: unresolved variable or path' : `in ${one.repo}`})`)
    .join('; ')
  return [
    `preflight: writing files inside a git repository through Bash is blocked (${list}).`,
    'Use the Edit or Write tool instead, so the repo pre-commit and the removed-symbol sweep run on the change.',
    'Scratch output may go to a path outside any git repository (e.g. /tmp), written literally or through a variable assigned in the same command.',
  ].join(' ')
}
