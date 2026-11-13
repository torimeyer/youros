import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ReceiptPanel, { type ReceiptData } from './ReceiptPanel'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

const verifiedReceipt: ReceiptData = {
  verified: true,
  commits: [
    {
      sha: 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678',
      short_sha: 'a1b2c3d4',
      subject: 'feat(receipts): implement verifier (→42)',
      timestamp: '2026-11-13T20:05:00Z',
    },
  ],
  diff_stat: ' api/services/git_receipts.py | 120 ++++++++++++\n 1 file changed, 120 insertions(+)',
  changed_files: ['api/services/git_receipts.py'],
  trace_linked: true,
  test_runs: [
    {
      command: 'pytest api/tests/test_git_receipts.py',
      exit_code: 0,
      timestamp: '2026-11-13T20:06:00Z',
    },
  ],
  missing_reasons: [],
  branch_name: 'feat/w1-git-receipts',
}

const failedReceipt: ReceiptData = {
  verified: false,
  commits: [],
  diff_stat: '',
  changed_files: [],
  trace_linked: false,
  test_runs: [
    {
      command: 'pytest api/tests/test_git_receipts.py',
      exit_code: 1,
      timestamp: '2026-11-13T20:04:00Z',
    },
  ],
  missing_reasons: [
    'No git commits found ahead of base commit',
    'No ostk trace or commit attribution linking task',
    'Latest recorded test command exited non-zero',
  ],
}

describe('ReceiptPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiPost.mockResolvedValue({ ok: true })
  })

  it('renders Verified state with commit SHA, badges, and triggers Merge and Reap Worktree', async () => {
    const onActionComplete = vi.fn()
    render(
      <ReceiptPanel
        taskId="→42"
        agentName="builder-42"
        receipt={verifiedReceipt}
        onActionComplete={onActionComplete}
      />
    )

    expect(screen.getByTestId('receipt-panel')).toBeInTheDocument()
    expect(screen.getByTestId('receipt-status-verified')).toHaveTextContent('Verified')
    expect(screen.getByText('a1b2c3d4')).toBeInTheDocument()
    expect(screen.getByText(/implement verifier/)).toBeInTheDocument()
    expect(screen.getByTestId('receipt-diff-stat')).toHaveTextContent('git_receipts.py')
    expect(screen.getByTestId('receipt-trace-badge')).toHaveTextContent(/ostk trace/i)
    expect(screen.getByTestId('receipt-test-badge')).toHaveTextContent(/exit 0/i)

    const mergeBtn = screen.getByTestId('receipt-merge-reap-btn')
    expect(mergeBtn).not.toBeDisabled()
    fireEvent.click(mergeBtn)

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/builder-42/merge-and-reap', {})
    })
    expect(onActionComplete).toHaveBeenCalledTimes(1)
  })

  it('renders Failed state with missing_reasons, Reset to Save Point, and Pin Decision submit', async () => {
    const onActionComplete = vi.fn()
    render(
      <ReceiptPanel
        taskId="→42"
        agentName="builder-42"
        receipt={failedReceipt}
        onActionComplete={onActionComplete}
      />
    )

    expect(screen.getByTestId('receipt-status-unverified')).toHaveTextContent('Missing Proof')
    expect(screen.getByText('No git commits found ahead of base commit')).toBeInTheDocument()
    expect(screen.getByText('Latest recorded test command exited non-zero')).toBeInTheDocument()

    // Merge and Reap must be disabled when unverified
    expect(screen.getByTestId('receipt-merge-reap-btn')).toBeDisabled()

    // Reset to Save Point works when unverified
    fireEvent.click(screen.getByTestId('receipt-reset-save-point-btn'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/builder-42/reset-save-point', {})
    })
    expect(onActionComplete).toHaveBeenCalledTimes(1)

    // Pin Decision inline key/value submit
    fireEvent.change(screen.getByTestId('pin-decision-key-input'), {
      target: { value: 'db_driver' },
    })
    fireEvent.change(screen.getByTestId('pin-decision-value-input'), {
      target: { value: 'sqlite_wal' },
    })
    fireEvent.click(screen.getByTestId('pin-decision-submit-btn'))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith('/agents/builder-42/pin-decision', {
        key: 'db_driver',
        value: 'sqlite_wal',
      })
    })
  })

  it('fetches /tasks/:taskId/receipt when receipt prop is omitted and handles errors gracefully', async () => {
    mockedApiGet.mockResolvedValueOnce({ ...verifiedReceipt, agent_name: 'auto-agent-7' })

    render(<ReceiptPanel taskId="42" />)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/42/receipt')
    })
    await waitFor(() => {
      expect(screen.getByTestId('receipt-status-verified')).toBeInTheDocument()
    })
    expect(screen.getByTestId('receipt-merge-reap-btn')).toBeInTheDocument()
  })

  it('never crashes when receipt fetch rejects or returns unshaped payload', async () => {
    mockedApiGet.mockRejectedValueOnce(new Error('network failure'))
    const { rerender } = render(<ReceiptPanel taskId="99" />)

    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/99/receipt')
    })
    expect(screen.getByTestId('receipt-panel')).toBeInTheDocument()

    mockedApiGet.mockResolvedValueOnce({})
    rerender(<ReceiptPanel taskId="100" />)
    await waitFor(() => {
      expect(mockedApiGet).toHaveBeenCalledWith('/tasks/100/receipt')
    })
    expect(screen.getByTestId('receipt-panel')).toBeInTheDocument()
  })
})
