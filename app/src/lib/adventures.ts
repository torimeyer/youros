export interface AdventureTemplate {
  id: string
  title: string
  tagline: string
  icon: string
  placeholder: string
}

export interface AdventurePlan {
  adventure_id: string
  goal: { title: string; description: string }
  tasks: { title: string; priority: string }[]
}

/** localStorage key used to remember that the user dismissed the Adventure card. */
export const ADVENTURE_DISMISSED_KEY = 'myos_adventure_dismissed'

/**
 * →2920: the "One thing to try right now" suggestion. It used to live in the
 * onboarding wizard's Showcase step; the dashboard's try-an-adventure card
 * shows it now. The pick depends on which tools are connected.
 */
export interface TryNowSuggestion {
  prompt: string
  label: string
  description: string
}

export function pickTryNowSuggestion(googleConnected: boolean, atlassianConnected: boolean): TryNowSuggestion {
  const connectedCount = (googleConnected ? 1 : 0) + (atlassianConnected ? 1 : 0)
  if (connectedCount >= 2) {
    return {
      prompt: 'Search across my connected tools for anything about our last product launch and summarize what you find.',
      label: 'Search across your tools',
      description: 'yourOS can search your connected tools at the same time and bring back one answer.',
    }
  }
  if (googleConnected) {
    return {
      prompt: 'What meetings do I have today, and what should I prepare for each one?',
      label: "Prep for today's meetings",
      description: 'yourOS reads your calendar and helps you walk into every meeting ready.',
    }
  }
  return {
    prompt: 'Show me the tasks and agents I have running right now.',
    label: 'See your tasks and agents',
    description: 'Every task and agent you start stays here between sessions. Nothing disappears when you close the tab.',
  }
}
