import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import AgentfileEditor from './AgentfileEditor'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPut = vi.mocked(api.put)

const mockFormData = {
  name: 'my-bot',
  description: 'Does things',
  model: 'claude-sonnet-4-6',
  tools: ['bash', 'read'],
  instructions: 'Be very helpful.',
  tags: [],
  source: 'user',
}

const mockRawData = {
  name: 'my-bot',
  raw: 'FROM claude-sonnet-4-6\nPROMPT "Be very helpful."\nTOOL bash\nTOOL read\n',
}

function renderEditor(name = 'my-bot') {
  return render(
    <MemoryRouter initialEntries={[`/agentfiles/${name}/edit`]}>
      <Routes>
        <Route path="/agentfiles/:name/edit" element={<AgentfileEditor />} />
        <Route path="/agents" element={<div data-testid="agents-page">Agents</div>} />
      </Routes>
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedApiGet.mockImplementation((url: string) => {
    if (url.endsWith('/form')) return Promise.resolve(mockFormData)
    return Promise.resolve(mockRawData)
  })
  mockedApiPut.mockResolvedValue({ updated: 'my-bot' })
})

describe('AgentfileEditor', () => {
  it('renders form fields after loading', async () => {
    renderEditor()
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull())

    expect(screen.getByLabelText('Description')).toHaveValue('Does things')
    expect(screen.getByLabelText('What this agent does')).toHaveValue('Be very helpful.')
    expect(screen.getByLabelText('Model')).toHaveValue('claude-sonnet-4-6')
  })

  it('shows tool checkboxes with correct checked state', async () => {
    renderEditor()
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull())

    const bashCheckbox = screen.getByLabelText('Run commands')
    const readCheckbox = screen.getByLabelText('Read files')
    const writeCheckbox = screen.getByLabelText('Write files')

    expect(bashCheckbox).toBeChecked()
    expect(readCheckbox).toBeChecked()
    expect(writeCheckbox).not.toBeChecked()
  })

  it('YAML toggle shows raw file content', async () => {
    renderEditor()
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull())

    const toggleBtn = screen.getByLabelText('View raw file')
    fireEvent.click(toggleBtn)

    await waitFor(() => {
      expect(screen.getByText(/FROM claude-sonnet-4-6/)).toBeInTheDocument()
    })
  })

  it('YAML toggle switches back to form', async () => {
    renderEditor()
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull())

    const toggleBtn = screen.getByLabelText('View raw file')
    fireEvent.click(toggleBtn)
    await waitFor(() => screen.getByLabelText('Show form'))
    fireEvent.click(screen.getByLabelText('Show form'))

    await waitFor(() => {
      expect(screen.getByLabelText('Description')).toBeInTheDocument()
    })
  })

  it('save button calls PUT with form data', async () => {
    renderEditor()
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull())

    const descInput = screen.getByLabelText('Description')
    fireEvent.change(descInput, { target: { value: 'Updated description' } })

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => {
      expect(mockedApiPut).toHaveBeenCalledWith(
        '/agentfiles/my-bot/form',
        expect.objectContaining({ description: 'Updated description' })
      )
    })
  })

  it('shows saved confirmation after save', async () => {
    renderEditor()
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull())

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument())
  })

  it('shows error message on load failure', async () => {
    mockedApiGet.mockRejectedValue(new Error('network error'))
    renderEditor()
    await waitFor(() => {
      expect(screen.getByText('Could not load this agent setup.')).toBeInTheDocument()
    })
  })

  it('shows read-only notice for builtin agentfiles', async () => {
    mockedApiGet.mockImplementation((url: string) => {
      if (url.endsWith('/form')) return Promise.resolve({ ...mockFormData, source: 'builtin' })
      return Promise.resolve(mockRawData)
    })
    renderEditor()
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull())
    expect(screen.getByText(/built-in setup/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save changes/i })).toBeNull()
  })
})
