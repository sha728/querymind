import type { Cell } from '../api/types'

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 6 })

/** Display text for one result cell (design §4.2 value encoding). */
export function formatCell(value: Cell): string {
  if (value === null) return 'NULL'
  if (typeof value === 'number') return numberFormat.format(value)
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return value
}
