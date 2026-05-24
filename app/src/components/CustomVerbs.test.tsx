import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import CustomVerbs from './CustomVerbs'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      delete: vi.fn(),
    },
  }
})

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)
const mockedApiDelete = vi.mocked(api.delete)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CustomVerbs — list', () => {
  it('renders verbs from GET response', async () => {
    mockedApiGet.mockResolvedValue({ verbs: [
      { name: 'deploy', description: 'Deploy the app' },
      { name: 'test', description: 'Run tests' },
    ]})

    render(<CustomVerbs />)

    await waitFor(() => {
      expect(screen.getByTestId('verb-row-deploy')).toBeInTheDocument()
    })
    expect(screen.getByText('deploy')).toBeInTheDocument()
    expect(screen.getByText('Deploy the app')).toBeInTheDocument()
    expect(screen.getByTestId('verb-row-test')).toBeInTheDocument()
  })

  it('shows empty state when list is empty', async () => {
    mockedApiGet.mockResolvedValue({ verbs: [] })

    render(<CustomVerbs />)

    await waitFor(() => {
      expect(screen.getByTestId('custom-verbs-empty')).toBeInTheDocument()
    })
    expect(screen.getByText('No custom commands yet.')).toBeInTheDocument()
  })
})

describe('CustomVerbs — add', () => {
  it('submits POST with correct body and refreshes list', async () => {
    mockedApiGet
      .mockResolvedValueOnce({ verbs: [] })
      .mockResolvedValueOnce({ verbs: [{ name: 'greet', description: 'Say hello' }] })
    mockedApiPost.mockResolvedValue({})

    render(<CustomVerbs />)

    await waitFor(() => screen.getByTestId('custom-verbs-empty'))

    fireEvent.change(screen.getByTestId('verb-name-input'), { target: { value: 'greet' } })
    fireEvent.change(screen.getByTestId('verb-desc-input'), { target: { value: 'Say hello' } })
    fireEvent.click(screen.getByTestId('verb-add-btn'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/ostk/language/verb', { name: 'greet', description: 'Say hello' })
    })
    await waitFor(() => {
      expect(screen.getByTestId('verb-row-greet')).toBeInTheDocument()
    })
  })

  it('shows error and does not submit when name is invalid', async () => {
    mockedApiGet.mockResolvedValue({ verbs: [] })

    render(<CustomVerbs />)

    await waitFor(() => screen.getByTestId('custom-verbs-empty'))

    fireEvent.change(screen.getByTestId('verb-name-input'), { target: { value: '123bad' } })
    fireEvent.change(screen.getByTestId('verb-desc-input'), { target: { value: 'Does something' } })
    fireEvent.click(screen.getByTestId('verb-add-btn'))

    expect(screen.getByTestId('verb-name-error')).toBeInTheDocument()
    expect(screen.getByText(/Name can only contain letters, numbers, hyphens, and underscores/i)).toBeInTheDocument()
    expect(mockedApiPost).not.toHaveBeenCalled()
  })

  it('shows error and does not submit when name starts with a number', async () => {
    mockedApiGet.mockResolvedValue({ verbs: [] })

    render(<CustomVerbs />)

    await waitFor(() => screen.getByTestId('custom-verbs-empty'))

    fireEvent.change(screen.getByTestId('verb-name-input'), { target: { value: '9foo' } })
    fireEvent.click(screen.getByTestId('verb-add-btn'))

    expect(screen.getByTestId('verb-name-error')).toBeInTheDocument()
    expect(mockedApiPost).not.toHaveBeenCalled()
  })
})

describe('CustomVerbs — remove', () => {
  it('calls DELETE and removes verb from list', async () => {
    mockedApiGet
      .mockResolvedValueOnce({ verbs: [{ name: 'deploy', description: 'Deploy the app' }] })
      .mockResolvedValueOnce({ verbs: [] })
    mockedApiDelete.mockResolvedValue({})

    render(<CustomVerbs />)

    await waitFor(() => screen.getByTestId('verb-row-deploy'))

    fireEvent.click(screen.getByTestId('remove-verb-deploy'))

    await waitFor(() => {
      expect(mockedApiDelete).toHaveBeenCalledWith('/ostk/language/verb/deploy')
    })
    await waitFor(() => {
      expect(screen.getByTestId('custom-verbs-empty')).toBeInTheDocument()
    })
  })
})
