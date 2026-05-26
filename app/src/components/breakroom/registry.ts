import { lazy, type ComponentType } from 'react'

export type GameLane = 'logic' | 'retro'

export interface GameDef {
  id: string
  name: string
  icon: string // Material Symbols name
  lane: GameLane
  blurb: string
  component: ComponentType
}

// Single source of truth. Adding a game = append one entry + drop in a
// default-exported component. Components are lazy() so they stay out of the
// main bundle until a tile is clicked (mirrors React.lazy in pages/Activity.tsx).
export const GAMES: GameDef[] = [
  {
    id: 'set',
    name: 'Set',
    icon: 'style',
    lane: 'logic',
    blurb: 'Spot three cards that are all alike or all different.',
    component: lazy(() => import('./games/Set')),
  },
  {
    id: 'solitaire',
    name: 'Solitaire',
    icon: 'playing_cards',
    lane: 'logic',
    blurb: 'Stack down by alternating color, build each suit up from the ace.',
    component: lazy(() => import('./games/Solitaire')),
  },
  {
    id: 'minesweeper',
    name: 'Minesweeper',
    icon: 'grid_on',
    lane: 'logic',
    blurb: 'Clear the field without detonating a mine. Flag what you suspect.',
    component: lazy(() => import('./games/Minesweeper')),
  },
  {
    id: 'mastermind',
    name: 'Mastermind',
    icon: 'lightbulb',
    lane: 'logic',
    blurb: 'Crack the hidden color code in ten guesses using the peg hints.',
    component: lazy(() => import('./games/Mastermind')),
  },
  {
    id: 'pong',
    name: 'Pong',
    icon: 'sports_tennis',
    lane: 'retro',
    blurb: 'The original. Keep the ball in play and beat the computer to five.',
    component: lazy(() => import('./games/Pong')),
  },
  {
    id: 'snake',
    name: 'Snake',
    icon: 'gesture',
    lane: 'retro',
    blurb: 'Eat, grow, and do not run into the wall or your own tail.',
    component: lazy(() => import('./games/Snake')),
  },
]
