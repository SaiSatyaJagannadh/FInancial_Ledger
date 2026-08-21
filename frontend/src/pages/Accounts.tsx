import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { Badge, Empty, ErrorNote, Money, Panel } from '../components/ui'
import type { AccountType } from '../types'

const TYPES: AccountType[] = ['asset', 'liability', 'equity', 'income', 'expense']

export default function Accounts() {
  const queryClient = useQueryClient()
  const [showArchived, setShowArchived] = useState(false)
  const [form, setForm] = useState({ code: '', name: '', type: 'expense' as AccountType })

  const accounts = useQuery({
    queryKey: ['accounts', showArchived],
    queryFn: () => api.accounts(showArchived),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['accounts'] })
    queryClient.invalidateQueries({ queryKey: ['health'] })
  }

  const create = useMutation({
    mutationFn: () => api.createAccount(form),
    onSuccess: () => {
      setForm({ code: '', name: '', type: form.type })
      invalidate()
    },
  })

  const archive = useMutation({ mutationFn: api.archiveAccount, onSuccess: invalidate })
  const remove = useMutation({ mutationFn: api.deleteAccount, onSuccess: invalidate })

  const grouped = TYPES.map((type) => ({
    type,
    rows: (accounts.data ?? []).filter((a) => a.type === type),
  })).filter((group) => group.rows.length > 0)

  return (
    <div className="space-y-5">
      <Panel title="New account">
        <form
          className="p-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto_auto]"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate()
          }}
        >
          <label className="grid gap-1">
            <span className="text-xs muted">Code</span>
            <input
              className="field"
              placeholder="expenses:food:groceries"
              required
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
            />
          </label>
          <label className="grid gap-1">
            <span className="text-xs muted">Name</span>
            <input
              className="field"
              placeholder="Groceries"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label className="grid gap-1">
            <span className="text-xs muted">Type</span>
            <select
              className="field"
              value={form.type}
              onChange={(e) => setForm({ ...form, type: e.target.value as AccountType })}
            >
              {TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <button className="btn btn-primary self-end" disabled={create.isPending}>
            {create.isPending ? 'Adding…' : 'Add account'}
          </button>
          {create.error && (
            <div className="sm:col-span-4">
              <ErrorNote error={create.error} />
            </div>
          )}
        </form>
      </Panel>

      <label className="flex items-center gap-2 text-sm muted">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
        />
        Show archived
      </label>

      {(remove.error || archive.error) && <ErrorNote error={remove.error ?? archive.error} />}

      {grouped.length === 0 && <Empty>No accounts yet — add one above.</Empty>}

      {grouped.map((group) => (
        <Panel key={group.type} title={group.type}>
          <table>
            <thead>
              <tr>
                <th>Code</th>
                <th>Name</th>
                <th className="text-right">Balance</th>
                <th className="text-right">With children</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {group.rows.map((account) => (
                <tr key={account.id}>
                  <td className="tnum text-xs">{account.code}</td>
                  <td>
                    {account.name}{' '}
                    {account.archived && <Badge>archived</Badge>}
                  </td>
                  <td className="text-right">
                    <Money minor={account.balance_minor} currency={account.currency} />
                  </td>
                  <td className="text-right muted">
                    <Money minor={account.rollup_minor} currency={account.currency} />
                  </td>
                  <td className="text-right whitespace-nowrap">
                    {!account.archived && (
                      <button className="btn text-xs" onClick={() => archive.mutate(account.id)}>
                        Archive
                      </button>
                    )}{' '}
                    <button
                      className="btn text-xs"
                      title="Only possible for accounts with no postings"
                      onClick={() => remove.mutate(account.id)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ))}
    </div>
  )
}
