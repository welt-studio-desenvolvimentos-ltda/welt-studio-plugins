// O que a lib precisa do mundo de fora. O register.ts liga isto ao `$`; os testes, a um fake.

export type RunResult = { exitCode: number; stdout: string; stderr: string }

export type RunOptions = {
  cwd?: string
  stdin?: string
  timeoutMs?: number
}

export type Host = {
  /** Roda um comando por argv (sem shell). Rejeita se não inicia ou estoura o timeout. */
  run(argv: readonly string[], options?: RunOptions): Promise<RunResult>
  exists(path: string): Promise<boolean>
  /** Nomes das entradas do diretório; vazio quando não existe ou não pode ser lido. */
  list(dir: string): Promise<string[]>
  /** Texto do arquivo, ou undefined quando não existe ou não pode ser lido. */
  readText(path: string): Promise<string | undefined>
  /** Conteúdo do arquivo em base64, ou undefined quando não existe ou não pode ser lido. */
  readBase64(path: string): Promise<string | undefined>
  /** Grava o texto, criando os diretórios que faltam. */
  writeText(path: string, text: string): Promise<void>
}
