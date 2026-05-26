// Pure logic for Snake. No React, no DOM, unit-testable headless.

export interface Pt {
  x: number
  y: number
}

export interface StepResult {
  snake: Pt[]
  ate: boolean
  dead: boolean
}

export function nextHead(head: Pt, dir: Pt): Pt {
  return { x: head.x + dir.x, y: head.y + dir.y }
}

function outOfBounds(p: Pt, cols: number, rows: number): boolean {
  return p.x < 0 || p.y < 0 || p.x >= cols || p.y >= rows
}

function hitsBody(p: Pt, body: Pt[]): boolean {
  return body.some((b) => b.x === p.x && b.y === p.y)
}

export function detectCollision(head: Pt, body: Pt[], cols: number, rows: number): boolean {
  return outOfBounds(head, cols, rows) || hitsBody(head, body)
}

// Advance the snake one tick. When the head reaches food the tail is kept
// (grow); otherwise the tail tip moves. The tail tip is excluded from the
// self-collision check when not eating, since it vacates this turn.
export function step(snake: Pt[], dir: Pt, food: Pt, cols: number, rows: number): StepResult {
  const head = nextHead(snake[0], dir)
  const willEat = head.x === food.x && head.y === food.y
  const bodyToCheck = willEat ? snake : snake.slice(0, -1)
  if (outOfBounds(head, cols, rows) || hitsBody(head, bodyToCheck)) {
    return { snake, ate: false, dead: true }
  }
  const newSnake = [head, ...snake]
  if (!willEat) newSnake.pop()
  return { snake: newSnake, ate: willEat, dead: false }
}
