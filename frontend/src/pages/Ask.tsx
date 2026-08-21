import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { Empty, ErrorNote, Money, Panel } from '../components/ui'

const EXAMPLES = [
  'How much did I spend on groceries this year?',
  'What did I earn last month?',
  'Show my spending by category since January',
]

export default function Ask() {
  const [question, setQuestion] = useState('')
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const ask = useMutation({ mutationFn: (q: string) => api.ask(q) })

  const answer = ask.data

  return (
    <div className="space-y-5 max-w-3xl">
      <Panel title="Ask your ledger">
        <form
          className="p-4 space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (question.trim()) ask.mutate(question.trim())
          }}
        >
          <input
            className="field"
            placeholder="How much did I spend on groceries this year?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="flex gap-2 flex-wrap">
            <button className="btn btn-primary" disabled={ask.isPending || !question.trim()}>
              {ask.isPending ? 'Asking…' : 'Ask'}
            </button>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                className="btn text-xs"
                onClick={() => setQuestion(example)}
              >
                {example}
              </button>
            ))}
          </div>
          <p className="text-xs muted">
            The model chooses which accounts and dates to look at. Every figure below is summed
            by the database from your postings.
          </p>
          {health.data && !health.data.ai_enabled && (
            <p className="text-xs muted">
              AI is off — set <code>NVIDIA_API_KEY</code> in <code>backend/.env</code> to enable
              this page. Reports and categorization work without it.
            </p>
          )}
          <ErrorNote error={ask.error} />
        </form>
      </Panel>

      {answer && (
        <Panel title="Answer">
          <div className="p-4 space-y-4">
            <p>{answer.answer}</p>
            <div className="flex items-baseline gap-2">
              <span className="text-xs uppercase tracking-wide muted">Computed total</span>
              <span className="text-2xl">
                <Money minor={answer.total_minor} />
              </span>
            </div>

            {answer.rows.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Group</th>
                    <th className="text-right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {answer.rows.map((row) => (
                    <tr key={row.label}>
                      <td>{row.label}</td>
                      <td className="text-right">
                        <Money minor={row.amount_minor} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>No postings matched that query.</Empty>
            )}

            <details className="text-xs muted">
              <summary className="cursor-pointer">Query the model chose</summary>
              <pre className="mt-2 overflow-x-auto">{JSON.stringify(answer.query, null, 2)}</pre>
            </details>
          </div>
        </Panel>
      )}
    </div>
  )
}
