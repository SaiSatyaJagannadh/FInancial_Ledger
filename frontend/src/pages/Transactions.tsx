import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../api'
import { Badge, Empty, ErrorNote, Money, Panel } from '../components/ui'
import { parseToMinor } from '../money'

const today = () => new Date().toISOString().slice(0, 10)

export default function Transactions() {
  const queryClient = useQueryClient()
  const [filters, setFilters] = useState({ q: '', start: '', end: '' })
  const [entry, setEntry] = useState({
    date: today(),
    description: '',
    amount: '',
    fromId: '',
    toId: '',
  })
  const [formError, setFormError] = useState<string | null>(null)

  const accounts = useQuery({ queryKey: ['accounts', false], queryFn: () => api.accounts() })
  const transactions = useQuery({
    queryKey: ['transactions', filters],
    queryFn: () => api.transactions({ ...filters, limit: 200 }),
  })

  const accountsById = useMemo(
    () => new Map((accounts.data ?? []).map((a) => [a.id, a])),
    [accounts.data],
  )

  const create = useMutation({
    mutationFn: () => {
      // Money moves *from* one account *to* another: one debit, one credit.
      const minor = parseToMinor(entry.amount)
      if (minor <= 0) throw new Error('enter a positive amount')
      if (!entry.fromId || !entry.toId) throw new Error('pick both accounts')
      if (entry.fromId === entry.toId) throw new Error('pick two different accounts')
      return api.createTransaction({
        date: entry.date,
        description: entry.description,
        postings: [
          { account_id: Number(entry.toId), amount_minor: minor },
          { account_id: Number(entry.fromId), amount_minor: -minor },
        ],
      })
    },
    onSuccess: () => {
      setEntry({ ...entry, description: '', amount: '' })
      setFormError(null)
      queryClient.invalidateQueries()
    },
    onError: (error) => setFormError(error instanceof Error ? error.message : String(error)),
  })

  const remove = useMutation({
    mutationFn: api.deleteTransaction,
    onSuccess: () => queryClient.invalidateQueries(),
  })

  return (
    <div className="space-y-5">
      <Panel title="Record a transaction">
        <form
          className="p-4 grid gap-3 md:grid-cols-6"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate()
          }}
        >
          <label className="grid gap-1">
            <span className="text-xs muted">Date</span>
            <input
              type="date"
              className="field"
              required
              value={entry.date}
              onChange={(e) => setEntry({ ...entry, date: e.target.value })}
            />
          </label>
          <label className="grid gap-1 md:col-span-2">
            <span className="text-xs muted">Description</span>
            <input
              className="field"
              required
              placeholder="Whole Foods"
              value={entry.description}
              onChange={(e) => setEntry({ ...entry, description: e.target.value })}
            />
          </label>
          <label className="grid gap-1">
            <span className="text-xs muted">Amount</span>
            <input
              className="field tnum"
              required
              inputMode="decimal"
              placeholder="84.32"
              value={entry.amount}
              onChange={(e) => setEntry({ ...entry, amount: e.target.value })}
            />
          </label>
          <label className="grid gap-1">
            <span className="text-xs muted">From</span>
            <select
              className="field"
              required
              value={entry.fromId}
              onChange={(e) => setEntry({ ...entry, fromId: e.target.value })}
            >
              <option value="">select…</option>
              {(accounts.data ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.code}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1">
            <span className="text-xs muted">To</span>
            <select
              className="field"
              required
              value={entry.toId}
              onChange={(e) => setEntry({ ...entry, toId: e.target.value })}
            >
              <option value="">select…</option>
              {(accounts.data ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.code}
                </option>
              ))}
            </select>
          </label>
          <div className="md:col-span-6 flex items-center gap-3">
            <button className="btn btn-primary" disabled={create.isPending}>
              {create.isPending ? 'Saving…' : 'Save'}
            </button>
            <span className="text-xs muted">
              Both legs are written together — the entry is rejected if it does not balance.
            </span>
          </div>
          {formError && (
            <div className="md:col-span-6">
              <ErrorNote error={new Error(formError)} />
            </div>
          )}
        </form>
      </Panel>

      <Panel
        title="Transactions"
        action={
          <div className="flex gap-2 flex-wrap">
            <input
              className="field w-40"
              placeholder="Search…"
              value={filters.q}
              onChange={(e) => setFilters({ ...filters, q: e.target.value })}
            />
            <input
              type="date"
              className="field w-36"
              value={filters.start}
              onChange={(e) => setFilters({ ...filters, start: e.target.value })}
            />
            <input
              type="date"
              className="field w-36"
              value={filters.end}
              onChange={(e) => setFilters({ ...filters, end: e.target.value })}
            />
          </div>
        }
      >
        {transactions.isLoading && <Empty>Loading…</Empty>}
        {transactions.data?.length === 0 && <Empty>No transactions match.</Empty>}
        {!!transactions.data?.length && (
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Description</th>
                <th>Legs</th>
                <th className="text-right">Amount</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {transactions.data.map((tx) => {
                const debit = tx.postings.find((p) => p.amount_minor > 0)
                return (
                  <tr key={tx.id}>
                    <td className="tnum whitespace-nowrap muted">{tx.date}</td>
                    <td>
                      {tx.description}{' '}
                      {tx.source !== 'manual' && <Badge>{tx.source}</Badge>}
                    </td>
                    <td className="text-xs muted">
                      {tx.postings
                        .map((p) => accountsById.get(p.account_id)?.code ?? `#${p.account_id}`)
                        .join(' → ')}
                    </td>
                    <td className="text-right">
                      <Money minor={debit?.amount_minor ?? 0} />
                    </td>
                    <td className="text-right">
                      <button className="btn text-xs" onClick={() => remove.mutate(tx.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Panel>
      <ErrorNote error={transactions.error ?? remove.error} />
    </div>
  )
}
