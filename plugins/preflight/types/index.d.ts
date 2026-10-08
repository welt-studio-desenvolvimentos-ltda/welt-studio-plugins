// Contrato de estado da sessão do preflight.
export type PreflightTurn = {
  /** Arquivos (relativos à raiz do repo) tocados por Edit/Write desde o último prompt do usuário. */
  touched: string[]
  /** Quantas vezes o Stop foi bloqueado desde o último prompt do usuário. */
  stopBlocks: number
  /** Quantos avisos o preflight devolveu ao modelo desde o último prompt do usuário. */
  notes: number
}

declare module 'claude-code' {
  interface PluginState {
    preflight: {
      /** Raiz do repositório git da sessão; '' fora de um repo. */
      root: string
      turn: PreflightTurn
    }
  }
}
