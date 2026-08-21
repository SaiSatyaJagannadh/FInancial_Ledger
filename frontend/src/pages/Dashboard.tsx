import { useQuery } from '@tanstack/react-query'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../api'
import { Empty, Money, Panel, Stat } from '../components/ui'
import { formatCompact, formatMinor, monthLabel } from '../money'

const CHART_COLORS = ['#1a7f4b', '#2f6fb0', '#b8860b', '#8e5ba6', '#c0653b', '#4a7c7e']

export default function Dashboard() {
  const balanceSheet = useQuery({ queryKey: ['balance-sheet'], queryFn: () => api.balanceSheet() })
  const monthly = useQuery({ queryKey: ['monthly'], queryFn: () => api.monthly(12) })
  const netWorth = useQuery({ queryKey: ['net-worth'], queryFn: () => api.netWorth(12) })
  const spend = useQuery({ queryKey: ['spend'], queryFn: () => api.spendByCategory() })
  const recent = useQuery({
    queryKey: ['recent'],
    queryFn: () => api.transactions({ limit: 8 }),
  })

  const latest = monthly.data?.[monthly.data.length - 1]
  const worth = netWorth.data?.[netWorth.data.length - 1]

  const trendData = (monthly.data ?? []).map((point) => ({
    month: monthLabel(point.month),
    Income: point.income_minor / 100,
    Expenses: point.expenses_minor / 100,
  }))

  const worthData = (netWorth.data ?? []).map((point) => ({
    month: monthLabel(point.month),
    'Net worth': point.net_worth_minor / 100,
  }))

  const topSpend = (spend.data ?? []).slice(0, 6).map((row) => ({
    name: row.name,
    amount: row.amount_minor / 100,
  }))

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Net worth"
          minor={worth?.net_worth_minor ?? 0}
          hint={worth ? `as of ${worth.month}` : 'no data yet'}
        />
        <Stat
          label="Assets"
          minor={balanceSheet.data?.total_assets_minor ?? 0}
          tone="neutral"
        />
        <Stat
          label="This month in"
          minor={latest?.income_minor ?? 0}
          tone="positive"
          hint={latest ? monthLabel(latest.month) : undefined}
        />
        <Stat
          label="This month out"
          minor={-(latest?.expenses_minor ?? 0)}
          tone="negative"
          hint={latest ? monthLabel(latest.month) : undefined}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Income vs expenses">
          <div className="h-64 p-3">
            {trendData.length === 0 ? (
              <Empty>Import a statement to see monthly trends.</Empty>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    stroke="var(--text-muted)"
                    tickFormatter={(v) => formatCompact(Number(v) * 100)}
                    width={54}
                  />
                  <Tooltip
                    formatter={(v) => formatMinor(Number(v) * 100)}
                    contentStyle={{
                      background: 'var(--panel)',
                      border: '1px solid var(--line)',
                      borderRadius: 8,
                      color: 'var(--text)',
                    }}
                  />
                  <Bar dataKey="Income" fill="#1a7f4b" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="Expenses" fill="#c0653b" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Panel>

        <Panel title="Net worth">
          <div className="h-64 p-3">
            {worthData.length === 0 ? (
              <Empty>No asset or liability activity yet.</Empty>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={worthData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    stroke="var(--text-muted)"
                    tickFormatter={(v) => formatCompact(Number(v) * 100)}
                    width={54}
                  />
                  <Tooltip
                    formatter={(v) => formatMinor(Number(v) * 100)}
                    contentStyle={{
                      background: 'var(--panel)',
                      border: '1px solid var(--line)',
                      borderRadius: 8,
                      color: 'var(--text)',
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="Net worth"
                    stroke="#2f6fb0"
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </Panel>

        <Panel title="Top spending categories">
          <div className="h-64 p-3">
            {topSpend.length === 0 ? (
              <Empty>Nothing categorized yet.</Empty>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={topSpend} layout="vertical" margin={{ left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" horizontal={false} />
                  <XAxis
                    type="number"
                    tick={{ fontSize: 11 }}
                    stroke="var(--text-muted)"
                    tickFormatter={(v) => formatCompact(Number(v) * 100)}
                  />
                  <YAxis
                    type="category"
                    dataKey="name"
                    tick={{ fontSize: 11 }}
                    stroke="var(--text-muted)"
                    width={110}
                  />
                  <Tooltip
                    formatter={(v) => formatMinor(Number(v) * 100)}
                    contentStyle={{
                      background: 'var(--panel)',
                      border: '1px solid var(--line)',
                      borderRadius: 8,
                      color: 'var(--text)',
                    }}
                  />
                  <Bar dataKey="amount" radius={[0, 3, 3, 0]}>
                    {topSpend.map((_, index) => (
                      <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Panel>

        <Panel title="Recent activity">
          {recent.data?.length ? (
            <table>
              <tbody>
                {recent.data.map((tx) => {
                  const total = tx.postings
                    .filter((p) => p.amount_minor < 0)
                    .reduce((sum, p) => sum + p.amount_minor, 0)
                  return (
                    <tr key={tx.id}>
                      <td className="muted tnum whitespace-nowrap">{tx.date}</td>
                      <td className="truncate max-w-[16rem]">{tx.description}</td>
                      <td className="text-right">
                        <Money minor={total} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <Empty>No transactions yet.</Empty>
          )}
        </Panel>
      </div>
    </div>
  )
}
