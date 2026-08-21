import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { NavLink, Navigate, Route, HashRouter as Router, Routes } from 'react-router-dom'
import { api } from './api'
import Accounts from './pages/Accounts'
import Ask from './pages/Ask'
import Categorize from './pages/Categorize'
import Dashboard from './pages/Dashboard'
import Import from './pages/Import'
import Reports from './pages/Reports'
import Transactions from './pages/Transactions'
import { Badge } from './components/ui'

const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

const NAV = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/transactions', label: 'Transactions' },
  { to: '/accounts', label: 'Accounts' },
  { to: '/import', label: 'Import' },
  { to: '/categorize', label: 'Categorize' },
  { to: '/reports', label: 'Reports' },
  { to: '/ask', label: 'Ask' },
]

function HealthBadge() {
  const { data } = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30_000 })
  if (!data) return null
  return (
    <div className="flex items-center gap-2" data-testid="health">
      <Badge tone={data.balanced ? 'good' : 'bad'}>
        {data.balanced ? 'Books balanced' : `Off by ${data.trial_balance_minor}`}
      </Badge>
      <Badge tone={data.ai_enabled ? 'good' : 'neutral'}>
        {data.ai_enabled ? 'AI on' : 'AI off'}
      </Badge>
    </div>
  )
}

function Shell() {
  return (
    <div className="min-h-full">
      <header className="border-b border-[var(--line)] bg-[var(--panel)] sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4 flex-wrap">
          <span className="font-semibold tracking-tight">Ledger</span>
          <nav className="flex gap-1 flex-wrap text-sm">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `px-2.5 py-1 rounded-md ${isActive ? 'bg-[var(--accent-soft)] text-[var(--accent)]' : 'muted hover:text-[var(--text)]'}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto">
            <HealthBadge />
          </div>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/accounts" element={<Accounts />} />
          <Route path="/import" element={<Import />} />
          <Route path="/categorize" element={<Categorize />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/ask" element={<Ask />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <Router>
        <Shell />
      </Router>
    </QueryClientProvider>
  )
}
