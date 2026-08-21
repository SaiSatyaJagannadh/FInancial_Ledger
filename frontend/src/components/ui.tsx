import type { ReactNode } from 'react'
import { formatMinor } from '../money'

export function Panel({
  title,
  action,
  children,
  className = '',
}: {
  title?: string
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--line)]">
          {title && <h2 className="text-sm font-medium">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

export function Stat({
  label,
  minor,
  hint,
  tone = 'auto',
}: {
  label: string
  minor: number
  hint?: string
  tone?: 'auto' | 'positive' | 'negative' | 'neutral'
}) {
  const cls =
    tone === 'neutral'
      ? ''
      : tone === 'positive' || (tone === 'auto' && minor >= 0)
        ? 'pos'
        : 'neg'
  return (
    <div className="panel px-4 py-3">
      <div className="text-xs uppercase tracking-wide muted">{label}</div>
      <div className={`tnum text-2xl mt-1 ${cls}`}>{formatMinor(minor)}</div>
      {hint && <div className="text-xs muted mt-0.5">{hint}</div>}
    </div>
  )
}

export function Money({ minor, currency = 'USD' }: { minor: number; currency?: string }) {
  return (
    <span className={`tnum ${minor < 0 ? 'neg' : ''}`}>{formatMinor(minor, currency)}</span>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="px-4 py-8 text-center text-sm muted">{children}</p>
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return (
    <p
      role="alert"
      className="text-sm rounded-md px-3 py-2"
      style={{ background: 'var(--negative-soft)', color: 'var(--negative)' }}
    >
      {message}
    </p>
  )
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  const style =
    tone === 'good'
      ? { background: 'var(--accent-soft)', color: 'var(--accent)' }
      : tone === 'bad'
        ? { background: 'var(--negative-soft)', color: 'var(--negative)' }
        : { background: 'var(--bg)', color: 'var(--text-muted)' }
  return (
    <span className="text-xs px-2 py-0.5 rounded-full whitespace-nowrap" style={style}>
      {children}
    </span>
  )
}
