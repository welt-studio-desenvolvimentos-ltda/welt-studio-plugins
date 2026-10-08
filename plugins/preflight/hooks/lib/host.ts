// O que a lib precisa do mundo de fora. O register.ts liga isto ao `$`; os testes, a um fake.

export type RunResult = { exitCode: number; stdout: string; stderr: string }

export type RunOptions = {
  cwd?: string
  env?: Record<string, string>
  timeoutMs?: number
}

export type Host = {
  /** Roda um comando por argv (sem shell). Rejeita se não inicia ou estoura o timeout. */
  run(argv: readonly string[], options?: RunOptions): Promise<RunResult>
  /** Texto do arquivo, ou undefined quando não existe ou não pode ser lido. */
  readFile(path: string): Promise<string | undefined>
  exists(path: string): Promise<boolean>
}

export type Store = {
  get(key: string): Promise<unknown>
  set(key: string, value: unknown): Promise<void>
}
