import { lazy, type ComponentType } from 'react'

export type GameLane = 'logic' | 'retro'

export interface GameDef {
  id: string
  name: string
  icon: string // Material Symbols name
  lane: GameLane
  blurb: string
  /** Up to 5 short, plain-language steps shown on the how-to-play splash. */
  howTo: string[]
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
    howTo: [
      'Every card has a color, a shape, a number of shapes, and a fill.',
      'Pick three cards by tapping them.',
      'For each of the four things, the three cards must be all the same or all different.',
      'Get it right and the cards clear and you score a point.',
      'Find as many sets as you can before the cards run out.',
    ],
    component: lazy(() => import('./games/Set')),
  },
  {
    id: 'solitaire',
    name: 'Solitaire',
    icon: 'playing_cards',
    lane: 'logic',
    blurb: 'Stack down by alternating color, build each suit up from the ace.',
    howTo: [
      'Stack cards down in order, switching color each time (red on black, black on red).',
      'Drag or click a card to move it onto another pile.',
      'Send Aces to the top, then build each suit up: 2, 3, 4, all the way to King.',
      'Tap the deck in the corner when you need more cards.',
      'Win by moving every card to the top piles.',
    ],
    component: lazy(() => import('./games/Solitaire')),
  },
  {
    id: 'minesweeper',
    name: 'Minesweeper',
    icon: 'grid_on',
    lane: 'logic',
    blurb: 'Clear the field without setting off a mine. Flag what you suspect.',
    howTo: [
      'Tap a square to clear it. Your first tap is always safe.',
      'A number shows how many mines are touching that square.',
      'Use the numbers to figure out where the mines are hiding.',
      'Tap the flag button, then a square, to mark a mine you found.',
      'Clear every safe square to win.',
    ],
    component: lazy(() => import('./games/Minesweeper')),
  },
  {
    id: 'mastermind',
    name: 'Mastermind',
    icon: 'lightbulb',
    lane: 'logic',
    blurb: 'Crack the hidden color code in ten guesses using the dot hints.',
    howTo: [
      'The computer picked a secret row of four colors.',
      'Tap colors to fill your row, then press Check.',
      'A black dot means one color is right and in the right spot.',
      'A white dot means one color is right but in the wrong spot.',
      'Use the dots to crack the code within ten tries.',
    ],
    component: lazy(() => import('./games/Mastermind')),
  },
  {
    id: 'guesswho',
    name: 'Guess Who',
    icon: 'quiz',
    lane: 'logic',
    blurb: 'Ask yes or no questions to figure out the hidden food first.',
    howTo: [
      'The computer picks a secret food. You pick one too.',
      'On your turn, type a yes or no question like "Is it sweet?"',
      'The computer answers yes or no. Tap foods to flip down the ones it cannot be.',
      'The computer also asks you yes or no questions about your food.',
      'The first to guess the other’s secret food wins!',
    ],
    component: lazy(() => import('./games/GuessWho')),
  },
  {
    id: 'pong',
    name: 'Pong',
    icon: 'sports_tennis',
    lane: 'retro',
    blurb: 'The original. Keep the ball in play and beat the computer to five.',
    howTo: [
      'You control the paddle on the left.',
      'Move it up and down with the arrow keys or your mouse.',
      'Bounce the ball past the computer to score a point.',
      'If you miss the ball, the computer scores.',
      'The first to five points wins.',
    ],
    component: lazy(() => import('./games/Pong')),
  },
  {
    id: 'snake',
    name: 'Snake',
    icon: 'gesture',
    lane: 'retro',
    blurb: 'Eat, grow, and do not run into the wall or your own tail.',
    howTo: [
      'Steer the snake with the arrow keys.',
      'Eat the food to grow longer and score points.',
      'Do not run into the walls.',
      'Do not bite your own tail.',
      'Try to grow the longest snake you can.',
    ],
    component: lazy(() => import('./games/Snake')),
  },
  {
    id: 'frogger',
    name: 'Frogger',
    icon: 'directions_car',
    lane: 'retro',
    blurb: 'Hop across the road and river to reach the far bank.',
    howTo: [
      'Move the frog with the arrow keys.',
      'Cross the road without touching any cars.',
      'Ride the logs to cross the water.',
      'Do not fall in the water or get hit by a car.',
      'Reach the top to win.',
    ],
    component: lazy(() => import('./games/Frogger')),
  },
]
