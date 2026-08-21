import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api'

const mockFetch = (body: unknown, status = 200) =>
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: status < 400,
      status,
      statusText: 'Error',
      json: async () => body,
    })),
  )

afterEach(() => vi.unstubAllGlobals())

describe('api error handling', () => {
  it('surfaces a FastAPI string detail', async () => {
    mockFetch({ detail: 'postings do not balance to zero: USD off by 100' }, 422)
    await expect(api.health()).rejects.toThrow(/do not balance/)
  })

  it('joins a validation error list', async () => {
    mockFetch({ detail: [{ msg: 'too short' }, { msg: 'wrong type' }] }, 422)
    await expect(api.health()).rejects.toThrow('too short; wrong type')
  })

  it('keeps the status code on the error', async () => {
    mockFetch({ detail: 'AI is not configured' }, 503)
    await expect(api.health()).rejects.toMatchObject({ status: 503 })
    expect(new ApiError('x', 503).status).toBe(503)
  })

  it('returns parsed json on success', async () => {
    mockFetch({ balanced: true })
    await expect(api.health()).resolves.toEqual({ balanced: true })
  })
})
