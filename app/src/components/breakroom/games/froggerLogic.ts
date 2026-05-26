// Pure logic for Frogger. No React, no DOM, unit-testable headless.
// The road/river asymmetry is the bug magnet: a car cell kills, but a river
// cell is safe ONLY when a log occupies it (otherwise the frog drowns).

export type LaneType = 'safe' | 'road' | 'river'

export interface Lane {
  type: LaneType
  occupied: number[] // cars on a road lane, logs on a river lane
}

export interface FrogStatus {
  dead: boolean
  onLog: boolean
}

export function frogStatus(frogCol: number, lane: Lane): FrogStatus {
  const on = lane.occupied.includes(frogCol)
  if (lane.type === 'road') return { dead: on, onLog: false }
  if (lane.type === 'river') return { dead: !on, onLog: on }
  return { dead: false, onLog: false }
}

export function reachedHome(row: number, homeRow: number): boolean {
  return row === homeRow
}
