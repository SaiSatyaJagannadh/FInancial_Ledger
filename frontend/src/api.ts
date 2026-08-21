import type {
  Account,
  AskResponse,
  BalanceSheet,
  CategorizeResponse,
  CategorySpend,
  Health,
  ImportPreview,
  ImportRow,
  IncomeStatement,
  MonthlyPoint,
  NetWorthPoint,
  Rule,
  Transaction,
} from './types'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init.headers
        : { 'Content-Type': 'application/json', ...init?.headers },
  })

  if (!response.ok) {
    // FastAPI puts the useful part in `detail`, which is a string for our
    // HTTPExceptions and a list for schema validation failures.
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail))
        detail = body.detail.map((d: { msg: string }) => d.msg).join('; ')
    } catch {
      /* non-JSON error body: keep the status text */
    }
    throw new ApiError(detail, response.status)
  }

  return response.status === 204 ? (undefined as T) : response.json()
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const api = {
  health: () => request<Health>('/health'),

  accounts: (includeArchived = false) =>
    request<Account[]>(`/accounts${qs({ include_archived: includeArchived })}`),
  createAccount: (body: {
    code: string
    name: string
    type: string
    currency?: string
    parent_id?: number | null
  }) => request<Account>('/accounts', { method: 'POST', body: JSON.stringify(body) }),
  archiveAccount: (id: number) => request<Account>(`/accounts/${id}/archive`, { method: 'POST' }),
  deleteAccount: (id: number) => request<void>(`/accounts/${id}`, { method: 'DELETE' }),

  transactions: (params: {
    start?: string
    end?: string
    account_id?: number
    q?: string
    limit?: number
  }) => request<Transaction[]>(`/transactions${qs(params)}`),
  createTransaction: (body: {
    date: string
    description: string
    memo?: string | null
    postings: { account_id: number; amount_minor: number; currency?: string }[]
  }) => request<Transaction>('/transactions', { method: 'POST', body: JSON.stringify(body) }),
  deleteTransaction: (id: number) => request<void>(`/transactions/${id}`, { method: 'DELETE' }),

  rules: () => request<Rule[]>('/rules'),
  createRule: (body: {
    pattern: string
    match_type: string
    account_id: number
    priority?: number
  }) => request<Rule>('/rules', { method: 'POST', body: JSON.stringify(body) }),
  deleteRule: (id: number) => request<void>(`/rules/${id}`, { method: 'DELETE' }),

  previewImport: (accountId: number, file: File) => {
    const form = new FormData()
    form.set('account_id', String(accountId))
    form.set('file', file)
    return request<ImportPreview>('/imports/preview', { method: 'POST', body: form })
  },
  commitImport: (accountId: number, rows: ImportRow[]) =>
    request<{ created: number; skipped: number; errors: string[] }>('/imports/commit', {
      method: 'POST',
      body: JSON.stringify({ account_id: accountId, rows }),
    }),

  balanceSheet: (asOf?: string) =>
    request<BalanceSheet>(`/reports/balance-sheet${qs({ as_of: asOf })}`),
  incomeStatement: (start?: string, end?: string) =>
    request<IncomeStatement>(`/reports/income-statement${qs({ start, end })}`),
  spendByCategory: (start?: string, end?: string, rollup = true) =>
    request<CategorySpend[]>(`/reports/spend-by-category${qs({ start, end, rollup })}`),
  monthly: (months = 12) => request<MonthlyPoint[]>(`/reports/monthly${qs({ months })}`),
  netWorth: (months = 12) => request<NetWorthPoint[]>(`/reports/net-worth${qs({ months })}`),

  categorize: (limit = 25) =>
    request<CategorizeResponse>('/ai/categorize', {
      method: 'POST',
      body: JSON.stringify({ limit }),
    }),
  applyCategories: (assignments: { transaction_id: number; account_id: number }[]) =>
    request<{ updated: number; errors: string[] }>('/ai/apply', {
      method: 'POST',
      body: JSON.stringify({ assignments }),
    }),
  ask: (question: string) =>
    request<AskResponse>('/ai/ask', { method: 'POST', body: JSON.stringify({ question }) }),
}
