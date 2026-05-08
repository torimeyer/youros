import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import Icon from '../components/Icon'
import TopBar from '../components/TopBar'
import { ConnectCard, LoadingState, EmptyState } from '../components/ui'
import { api } from '../lib/api'
import SlackReplyComposer from '../components/SlackReplyComposer'
import { notifyInboxChange } from '../lib/sidebarBus'

interface SlackChannel {
  id: string
  name: string
  is_private: boolean
  num_members: number
  topic: string
}

interface SlackMessage {
  ts: string
  user: string
  text: string
  type: string
}

interface SlackStatus {
  connected: boolean
  team_name: string
  team_id: string
  configured: boolean
}

// Seed from localStorage for instant paint — keyed by team_id to prevent
// stale channels from a prior workspace showing after reconnect (→1063).
const SLACK_CHANNELS_CACHE_KEY = 'myos.slackChannels.v2'

interface ChannelCacheEntry {
  team_id: string
  channels: SlackChannel[]
}

function readChannelCache(): ChannelCacheEntry | null {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return null
    const raw = window.localStorage.getItem(SLACK_CHANNELS_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || !Array.isArray(parsed.channels)) return null
    return { team_id: parsed.team_id || '', channels: parsed.channels }
  } catch {
    return null
  }
}

function writeChannelCache(team_id: string, channels: SlackChannel[]) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(SLACK_CHANNELS_CACHE_KEY, JSON.stringify({ team_id, channels }))
  } catch {
    // not fatal
  }
}

function clearChannelCache() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.removeItem(SLACK_CHANNELS_CACHE_KEY)
  } catch {
    // not fatal
  }
}

function stripSlackMrkdwn(text: string): string {
  return text.replace(/<([^|>]+)\|([^>]+)>/g, '$2').replace(/<([^>]+)>/g, '$1')
}

export default function Slack() {
  const [searchParams] = useSearchParams()
  const [status, setStatus] = useState<SlackStatus | null>(null)
  const [channels, setChannels] = useState<SlackChannel[]>(() => readChannelCache()?.channels ?? [])
  const [loading, setLoading] = useState<boolean>(() => (readChannelCache()?.channels ?? []).length === 0)
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null)
  const [messages, setMessages] = useState<SlackMessage[]>([])
  const [messagesLoading, setMessagesLoading] = useState(false)
  const [newMessage, setNewMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [flaggedTs, setFlaggedTs] = useState<Set<string>>(new Set())
  const [replyOpenTs, setReplyOpenTs] = useState<string | null>(null)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [configuring, setConfiguring] = useState(false)
  const [configureError, setConfigureError] = useState<string | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get<SlackStatus>('/slack/status')
      setStatus(res)
    } catch {
      setStatus({ connected: false, team_name: '', team_id: '', configured: false })
    }
  }, [])

  const fetchChannels = useCallback(async (teamId?: string) => {
    try {
      const res = await api.get<{ channels: SlackChannel[] }>('/slack/channels')
      setChannels(res.channels || [])
      writeChannelCache(teamId ?? '', res.channels || [])
    } catch {
      setChannels((prev) => (prev.length > 0 ? prev : []))
    }
  }, [])

  const fetchMessages = useCallback(async (channelId: string) => {
    setMessagesLoading(true)
    try {
      const res = await api.get<{ messages: SlackMessage[] }>(`/slack/messages/${channelId}`)
      setMessages(res.messages || [])
    } catch {
      setMessages([])
    } finally {
      setMessagesLoading(false)
    }
  }, [])

  useEffect(() => {
    const cached = readChannelCache()
    const hasCached = (cached?.channels ?? []).length > 0
    if (!hasCached) setLoading(true)
    ;(async () => {
      try {
        const s = await api.get<SlackStatus>('/slack/status')
        setStatus(s)
        if (s.connected) {
          // Discard cached channels immediately if the workspace changed (→1063)
          if (cached?.team_id && s.team_id && cached.team_id !== s.team_id) {
            setChannels([])
            clearChannelCache()
          }
          await fetchChannels(s.team_id)
        } else {
          setChannels([])
          clearChannelCache()
        }
      } catch {
        setStatus({ connected: false, team_name: '', team_id: '', configured: false })
      }
      setLoading(false)
    })()
  }, [fetchChannels])

  // Handle ?connected=true redirect — re-fetch status to get the new team_id
  // so we can validate the cache and write channels under the correct workspace.
  useEffect(() => {
    if (searchParams.get('connected') === 'true') {
      ;(async () => {
        try {
          const s = await api.get<SlackStatus>('/slack/status')
          setStatus(s)
          const cached = readChannelCache()
          if (cached?.team_id && s.team_id && cached.team_id !== s.team_id) {
            setChannels([])
            clearChannelCache()
          }
          await fetchChannels(s.team_id)
        } catch {
          // ignore
        }
      })()
    }
  }, [searchParams, fetchChannels])

  const handleConnect = async () => {
    setConnectError(null)
    try {
      const res = await api.get<{ url: string }>('/slack/auth')
      window.location.href = res.url
    } catch {
      setConnectError('Could not start the Slack connection. Make sure Slack is configured in your .env file (SLACK_CLIENT_ID and SLACK_CLIENT_SECRET).')
    }
  }

  const handleConfigure = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!clientId.trim() || !clientSecret.trim()) return
    setConfiguring(true)
    setConfigureError(null)
    try {
      await api.post('/slack/configure', { client_id: clientId.trim(), client_secret: clientSecret.trim() })
      await fetchStatus()
    } catch {
      setConfigureError('Could not save Slack credentials. Check that they are correct and try again.')
    } finally {
      setConfiguring(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      await api.delete('/slack/disconnect')
      setStatus({ connected: false, team_name: '', team_id: '', configured: false })
      setChannels([])
      clearChannelCache()
      setSelectedChannel(null)
      setMessages([])
    } catch {
      // ignore
    }
  }

  const handleSelectChannel = (channelId: string) => {
    setSelectedChannel(channelId)
    fetchMessages(channelId)
  }

  const handleSend = async () => {
    if (!selectedChannel || !newMessage.trim()) return
    setSending(true)
    try {
      await api.post('/slack/send', { channel_id: selectedChannel, text: newMessage })
      setNewMessage('')
      await fetchMessages(selectedChannel)
    } catch {
      // ignore
    } finally {
      setSending(false)
    }
  }

  const handleFlag = async (msg: SlackMessage) => {
    if (!selectedChannel) return
    const channelName = channels.find((c) => c.id === selectedChannel)?.name || ''
    try {
      await api.post('/slack/followups', {
        channel_id: selectedChannel,
        channel_name: channelName,
        ts: msg.ts,
        user: msg.user,
        text: msg.text,
      })
      setFlaggedTs((prev) => new Set([...prev, msg.ts]))
      notifyInboxChange()
    } catch {
      // ignore
    }
  }

  const handleCreateTask = async (msg: SlackMessage) => {
    const channelName = channels.find((c) => c.id === selectedChannel)?.name || ''
    const title = msg.text.length > 80 ? msg.text.slice(0, 77) + '...' : msg.text
    try {
      await api.post('/tasks', {
        title: `[Slack #${channelName}] ${title}`,
        priority: 'P2',
        description: `From Slack #${channelName}:\n\n${msg.text}`,
      })
    } catch {
      // ignore
    }
  }

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-4 rounded-xl'

  if (loading) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Slack" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <LoadingState variant="spinner" />
        </div>
      </div>
    )
  }

  if (!status?.connected) {
    return (
      <div className="min-h-dvh bg-slate-950 text-white">
        <TopBar title="Slack" />
        <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
          <ConnectCard
            icon="chat"
            accentColor="#a855f7"
            title="Connect Slack"
            description="See your Slack messages, reply to conversations, and create tasks from messages without leaving myOS."
            primaryAction={
              status?.configured ? (
                <button
                  onClick={handleConnect}
                  className="w-full py-3 bg-purple-600 hover:bg-purple-700 rounded-xl font-medium transition-colors"
                >
                  Connect Slack workspace
                </button>
              ) : (
                <form onSubmit={handleConfigure} className="text-sm text-left space-y-3">
                  <div>
                    <label htmlFor="slack-client-id" className="block text-slate-400 mb-1">Client ID</label>
                    <input
                      id="slack-client-id"
                      type="text"
                      value={clientId}
                      onChange={(e) => setClientId(e.target.value)}
                      placeholder="Your Slack app client ID"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                    />
                  </div>
                  <div>
                    <label htmlFor="slack-client-secret" className="block text-slate-400 mb-1">Client Secret</label>
                    <input
                      id="slack-client-secret"
                      type="password"
                      value={clientSecret}
                      onChange={(e) => setClientSecret(e.target.value)}
                      placeholder="Your Slack app client secret"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                    />
                  </div>
                  {configureError && (
                    <p className="text-red-400 text-xs">{configureError}</p>
                  )}
                  <button
                    type="submit"
                    disabled={configuring || !clientId.trim() || !clientSecret.trim()}
                    className="w-full py-2 bg-purple-600 hover:bg-purple-700 rounded-lg font-medium transition-colors disabled:opacity-50"
                  >
                    {configuring ? 'Saving...' : 'Save'}
                  </button>
                  <p className="text-slate-500 text-xs">
                    Create a Slack app at{' '}
                    <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer" className="text-purple-400 hover:text-purple-300">
                      api.slack.com/apps
                    </a>
                  </p>
                </form>
              )
            }
            error={connectError ?? undefined}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Slack" />
      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl sm:text-2xl font-bold">Slack</h1>
              {status.team_name && (
                <span className="px-2 py-0.5 bg-purple-500/20 text-purple-400 text-sm font-semibold rounded-full">
                  {status.team_name}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={handleDisconnect}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm transition-colors text-slate-400"
          >
            <Icon name="link_off" size={16} />
            Disconnect
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Channel list */}
          <div className={cardClass}>
            <div className="flex items-center gap-2 mb-4">
              <Icon name="tag" className="text-purple-400" size={18} />
              <h2 className="text-base font-semibold">Channels</h2>
            </div>
            {channels.length === 0 ? (
              <EmptyState icon="tag" title="No channels yet" description="Sync to pull your Slack channels." />
            ) : (
              <div className="space-y-1 max-h-[60vh] overflow-y-auto">
                {channels.map((ch) => (
                  <button
                    key={ch.id}
                    onClick={() => handleSelectChannel(ch.id)}
                    className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                      selectedChannel === ch.id
                        ? 'bg-purple-500/20 text-purple-300'
                        : 'text-slate-300 hover:bg-slate-800/60'
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <Icon name={ch.is_private ? 'lock' : 'tag'} size={14} className="text-slate-500" />
                      {ch.name}
                    </span>
                    {ch.topic && (
                      <span className="text-xs text-slate-500 truncate block mt-0.5">{stripSlackMrkdwn(ch.topic)}</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Messages */}
          <div className={`${cardClass} lg:col-span-2`}>
            <div className="flex items-center gap-2 mb-4">
              <Icon name="forum" className="text-purple-400" size={18} />
              <h2 className="text-base font-semibold">
                {selectedChannel
                  ? `#${channels.find((c) => c.id === selectedChannel)?.name || ''}`
                  : 'Messages'}
              </h2>
            </div>

            {!selectedChannel ? (
              <EmptyState icon="forum" title="Select a channel to read messages" />
            ) : messagesLoading ? (
              <LoadingState variant="spinner" message="Loading messages..." />
            ) : messages.length === 0 ? (
              <EmptyState icon="forum" title="No messages in this channel yet" />
            ) : (
              <>
                <div className="space-y-3 max-h-[50vh] overflow-y-auto mb-4">
                  {[...messages].reverse().map((msg) => (
                    <div key={msg.ts}>
                      <div className="group flex items-start gap-3 px-2 py-2 rounded-lg hover:bg-slate-800/40">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-0.5">
                            <span className="text-sm font-medium text-slate-200">{msg.user || 'Unknown'}</span>
                            <span className="text-xs text-slate-500">
                              {new Date(parseFloat(msg.ts) * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                            </span>
                          </div>
                          <p className="text-sm text-slate-300 whitespace-pre-wrap">{msg.text}</p>
                        </div>
                        <div className="shrink-0 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-all">
                          <button
                            data-testid={`slack-flag-${msg.ts}`}
                            onClick={() => handleFlag(msg)}
                            title="Flag for follow-up"
                            className="p-1.5 rounded-lg hover:bg-slate-700 transition-colors"
                          >
                            <Icon
                              name={flaggedTs.has(msg.ts) ? 'flag' : 'flag'}
                              size={16}
                              className={flaggedTs.has(msg.ts) ? 'text-amber-400' : 'text-slate-400'}
                            />
                          </button>
                          <button
                            data-testid={`slack-reply-${msg.ts}`}
                            onClick={() =>
                              setReplyOpenTs((prev) => (prev === msg.ts ? null : msg.ts))
                            }
                            title="Reply"
                            className="p-1.5 rounded-lg hover:bg-slate-700 transition-colors"
                          >
                            <Icon name="reply" size={16} className="text-slate-400" />
                          </button>
                          <button
                            onClick={() => handleCreateTask(msg)}
                            title="Create task from this message"
                            className="p-1.5 rounded-lg hover:bg-slate-700 transition-colors"
                          >
                            <Icon name="add_task" size={16} className="text-slate-400" />
                          </button>
                        </div>
                      </div>
                      {replyOpenTs === msg.ts && selectedChannel && (
                        <SlackReplyComposer
                          channelId={selectedChannel}
                          ts={msg.ts}
                          onCancel={() => setReplyOpenTs(null)}
                          onSent={() => {
                            setReplyOpenTs(null)
                            fetchMessages(selectedChannel)
                          }}
                        />
                      )}
                    </div>
                  ))}
                </div>

                {/* Compose */}
                <div className="flex items-center gap-2 pt-3 border-t border-slate-800">
                  <input
                    type="text"
                    value={newMessage}
                    onChange={(e) => setNewMessage(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault()
                        handleSend()
                      }
                    }}
                    placeholder="Type a message..."
                    className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                  />
                  <button
                    onClick={handleSend}
                    disabled={sending || !newMessage.trim()}
                    className="px-3 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                  >
                    <Icon name="send" size={16} />
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
