import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import NotificationToasts from './NotificationToast'
import { useNotificationStore } from '../stores/notifications'

beforeEach(() => {
  useNotificationStore.setState({
    notifications: [],
    toastIds: [],
    firedKeys: new Set<string>(),
    persistentToastIds: new Set<string>(),
  })
})

describe('NotificationToasts', () => {
  it('test_roadmap_toast_renders_human_body_not_raw_type', () => {
    // Regression: users were seeing "Agent roadmap_ready" because the
    // toast fell back to the default ``Agent ${statusMessage(status)}``
    // line when the status was an unknown persistent type. The fix
    // reads ``notification.body`` first, which carries the backend's
    // human-readable message.
    useNotificationStore.getState().addPersistentToast({
      id: 'roadmap-toast',
      type: 'roadmap_ready',
      title: 'Roadmap ready',
      body: "Open roadmap.md. Type 'create tasks' in chat to break it down.",
      action_url: '/files',
    })

    render(<NotificationToasts />)

    expect(screen.getByText('Roadmap ready')).toBeInTheDocument()
    expect(
      screen.getByText(
        "Open roadmap.md. Type 'create tasks' in chat to break it down.",
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Agent roadmap_ready/)).not.toBeInTheDocument()
  })

  it('test_agent_status_toast_still_falls_back_to_generic_message', () => {
    // Agent status-change toasts leave ``body`` undefined and must still
    // render "Agent finished" using the existing statusMessage helper.
    useNotificationStore.getState().addNotification(
      { name: 'my-agent', source: 'claude-code' },
      'running',
      'completed',
    )

    render(<NotificationToasts />)

    expect(screen.getByText('my-agent')).toBeInTheDocument()
    expect(screen.getByText('Agent finished')).toBeInTheDocument()
  })

  it('test_persistent_toast_without_body_shows_generic_fallback', () => {
    // Defensive path: if a persistent notification arrives with an empty
    // body and an unknown status, the toast must never surface the raw
    // type string. Show a neutral "New notification" instead.
    useNotificationStore.getState().addPersistentToast({
      id: 'empty-body',
      type: 'some_other_type',
      title: 'Something happened',
      body: '',
      action_url: null,
    })

    render(<NotificationToasts />)

    expect(screen.getByText('Something happened')).toBeInTheDocument()
    expect(
      screen.queryByText(/Agent some_other_type/),
    ).not.toBeInTheDocument()
    expect(screen.getByText('New notification')).toBeInTheDocument()
  })
})
