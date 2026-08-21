/** Formatting only. All arithmetic happens on the server, in integer cents. */

export function formatMinor(minor: number, currency = 'USD'): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(minor / 100)
}

export function formatCompact(minor: number, currency = 'USD'): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(minor / 100)
}

/** Parse user input into signed minor units without ever touching a float sum. */
export function parseToMinor(input: string): number {
  const text = input.trim()
  if (!text) throw new Error('empty amount')
  const negative = /^\(.*\)$/.test(text) || text.trim().startsWith('-')
  const cleaned = text.replace(/[^\d.]/g, '')
  if (!cleaned || cleaned === '.') throw new Error(`not an amount: ${input}`)

  const [whole, fraction = ''] = cleaned.split('.')
  const cents = Number(whole || '0') * 100 + Number((fraction + '00').slice(0, 2))
  // Round the third decimal the way the server does, rather than truncating it.
  const third = Number(fraction[2] ?? '0')
  const total = cents + (third >= 5 ? 1 : 0)
  return negative ? -total : total
}

export function monthLabel(month: string): string {
  const [year, m] = month.split('-')
  const date = new Date(Number(year), Number(m) - 1, 1)
  return date.toLocaleDateString(undefined, { month: 'short', year: '2-digit' })
}
