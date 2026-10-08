// Lexer de shell só até onde a trava de escrita precisa: palavras de cada comando simples,
// redirecionamentos de saída e corpos de heredoc. Não expande variável nem substituição.

export type Redirect = { op: string; target: string }

export type SimpleCommand = {
  words: string[]
  redirects: Redirect[]
  /** Corpos dos heredocs do comando, na ordem, unidos por quebra de linha. */
  heredoc: string
}

type Pending = { kind: 'redirect'; op: string } | { kind: 'heredoc'; stripTabs: boolean } | { kind: 'skip' }

export function parseShell(source: string): SimpleCommand[] {
  const commands: SimpleCommand[] = []
  let current: SimpleCommand = { words: [], redirects: [], heredoc: '' }
  let word = ''
  let hasWord = false
  let pending: Pending | undefined
  const heredocs: { delimiter: string; stripTabs: boolean }[] = []
  let i = 0

  const endWord = (): void => {
    if (!hasWord) return
    if (pending?.kind === 'redirect') current.redirects.push({ op: pending.op, target: word })
    else if (pending?.kind === 'heredoc') heredocs.push({ delimiter: word, stripTabs: pending.stripTabs })
    else if (pending?.kind !== 'skip') current.words.push(word)
    pending = undefined
    word = ''
    hasWord = false
  }

  const endCommand = (): void => {
    endWord()
    if (current.words.length > 0 || current.redirects.length > 0) commands.push(current)
    current = { words: [], redirects: [], heredoc: '' }
  }

  // Lê os corpos dos heredocs pendentes a partir de `i` (início da linha seguinte).
  const readHeredocBodies = (): void => {
    const bodies: string[] = []
    for (const { delimiter, stripTabs } of heredocs) {
      const lines: string[] = []
      while (i < source.length) {
        const end = source.indexOf('\n', i)
        const raw = end === -1 ? source.slice(i) : source.slice(i, end)
        i = end === -1 ? source.length : end + 1
        const line = stripTabs ? raw.replace(/^\t+/, '') : raw
        if (line === delimiter) break
        lines.push(line)
      }
      bodies.push(lines.join('\n'))
    }
    heredocs.length = 0
    current.heredoc = [current.heredoc, ...bodies].filter(Boolean).join('\n')
  }

  while (i < source.length) {
    const char = source[i] as string
    const nextChar = source[i + 1]

    if (char === '\\' && i + 1 < source.length) {
      if (nextChar !== '\n') {
        word += nextChar
        hasWord = true
      }
      i += 2
      continue
    }
    if (char === "'") {
      const end = source.indexOf("'", i + 1)
      word += source.slice(i + 1, end === -1 ? source.length : end)
      hasWord = true
      i = end === -1 ? source.length : end + 1
      continue
    }
    if (char === '"') {
      i += 1
      while (i < source.length && source[i] !== '"') {
        if (source[i] === '\\' && i + 1 < source.length) i += 1
        word += source[i]
        i += 1
      }
      hasWord = true
      i += 1
      continue
    }
    if (char === '#' && !hasWord) {
      while (i < source.length && source[i] !== '\n') i += 1
      continue
    }
    if (char === ' ' || char === '\t') {
      endWord()
      i += 1
      continue
    }
    if (char === '\n') {
      const command = current
      endCommand()
      i += 1
      if (heredocs.length > 0) {
        // O heredoc pertence ao comando que acabou de fechar.
        const holder = current
        current = command
        readHeredocBodies()
        if (!commands.includes(command)) commands.push(command)
        current = holder
      }
      continue
    }
    if (char === ';' || char === '|' || (char === '&' && nextChar !== '>')) {
      endCommand()
      i += nextChar === char || (char === '|' && nextChar === '&') ? 2 : 1
      continue
    }
    if (char === '>' || (char === '&' && nextChar === '>')) {
      // `2>` / `1>`: a palavra em curso é só o descritor.
      if (hasWord && /^\d+$/.test(word)) {
        word = ''
        hasWord = false
      } else {
        endWord()
      }
      let op = char === '&' ? '&>' : '>'
      i += char === '&' ? 2 : 1
      if (source[i] === '>') {
        op += '>'
        i += 1
      } else if (source[i] === '|') {
        i += 1
      } else if (source[i] === '&' && op === '>') {
        // `>&2`, `>&-`: duplicação de descritor, não escreve arquivo.
        i += 1
        pending = { kind: 'skip' }
        continue
      }
      pending = { kind: 'redirect', op }
      continue
    }
    if (char === '<') {
      endWord()
      if (source.startsWith('<<<', i)) {
        i += 3
        pending = { kind: 'skip' }
      } else if (source.startsWith('<<', i)) {
        const stripTabs = source[i + 2] === '-'
        i += stripTabs ? 3 : 2
        pending = { kind: 'heredoc', stripTabs }
      } else {
        i += 1
        pending = { kind: 'skip' }
      }
      continue
    }
    word += char
    hasWord = true
    i += 1
  }
  endCommand()
  if (heredocs.length > 0) {
    const last = commands[commands.length - 1]
    if (last) {
      current = last
      readHeredocBodies()
    }
  }
  return commands
}
