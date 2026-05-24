import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MySetup from './MySetup'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn() },
}))

import { api } from '../lib/api'
const mockedGet = vi.mocked(api.get)

const mockSummary = {
  provider: 'Claude',
  model: 'Sonnet',
  connected_tools: ['GitHub', 'Linear'],
  connected_tools_count: 2,
  top_skills: ['Writing assistant', 'Research', 'Code reviewer'],
  agents_created: 42,
  agentfiles_count: 7,
}

function renderMySetup() {
  return render(
    <MemoryRouter>
      <MySetup />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  })
  global.URL.createObjectURL = vi.fn().mockReturnValue('blob:mock')
  global.URL.revokeObjectURL = vi.fn()
})

describe('MySetup', () => {
  it('renders summary cards after load', async () => {
    mockedGet.mockResolvedValueOnce(mockSummary)
    renderMySetup()

    await waitFor(() => {
      expect(screen.getByText('Claude')).toBeInTheDocument()
    })

    expect(screen.getByText('Your AI assistant')).toBeInTheDocument()
    expect(screen.getByText('Connected tools')).toBeInTheDocument()
    expect(screen.getByText('Agents created')).toBeInTheDocument()
    expect(screen.getByText('Skills installed')).toBeInTheDocument()
  })

  it('shows top skills chips', async () => {
    mockedGet.mockResolvedValueOnce(mockSummary)
    renderMySetup()

    await waitFor(() => {
      expect(screen.getByText('Writing assistant')).toBeInTheDocument()
    })

    expect(screen.getByText('Research')).toBeInTheDocument()
    expect(screen.getByText('Code reviewer')).toBeInTheDocument()
  })

  it('copy button triggers clipboard write', async () => {
    const markdownText = '# My AI Setup\n\n**Your AI assistant:** Claude (Sonnet)'
    mockedGet.mockResolvedValueOnce(mockSummary)
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve(markdownText),
    }) as ReturnType<typeof vi.fn>

    renderMySetup()

    const copyBtn = await waitFor(() => screen.getByTestId('copy-markdown-btn'))
    fireEvent.click(copyBtn)

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(markdownText)
    })
  })

  it('download button triggers fetch call', async () => {
    mockedGet.mockResolvedValueOnce(mockSummary)
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      blob: () => Promise.resolve(new Blob(['{}'], { type: 'application/json' })),
    }) as ReturnType<typeof vi.fn>

    renderMySetup()

    const downloadBtn = await waitFor(() => screen.getByTestId('download-config-btn'))
    fireEvent.click(downloadBtn)

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/my-setup/export?format=json')
    })
  })

  it('shows share text block', async () => {
    mockedGet.mockResolvedValueOnce(mockSummary)
    renderMySetup()

    await waitFor(() => {
      expect(screen.getByTestId('share-text-block')).toBeInTheDocument()
    })

    const shareBlock = screen.getByTestId('share-text-block')
    expect(shareBlock.textContent).toContain('Claude')
    expect(shareBlock.textContent).toContain('Sonnet')
  })

  it('shows error message when fetch fails', async () => {
    mockedGet.mockRejectedValueOnce(new Error('network error'))
    renderMySetup()

    await waitFor(() => {
      expect(screen.getByText(/Couldn't load your setup/)).toBeInTheDocument()
    })
  })
})
