import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import JiraCommentComposer from './JiraCommentComposer'

vi.mock('../lib/api', () => ({
  api: {
    post: vi.fn(),
  },
  ApiError: class ApiError extends Error {},
}))

import { api } from '../lib/api'

const mockedApiPost = vi.mocked(api.post)

function renderComposer(overrides: Partial<React.ComponentProps<typeof JiraCommentComposer>> = {}) {
  const props = {
    issueKey: 'PROJ-1',
    onCancel: vi.fn(),
    onSent: vi.fn(),
    ...overrides,
  }
  return { ...render(<JiraCommentComposer {...props} />), props }
}

describe('JiraCommentComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders textarea and Send/Cancel buttons', () => {
    renderComposer()
    expect(screen.getByTestId('jira-comment-textarea')).toBeInTheDocument()
    expect(screen.getByTestId('jira-comment-send')).toBeInTheDocument()
    expect(screen.getByTestId('jira-comment-cancel')).toBeInTheDocument()
  })

  it('Send button is disabled when textarea is empty', () => {
    renderComposer()
    expect(screen.getByTestId('jira-comment-send')).toBeDisabled()
  })

  it('calls api.post with correct URL and body on submit', async () => {
    mockedApiPost.mockResolvedValue({ ok: true })
    const { props } = renderComposer()
    fireEvent.change(screen.getByTestId('jira-comment-textarea'), {
      target: { value: 'hi' },
    })
    fireEvent.click(screen.getByTestId('jira-comment-send'))
    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith(
        '/atlassian/jira/issue/PROJ-1/comment',
        { body: 'hi' }
      )
    })
    expect(props.onSent).toHaveBeenCalled()
  })

  it('calls onSent on successful post', async () => {
    mockedApiPost.mockResolvedValue({ ok: true })
    const { props } = renderComposer()
    fireEvent.change(screen.getByTestId('jira-comment-textarea'), {
      target: { value: 'Done' },
    })
    fireEvent.click(screen.getByTestId('jira-comment-send'))
    await waitFor(() => {
      expect(props.onSent).toHaveBeenCalledTimes(1)
    })
  })

  it('shows error message on failed post', async () => {
    mockedApiPost.mockResolvedValue({ ok: false, error: 'Permission denied' })
    renderComposer()
    fireEvent.change(screen.getByTestId('jira-comment-textarea'), {
      target: { value: 'A comment' },
    })
    fireEvent.click(screen.getByTestId('jira-comment-send'))
    await waitFor(() => {
      expect(screen.getByText('Permission denied')).toBeInTheDocument()
    })
  })

  it('Cancel button calls onCancel', () => {
    const { props } = renderComposer()
    fireEvent.click(screen.getByTestId('jira-comment-cancel'))
    expect(props.onCancel).toHaveBeenCalledTimes(1)
  })
})
