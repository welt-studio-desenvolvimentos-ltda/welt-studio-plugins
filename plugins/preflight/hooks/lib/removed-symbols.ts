// Varredura de símbolo removido: a maior fonte de findings era sobra de rename/remoção/extração
// (docstring citando tipo apagado, import órfão, doc descrevendo função que não existe mais).

import type { Host } from './host'

const IDENTIFIER = /[A-Za-z_$][\w$]*/g
const MAX_SYMBOLS = 15
const MAX_HITS_PER_SYMBOL = 8

/**
 * Só nomes compostos (camelCase, PascalCase, snake_case, SCREAMING_CASE): palavras soltas
 * (`data`, `value`, `return`) dariam ruído no repo inteiro.
 */
function isDistinctive(name: string): boolean {
  if (name.length < 4) return false
  return /[a-z][A-Z]/.test(name) || /[A-Za-z]_[A-Za-z]/.test(name)
}

export function identifiers(text: string): Set<string> {
  const found = new Set<string>()
  for (const match of text.matchAll(IDENTIFIER)) {
    if (isDistinctive(match[0])) found.add(match[0])
  }
  return found
}

/** Identificadores que estavam no trecho antigo e sumiram do arquivo inteiro depois da edição. */
export function removedIdentifiers(before: string, fileAfter: string): string[] {
  const after = identifiers(fileAfter)
  return [...identifiers(before)].filter(name => !after.has(name)).slice(0, MAX_SYMBOLS)
}

/** Procura os nomes no repo (código, docs, comentários) e monta o aviso; undefined sem ocorrência. */
export async function sweepRemoved(host: Host, root: string, names: readonly string[]): Promise<string | undefined> {
  if (root === '' || names.length === 0) return undefined
  const argv = ['git', 'grep', '-n', '-I', '-w', '-F', ...names.flatMap(name => ['-e', name])]
  let stdout: string
  try {
    const result = await host.run(argv, { cwd: root, timeoutMs: 15000 })
    // git grep sai 1 quando não encontra nada.
    if (result.exitCode !== 0) return undefined
    stdout = result.stdout
  } catch {
    return undefined
  }

  const patterns = names.map(name => ({ name, pattern: new RegExp(`(^|[^\\w$])${name.replace(/\$/g, '\\$')}([^\\w$]|$)`) }))
  const hits = new Map<string, string[]>()
  for (const line of stdout.split('\n')) {
    if (line === '') continue
    // Só o conteúdo (`caminho:linha:conteúdo`): um nome no caminho do arquivo não é menção.
    const content = line.replace(/^.*?:\d+:/, '')
    for (const { name, pattern } of patterns) {
      if (!pattern.test(content)) continue
      const list = hits.get(name) ?? []
      list.push(line.length > 200 ? `${line.slice(0, 200)}…` : line)
      hits.set(name, list)
    }
  }
  if (hits.size === 0) return undefined

  const sections = [...hits].map(([name, lines]) => {
    const shown = lines.slice(0, MAX_HITS_PER_SYMBOL).map(line => `    ${line}`)
    const more = lines.length > MAX_HITS_PER_SYMBOL ? [`    … ${lines.length - MAX_HITS_PER_SYMBOL} more`] : []
    return [`  ${name} (${lines.length}):`, ...shown, ...more].join('\n')
  })
  return [
    'preflight: this edit removed names from the file that the repo still mentions.',
    'Check each one: a caller, import, doc, comment, docstring, test or config left behind is a',
    'leftover to update in this same task; an unrelated name that only shares the spelling is fine.',
    ...sections,
  ].join('\n')
}
