// Tests for DependencyMapWidget (→1452)
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import DependencyMapWidget from './DependencyMapWidget'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return { ...actual, api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }
})

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })),
})

import { api } from '../lib/api'
const mockedApiGet = vi.mocked(api.get)

describe('DependencyMapWidget', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders widget container', () => {
    mockedApiGet.mockResolvedValueOnce({ nodes: [], edges: [] })
    render(<DependencyMapWidget epicKey="PROJ-1" />)
    expect(screen.getByTestId('widget-dependency-map')).toBeInTheDocument()
  })

  it('shows empty state when no dependencies', async () => {
    mockedApiGet.mockResolvedValueOnce({ nodes: [], edges: [] })
    render(<DependencyMapWidget epicKey="PROJ-1" />)
    await waitFor(() => {
      expect(screen.getByTestId('depmap-empty')).toBeInTheDocument()
    })
  })

  it('renders the root epic node', async () => {
    mockedApiGet.mockResolvedValueOnce({
      nodes: [
        { key: 'PROJ-1', summary: 'Root epic', status: 'In Progress', owner: 'Alice' },
        { key: 'PROJ-2', summary: 'Child issue', status: 'Open', owner: 'Bob' },
      ],
      edges: [{ from: 'PROJ-1', to: 'PROJ-2', type: 'blocks' }],
    })
    render(<DependencyMapWidget epicKey="PROJ-1" />)
    await waitFor(() => {
      expect(screen.getByTestId('depmap-root')).toBeInTheDocument()
    })
    expect(screen.getByText('Root epic')).toBeInTheDocument()
  })

  it('renders linked issue in nested list', async () => {
    mockedApiGet.mockResolvedValueOnce({
      nodes: [
        { key: 'EPIC-1', summary: 'Parent', status: 'In Progress', owner: 'Alice' },
        { key: 'PROJ-5', summary: 'Downstream', status: 'Open', owner: 'Bob' },
      ],
      edges: [{ from: 'EPIC-1', to: 'PROJ-5', type: 'blocks' }],
    })
    render(<DependencyMapWidget epicKey="EPIC-1" />)
    await waitFor(() => {
      expect(screen.getByTestId('depmap-node-PROJ-5')).toBeInTheDocument()
    })
    expect(screen.getByText('Downstream')).toBeInTheDocument()
  })
})
