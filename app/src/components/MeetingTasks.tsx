import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { LoadingState, EmptyState } from '../components/ui'

interface MeetingTaskPair {
  event: {
    id: string
    summary?: string
    start: { dateTime?: string; date?: string }
    end: { dateTime?: string; date?: string }
  }
  related_tasks: Array<{
    id: string
    title: string
    priority?: string
    relevance_score: number
  }>
}

interface Props {
  meetings?: MeetingTaskPair[]
}

function hasEnded(end?: string): boolean {
  if (!end) return false
  return new Date(end) < new Date()
}

function formatTime(dt?: string): string {
  if (!dt) return ''
  try {
    return new Date(dt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  } catch {
    return ''
  }
}

export default function MeetingTasks({ meetings: propMeetings }: Props) {
  const [meetings, setMeetings] = useState<MeetingTaskPair[]>(propMeetings ?? [])
  const [loading, setLoading] = useState(!propMeetings)
  const [followUps, setFollowUps] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (propMeetings) return
    setLoading(true)
    api.get<MeetingTaskPair[]>('/api/meetings/upcoming-tasks')
      .then((data) => setMeetings(data))
      .catch(() => setMeetings([]))
      .finally(() => setLoading(false))
  }, [])

  async function submitFollowUp(eventId: string) {
    const title = followUps[eventId]?.trim()
    if (!title) return
    setSubmitting(prev => ({ ...prev, [eventId]: true }))
    try {
      await api.post('/api/meetings/' + eventId + '/follow-up', { title })
      setFollowUps(prev => ({ ...prev, [eventId]: '' }))
    } catch {
      // keep input so user can retry
    } finally {
      setSubmitting(prev => ({ ...prev, [eventId]: false }))
    }
  }

  if (loading) return <LoadingState />
  if (!meetings.length) return <EmptyState icon="calendar_today" title="No upcoming meetings" />

  return (
    <div className="space-y-4">
      {meetings.map(({ event, related_tasks }) => {
        const ended = hasEnded(event.end?.dateTime)
        const startTime = formatTime(event.start?.dateTime)
        const endTime = formatTime(event.end?.dateTime)
        return (
          <div key={event.id} className="rounded-lg border border-gray-200 p-4 space-y-3">
            <div>
              <p className="font-medium text-sm">{event.summary ?? 'Untitled meeting'}</p>
              {startTime && (
                <p className="text-xs text-gray-500">
                  {endTime ? startTime + ' – ' + endTime : startTime}
                </p>
              )}
            </div>
            {related_tasks.length > 0 && (
              <ul className="space-y-1">
                {related_tasks.map(task => (
                  <li key={task.id} className="text-xs text-gray-700 flex items-center gap-1">
                    <span className="w-1 h-1 rounded-full bg-blue-400 flex-shrink-0" />
                    {task.title}
                  </li>
                ))}
              </ul>
            )}
            {ended && (
              <div className="pt-1 space-y-1">
                <p className="text-xs text-amber-600">Meeting ended — add follow-ups?</p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="Follow-up task title"
                    value={followUps[event.id] ?? ''}
                    onChange={e => setFollowUps(prev => ({ ...prev, [event.id]: e.target.value }))}
                    onKeyDown={e => { if (e.key === 'Enter') submitFollowUp(event.id) }}
                    className="flex-1 text-xs border rounded px-2 py-1"
                  />
                  <button
                    onClick={() => submitFollowUp(event.id)}
                    disabled={submitting[event.id]}
                    className="text-xs px-2 py-1 rounded bg-blue-500 text-white disabled:opacity-50"
                  >
                    Add
                  </button>
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
