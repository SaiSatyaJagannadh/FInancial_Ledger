import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Money, Stat } from '../components/ui'

describe('Money', () => {
  it('marks negative amounts so losses read as losses', () => {
    const { container } = render(<Money minor={-8432} />)
    expect(screen.getByText('-$84.32')).toBeInTheDocument()
    expect(container.firstChild).toHaveClass('neg')
  })

  it('leaves positive amounts unmarked', () => {
    const { container } = render(<Money minor={8432} />)
    expect(container.firstChild).not.toHaveClass('neg')
  })
})

describe('Stat', () => {
  it('shows the label and formatted figure', () => {
    render(<Stat label="Net worth" minor={1234567} hint="as of 2026-08" />)
    expect(screen.getByText('Net worth')).toBeInTheDocument()
    expect(screen.getByText('$12,345.67')).toBeInTheDocument()
    expect(screen.getByText('as of 2026-08')).toBeInTheDocument()
  })
})
