// Caminhos POSIX sem Node: o módulo roda num ambiente sem `path`.

export function isAbsolute(path: string): boolean {
  return path.startsWith('/')
}

/** Resolve `path` contra `base` e normaliza `.` e `..`. */
export function resolvePath(base: string, path: string): string {
  const joined = isAbsolute(path) ? path : `${base}/${path}`
  const parts: string[] = []
  for (const part of joined.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') parts.pop()
    else parts.push(part)
  }
  return `/${parts.join('/')}`
}

/** Caminho relativo a `root` quando `path` está dentro dele; undefined fora. */
export function relativeInside(root: string, path: string): string | undefined {
  if (root === '') return undefined
  const absolute = resolvePath(root, path)
  if (absolute === root) return undefined
  return absolute.startsWith(`${root}/`) ? absolute.slice(root.length + 1) : undefined
}

export function dirname(path: string): string {
  const index = path.lastIndexOf('/')
  return index <= 0 ? '/' : path.slice(0, index)
}
