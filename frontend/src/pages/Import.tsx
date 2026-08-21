import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { Badge, Empty, ErrorNote, Money, Panel } from '../components/ui'
import type { ImportPreview, ImportRow } from '../types'

export default function Import() {
  const queryClient = useQueryClient()
  const [accountId, setAccountId] = useState('')
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [rows, setRows] = useState<ImportRow[]>([])
  const [result, setResult] = useState<{ created: number; skipped: number; errors: string[] } | null>(
    null,
  )

  const accounts = useQuery({ queryKey: ['accounts', false], queryFn: () => api.accounts() })
  const banks = (accounts.data ?? []).filter(
    (a) => a.type === 'asset' || a.type === 'liability',
  )
  const categories = (accounts.data ?? []).filter(
    (a) => a.type === 'expense' || a.type === 'income',
  )

  const doPreview = useMutation({
    mutationFn: (file: File) => api.previewImport(Number(accountId), file),
    onSuccess: (data) => {
      setPreview(data)
      setRows(data.rows)
      setResult(null)
    },
  })

  const commit = useMutation({
    mutationFn: () => api.commitImport(Number(accountId), rows),
    onSuccess: (data) => {
      setResult(data)
      setPreview(null)
      setRows([])
      queryClient.invalidateQueries()
    },
  })

  const setRowAccount = (index: number, value: string) => {
    setRows((current) =>
      current.map((row, i) =>
        i === index
          ? {
              ...row,
              suggested_account_id: value ? Number(value) : null,
              suggested_account_code:
                categories.find((a) => a.id === Number(value))?.code ?? null,
            }
          : row,
      ),
    )
  }

  const importable = rows.filter((row) => !row.duplicate).length

  return (
    <div className="space-y-5">
      <Panel title="Import a CSV statement">
        <div className="p-4 grid gap-3 sm:grid-cols-[1fr_1fr] items-end">
          <label className="grid gap-1">
            <span className="text-xs muted">Bank or card account</span>
            <select
              className="field"
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
            >
              <option value="">select…</option>
              {banks.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.code}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1">
            <span className="text-xs muted">CSV file</span>
            <input
              type="file"
              accept=".csv,text/csv"
              className="field"
              disabled={!accountId}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) doPreview.mutate(file)
              }}
            />
          </label>
          <p className="text-xs muted sm:col-span-2">
            Columns are detected automatically: a date, a description or payee, and either an
            amount or a debit/credit pair. Nothing is written until you confirm.
          </p>
          {doPreview.error && (
            <div className="sm:col-span-2">
              <ErrorNote error={doPreview.error} />
            </div>
          )}
        </div>
      </Panel>

      {result && (
        <Panel title="Import complete">
          <div className="p-4 text-sm space-y-2">
            <p>
              Created <strong className="tnum">{result.created}</strong>, skipped{' '}
              <strong className="tnum">{result.skipped}</strong> (already imported).
            </p>
            {result.errors.map((error) => (
              <p key={error} className="text-xs neg">
                {error}
              </p>
            ))}
          </div>
        </Panel>
      )}

      {preview && (
        <Panel
          title={`Preview — ${preview.total} rows, ${preview.duplicates} already imported`}
          action={
            <button
              className="btn btn-primary"
              disabled={commit.isPending || importable === 0}
              onClick={() => commit.mutate()}
            >
              {commit.isPending ? 'Importing…' : `Import ${importable} rows`}
            </button>
          }
        >
          {preview.errors.length > 0 && (
            <div className="p-3 space-y-1">
              {preview.errors.map((error) => (
                <p key={error} className="text-xs neg">
                  {error}
                </p>
              ))}
            </div>
          )}
          {rows.length === 0 ? (
            <Empty>Nothing to import from this file.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th className="text-right">Amount</th>
                  <th>Category</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={row.external_id} style={{ opacity: row.duplicate ? 0.45 : 1 }}>
                    <td className="tnum whitespace-nowrap muted">{row.date}</td>
                    <td>
                      {row.description} {row.duplicate && <Badge>duplicate</Badge>}
                    </td>
                    <td className="text-right">
                      <Money minor={row.amount_minor} />
                    </td>
                    <td>
                      <select
                        className="field text-xs"
                        disabled={row.duplicate}
                        value={row.suggested_account_id ?? ''}
                        onChange={(e) => setRowAccount(index, e.target.value)}
                      >
                        <option value="">uncategorized</option>
                        {categories
                          .filter((a) =>
                            row.amount_minor > 0 ? a.type === 'income' : a.type === 'expense',
                          )
                          .map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.code}
                            </option>
                          ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <ErrorNote error={commit.error} />
        </Panel>
      )}
    </div>
  )
}
