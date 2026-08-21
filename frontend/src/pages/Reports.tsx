import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { Badge, Empty, Money, Panel } from '../components/ui'
import type { ReportLine } from '../types'

const firstOfYear = () => `${new Date().getFullYear()}-01-01`
const today = () => new Date().toISOString().slice(0, 10)

function LineTable({ rows, total }: { rows: ReportLine[]; total: number }) {
  if (rows.length === 0) return <Empty>Nothing here yet.</Empty>
  return (
    <table>
      <tbody>
        {rows.map((line) => (
          <tr key={line.account_id}>
            <td>
              <span className="tnum text-xs muted mr-2">{line.code}</span>
              {line.name}
            </td>
            <td className="text-right">
              <Money minor={line.amount_minor} />
            </td>
          </tr>
        ))}
        <tr>
          <td className="font-medium">Total</td>
          <td className="text-right font-medium">
            <Money minor={total} />
          </td>
        </tr>
      </tbody>
    </table>
  )
}

export default function Reports() {
  const [range, setRange] = useState({ start: firstOfYear(), end: today() })

  const sheet = useQuery({
    queryKey: ['balance-sheet', range.end],
    queryFn: () => api.balanceSheet(range.end),
  })
  const statement = useQuery({
    queryKey: ['income-statement', range],
    queryFn: () => api.incomeStatement(range.start, range.end),
  })
  const spend = useQuery({
    queryKey: ['spend', range],
    queryFn: () => api.spendByCategory(range.start, range.end),
  })

  return (
    <div className="space-y-5">
      <div className="flex gap-3 flex-wrap items-end">
        <label className="grid gap-1">
          <span className="text-xs muted">From</span>
          <input
            type="date"
            className="field w-40"
            value={range.start}
            onChange={(e) => setRange({ ...range, start: e.target.value })}
          />
        </label>
        <label className="grid gap-1">
          <span className="text-xs muted">To</span>
          <input
            type="date"
            className="field w-40"
            value={range.end}
            onChange={(e) => setRange({ ...range, end: e.target.value })}
          />
        </label>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel
          title={`Balance sheet — as of ${sheet.data?.as_of ?? '…'}`}
          action={
            sheet.data && (
              <Badge tone={sheet.data.balanced ? 'good' : 'bad'}>
                {sheet.data.balanced ? 'A = L + E' : 'does not balance'}
              </Badge>
            )
          }
        >
          <div className="px-4 pt-3 text-xs uppercase tracking-wide muted">Assets</div>
          <LineTable rows={sheet.data?.assets ?? []} total={sheet.data?.total_assets_minor ?? 0} />
          <div className="px-4 pt-3 text-xs uppercase tracking-wide muted">Liabilities</div>
          <LineTable
            rows={sheet.data?.liabilities ?? []}
            total={sheet.data?.total_liabilities_minor ?? 0}
          />
          <div className="px-4 pt-3 text-xs uppercase tracking-wide muted">Equity</div>
          <LineTable rows={sheet.data?.equity ?? []} total={sheet.data?.total_equity_minor ?? 0} />
        </Panel>

        <div className="space-y-5">
          <Panel title="Income statement">
            <div className="px-4 pt-3 text-xs uppercase tracking-wide muted">Income</div>
            <LineTable
              rows={statement.data?.income ?? []}
              total={statement.data?.total_income_minor ?? 0}
            />
            <div className="px-4 pt-3 text-xs uppercase tracking-wide muted">Expenses</div>
            <LineTable
              rows={statement.data?.expenses ?? []}
              total={statement.data?.total_expenses_minor ?? 0}
            />
            <div className="flex justify-between px-4 py-3 border-t border-[var(--line)]">
              <span className="font-medium">Net</span>
              <Money minor={statement.data?.net_minor ?? 0} />
            </div>
          </Panel>

          <Panel title="Spending by category">
            {spend.data?.length ? (
              <table>
                <tbody>
                  {spend.data.map((row) => (
                    <tr key={row.account_id}>
                      <td>{row.name}</td>
                      <td className="text-right">
                        <Money minor={row.amount_minor} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>No spending in this range.</Empty>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}
