export interface QueryPreset {
  id: string
  title: string
  source: 'jira' | 'confluence'
  query: string
  emptyText: string
}

export const QUERY_PRESETS: QueryPreset[] = [
  {
    id: 'jira_due_soon',
    title: 'Due soon',
    source: 'jira',
    query: 'assignee = currentUser() AND statusCategory != Done AND due <= 3d ORDER BY due ASC',
    emptyText: 'Nothing due in the next 3 days.',
  },
  {
    id: 'jira_new_on_plate',
    title: 'New on my plate',
    source: 'jira',
    query: 'assignee CHANGED TO currentUser() AFTER -1d AND statusCategory != Done ORDER BY updated DESC',
    emptyText: 'Nothing new landed on you today.',
  },
  {
    id: 'jira_stale_mine',
    title: 'Stale on my plate',
    source: 'jira',
    query: 'assignee = currentUser() AND statusCategory != Done AND updated <= -14d ORDER BY updated ASC',
    emptyText: 'Nothing of yours has gone quiet.',
  },
  {
    id: 'jira_activity_on_mine',
    title: 'Activity on my issues',
    source: 'jira',
    query: '(reporter = currentUser() OR watcher = currentUser()) AND updated >= -2d ORDER BY updated DESC',
    emptyText: 'No recent activity on your issues.',
  },
  {
    id: 'conf_edited_by_me',
    title: 'Pages I edited',
    source: 'confluence',
    query: "contributor = currentUser() AND lastmodified > now('-7d') ORDER BY lastmodified DESC",
    emptyText: "You haven't edited any pages this week.",
  },
  {
    id: 'conf_stale_docs_i_own',
    title: 'My stale docs',
    source: 'confluence',
    query: "creator = currentUser() AND lastmodified < now('-90d') ORDER BY lastmodified ASC",
    emptyText: 'None of your pages have gone stale.',
  },
]

export function getPresetById(id: string): QueryPreset | undefined {
  return QUERY_PRESETS.find((p) => p.id === id)
}
