import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Empty, ErrorNote, Panel } from '../components/ui'
import type { Suggestion } from '../types'

const SOURCE_TONE: Record<string, string> = { rule: 'good', llm: 'neutral', heuristic: 'neutral' }

export default function Categorize() {
  const queryClient = useQueryClient()
  const [choices, setChoices] = useState<Record<number, number>>({})
  const [applied, setApplied] = useState<{ updated: number; errors: string[] } | null>(null)

  const accounts = useQuery({ queryKey: ['accounts', false], queryFn: () => api.accounts() })
  const categories = (accounts.data ?? []).filter(
    (a) => (a.type === 'expense' || a.type === 'income') && !a.code.includes('uncategorized'),
  )

  const suggest = useMutation({
    mutationFn: () => api.categorize(25),
    onSuccess: (data) => {
      setChoices(
        Object.fromEntries(data.suggestions.map((s: Suggestion) => [s.transaction_id, s.account_id])),
      )
      setApplied(null)
    },
  })

  const apply = useMutation({
    mutationFn: () =>
      api.applyCategories(
        Object.entries(choices).map(([transaction_id, account_id]) => ({
          transaction_id: Number(transaction_id),
          account_id,
        })),
      ),
    onSuccess: (data) => {
      setApplied(data)
      setChoices({})
      queryClient.invalidateQueries()
      suggest.mutate()
    },
  })

  useEffect(() => {
    suggest.mutate()
    // Run once on mount; suggest is a stable mutation handle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const suggestions = suggest.data?.suggestions ?? []

  return (
    <div className="space-y-5">
      <Panel
        title="Categorize uncategorized transactions"
        action={
          <div className="flex gap-2">
            <button className="btn" disabled={suggest.isPending} onClick={() => suggest.mutate()}>
              {suggest.isPending ? 'Thinking…' : 'Re-suggest'}
            </button>
            <button
              className="btn btn-primary"
              disabled={apply.isPending || Object.keys(choices).length === 0}
              onClick={() => apply.mutate()}
            >
              Apply {Object.keys(choices).length || ''}
            </button>
          </div>
        }
      >
        <div className="px-4 py-3 text-xs muted border-b border-[var(--line)]">
          Rules run first, then the model. Nothing is applied until you press Apply.
          {suggest.data?.note && <span className="block mt-1">{suggest.data.note}</span>}
        </div>

        {applied && (
          <div className="px-4 py-3 text-sm">
            Applied to <strong className="tnum">{applied.updated}</strong> transaction(s).
            {applied.errors.map((e) => (
              <span key={e} className="block text-xs neg">
                {e}
              </span>
            ))}
          </div>
        )}

        {suggest.isPending && <Empty>Working…</Empty>}
        {!suggest.isPending && suggestions.length === 0 && (
          <Empty>Nothing left to categorize. Import a statement to get more.</Empty>
        )}

        {suggestions.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Description</th>
                <th>Suggested</th>
                <th>Why</th>
                <th className="text-right">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {suggestions.map((s) => (
                <tr key={s.transaction_id}>
                  <td>{s.description}</td>
                  <td>
                    <select
                      className="field text-xs"
                      value={choices[s.transaction_id] ?? ''}
                      onChange={(e) =>
                        setChoices((current) => {
                          const next = { ...current }
                          if (e.target.value) next[s.transaction_id] = Number(e.target.value)
                          else delete next[s.transaction_id]
                          return next
                        })
                      }
                    >
                      <option value="">skip</option>
                      {categories.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.code}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="text-xs muted">
                    <Badge tone={SOURCE_TONE[s.source]}>{s.source}</Badge> {s.reason}
                  </td>
                  <td className="text-right tnum">{(s.confidence * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <ErrorNote error={suggest.error ?? apply.error} />
      </Panel>
    </div>
  )
}
