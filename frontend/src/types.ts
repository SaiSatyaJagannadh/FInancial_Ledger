export type AccountType = 'asset' | 'liability' | 'equity' | 'income' | 'expense'

export interface Account {
  id: number
  code: string
  name: string
  type: AccountType
  currency: string
  parent_id: number | null
  archived: boolean
  balance_minor: number
  balance: string
  rollup_minor: number
  rollup: string
}

export interface Posting {
  id: number
  account_id: number
  amount_minor: number
  currency: string
  amount: string
}

export interface Transaction {
  id: number
  date: string
  description: string
  memo: string | null
  source: string
  external_id: string | null
  postings: Posting[]
}

export interface Rule {
  id: number
  pattern: string
  match_type: 'contains' | 'regex'
  account_id: number
  priority: number
  active: boolean
}

export interface ImportRow {
  date: string
  description: string
  amount_minor: number
  external_id: string
  suggested_account_id: number | null
  suggested_account_code: string | null
  duplicate: boolean
}

export interface ImportPreview {
  rows: ImportRow[]
  total: number
  duplicates: number
  errors: string[]
}

export interface ReportLine {
  account_id: number
  code: string
  name: string
  type: AccountType
  amount_minor: number
  amount: string
}

export interface BalanceSheet {
  as_of: string
  assets: ReportLine[]
  liabilities: ReportLine[]
  equity: ReportLine[]
  total_assets_minor: number
  total_liabilities_minor: number
  total_equity_minor: number
  balanced: boolean
}

export interface IncomeStatement {
  start: string
  end: string
  income: ReportLine[]
  expenses: ReportLine[]
  total_income_minor: number
  total_expenses_minor: number
  net_minor: number
}

export interface CategorySpend {
  account_id: number
  code: string
  name: string
  amount_minor: number
  amount: string
}

export interface MonthlyPoint {
  month: string
  income_minor: number
  expenses_minor: number
  net_minor: number
}

export interface NetWorthPoint {
  month: string
  assets_minor: number
  liabilities_minor: number
  net_worth_minor: number
}

export interface Health {
  status: string
  trial_balance_minor: number
  balanced: boolean
  accounts: number
  transactions: number
  ai_enabled: boolean
}

export interface Suggestion {
  transaction_id: number
  description: string
  account_id: number
  account_code: string
  confidence: number
  reason: string
  source: 'rule' | 'heuristic' | 'llm'
}

export interface CategorizeResponse {
  suggestions: Suggestion[]
  source: string
  note: string | null
}

export interface AskResponse {
  question: string
  answer: string
  query: Record<string, unknown>
  total_minor: number
  total: string
  rows: { label: string; amount_minor: number; amount: number }[]
}
