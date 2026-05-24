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
