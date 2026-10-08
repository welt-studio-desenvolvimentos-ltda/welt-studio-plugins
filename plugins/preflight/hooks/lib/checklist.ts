// A seção do system prompt: o checklist dos findings que pedem julgamento (os que nenhuma
// trava mecânica pega) e o histórico de review deste repo.

import type { LedgerSummary } from './ledger'

export const SECTION_ID = 'preflight:checklist'

// Cada item corresponde a uma família recorrente de findings nas sessões analisadas.
const CHECKLIST = [
  'Every query or fetch renders loading, error and empty as distinct states; a failed load is never shown as "nothing here", and actions stay disabled while their data loads.',
  'Every mutation invalidates every cache key that reads what it changed.',
  'A fix applied to one code path is applied to its siblings (admin/member, create/update, web/api, each caller of the helper).',
  'Writes that must stay consistent run in one transaction; check-then-act is atomic.',
  'Input boundaries are handled: null, 0, empty string, empty list, timezone/UTC dates, off-by-one ranges.',
  'A value needed in two places (query key, error code, route, constant) has one declaration that both import.',
  'User-facing text goes through the project i18n/error mapping; raw codes and wrong-language strings never reach the UI.',
  'Each new test can fail for the reason it exists: no real network, no wall-clock dependence, no assertion that passes vacuously.',
  'Comments, docstrings, docs and the commit message describe the code as it is after the change.',
]

export function checklistSection(summary: LedgerSummary): string {
  const lines = [
    '# Preflight: get it right before review',
    'Every task here goes through /code-review afterwards. Before declaring work done, check the changed code against this list; it is what reviews keep finding:',
    ...CHECKLIST.map((item, index) => `${index + 1}. ${item}`),
  ]
  if (summary.total > 0) {
    const categories = summary.categories.map(({ category, count }) => `${category} ${count}`).join(', ')
    lines.push(
      '',
      `Past /code-review findings in this repo (${summary.total} recorded; ${categories}). Recent ones, do not repeat them:`,
      ...summary.recent.map(entry => `- [${entry.category}] ${entry.summary} (${entry.file})`),
    )
  }
  return lines.join('\n')
}
