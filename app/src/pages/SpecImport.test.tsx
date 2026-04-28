import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SpecImport from './SpecImport'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

const mockNavigate = vi.fn()

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

import { api } from '../lib/api'
const mockedApiPost = vi.mocked(api.post)

const VALID_YAML = `name: "My feature"
description: "What it does"
tasks:
  - title: "Task 1"
    priority: P1
    acceptance_criteria:
      - "Criterion 1"
  - title: "Task 2"
    priority: P2
`

function renderPage() {
  return render(
    <MemoryRouter>
      <SpecImport />
    </MemoryRouter>
  )
}

describe('SpecImport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNavigate.mockClear()
  })

  it('renders the paste area', () => {
    renderPage()
    expect(screen.getByTestId('yaml-textarea')).toBeInTheDocument()
  })

  it('renders the import button', () => {
    renderPage()
    expect(screen.getByTestId('import-button')).toBeInTheDocument()
  })

  it('import button is disabled when textarea is empty', () => {
    renderPage()
    expect(screen.getByTestId('import-button')).toBeDisabled()
  })

  it('shows preview after pasting valid YAML', async () => {
    renderPage()
    const textarea = screen.getByTestId('yaml-textarea')
    fireEvent.change(textarea, { target: { value: VALID_YAML } })
    await waitFor(() => {
      expect(screen.getByTestId('preview-panel')).toBeInTheDocument()
    })
  })

  it('shows the spec title in the preview', async () => {
    renderPage()
    fireEvent.change(screen.getByTestId('yaml-textarea'), {
      target: { value: VALID_YAML },
    })
    await waitFor(() => {
      expect(screen.getByTestId('preview-title')).toHaveTextContent('My feature')
    })
  })

  it('shows task count in the preview', async () => {
    renderPage()
    fireEvent.change(screen.getByTestId('yaml-textarea'), {
      target: { value: VALID_YAML },
    })
    await waitFor(() => {
      expect(screen.getByTestId('preview-task-count')).toHaveTextContent('2 tasks')
    })
  })

  it('shows acceptance criteria count in the preview', async () => {
    renderPage()
    fireEvent.change(screen.getByTestId('yaml-textarea'), {
      target: { value: VALID_YAML },
    })
    await waitFor(() => {
      expect(screen.getByTestId('preview-ac-count')).toHaveTextContent('1 acceptance criterion')
    })
  })

  it('import button calls POST /api/specs/import', async () => {
    mockedApiPost.mockResolvedValueOnce({ id: 'docs/spec/my-feature.md', path: 'docs/spec/my-feature.md' })
    renderPage()
    fireEvent.change(screen.getByTestId('yaml-textarea'), {
      target: { value: VALID_YAML },
    })
    await waitFor(() => screen.getByTestId('preview-panel'))
    fireEvent.click(screen.getByTestId('import-button'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/api/specs/import',
        expect.objectContaining({ yaml: VALID_YAML, format: 'speckit' })
      )
    })
  })

  it('navigates to /specs after successful import', async () => {
    mockedApiPost.mockResolvedValueOnce({ id: 'docs/spec/my-feature.md', path: 'docs/spec/my-feature.md' })
    renderPage()
    fireEvent.change(screen.getByTestId('yaml-textarea'), {
      target: { value: VALID_YAML },
    })
    await waitFor(() => screen.getByTestId('preview-panel'))
    fireEvent.click(screen.getByTestId('import-button'))
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/specs')
    })
  })

  it('shows error message when import fails', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('Server error'))
    renderPage()
    fireEvent.change(screen.getByTestId('yaml-textarea'), {
      target: { value: VALID_YAML },
    })
    await waitFor(() => screen.getByTestId('preview-panel'))
    fireEvent.click(screen.getByTestId('import-button'))
    await waitFor(() => {
      expect(screen.getByTestId('import-error')).toBeInTheDocument()
    })
  })

  it('clears preview when textarea is cleared', async () => {
    renderPage()
    const textarea = screen.getByTestId('yaml-textarea')
    fireEvent.change(textarea, { target: { value: VALID_YAML } })
    await waitFor(() => screen.getByTestId('preview-panel'))
    fireEvent.change(textarea, { target: { value: '' } })
    await waitFor(() => {
      expect(screen.queryByTestId('preview-panel')).not.toBeInTheDocument()
    })
  })
})
