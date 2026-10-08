// Ledger de findings por repo: o que o /code-review já pegou aqui volta como lembrete no
// system prompt, para o mesmo erro não ser escrito de novo.

import type { Store } from './host'

export type LedgerEntry = { category: string; summary: string; file: string; at: number }

export type ReportedFinding = {
  file: string
  summary: string
  short_summary?: string
  category?: string
  outcome?: string
}

const MAX_ENTRIES = 150
const ledgerKey = (root: string): string => `ledger:${root}`

export async function readLedger(store: Store, root: string): Promise<LedgerEntry[]> {
  const value = await store.get(ledgerKey(root))
  return Array.isArray(value) ? (value as LedgerEntry[]) : []
}

/** Grava um report do /code-review. Re-report pós-fix (com `outcome`) repete findings já gravados. */
export async function recordFindings(
  store: Store,
  root: string,
  findings: readonly ReportedFinding[],
  now: number,
): Promise<number> {
  const fresh = findings
    .filter(finding => finding.outcome === undefined)
    .map(finding => ({
      category: finding.category ?? 'uncategorized',
      summary: finding.short_summary ?? finding.summary.slice(0, 120),
      file: finding.file,
      at: now,
    }))
  if (root === '' || fresh.length === 0) return 0
  const entries = [...(await readLedger(store, root)), ...fresh].slice(-MAX_ENTRIES)
  await store.set(ledgerKey(root), entries)
  return fresh.length
}

export type LedgerSummary = {
  total: number
  categories: { category: string; count: number }[]
  recent: LedgerEntry[]
}

export function summarize(entries: readonly LedgerEntry[], recentCount = 8): LedgerSummary {
  const counts = new Map<string, number>()
  for (const entry of entries) counts.set(entry.category, (counts.get(entry.category) ?? 0) + 1)
  const categories = [...counts]
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 5)
  const seen = new Set<string>()
  const recent: LedgerEntry[] = []
  for (const entry of [...entries].reverse()) {
    if (seen.has(entry.summary)) continue
    seen.add(entry.summary)
    recent.push(entry)
    if (recent.length === recentCount) break
  }
  return { total: entries.length, categories, recent }
}
