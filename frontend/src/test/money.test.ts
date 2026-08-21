import { describe, expect, it } from 'vitest'
import { formatMinor, monthLabel, parseToMinor } from '../money'

describe('parseToMinor', () => {
  it.each([
    ['84.32', 8432],
    ['$1,234.56', 123456],
    ['12', 1200],
    ['0.5', 50],
    ['0.05', 5],
    ['(50.00)', -5000],
    ['-12.34', -1234],
    ['1.005', 101], // rounds the third decimal up, matching the server
  ])('parses %s', (input, expected) => {
    expect(parseToMinor(input)).toBe(expected)
  })

  it.each(['', '   ', 'abc', '$'])('rejects %j', (input) => {
    expect(() => parseToMinor(input)).toThrow()
  })
})

describe('formatMinor', () => {
  it('renders cents as currency', () => {
    expect(formatMinor(123456)).toBe('$1,234.56')
    expect(formatMinor(-5000)).toBe('-$50.00')
    expect(formatMinor(0)).toBe('$0.00')
  })
})

describe('monthLabel', () => {
  it('shortens an ISO month', () => {
    expect(monthLabel('2026-01')).toBe('Jan 26')
  })
})
