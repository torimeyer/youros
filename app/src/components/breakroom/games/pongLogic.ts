// Pure logic for Pong. No React, no DOM, unit-testable headless.

// Does the ball's vertical position fall within the paddle's span?
export function paddleHit(ballY: number, paddleTop: number, paddleHeight: number): boolean {
  return ballY >= paddleTop && ballY <= paddleTop + paddleHeight
}

// Reverse horizontal velocity on a paddle bounce.
export function reflectX(vx: number): number {
  return -vx
}

// Vertical spin from where the ball struck the paddle: center = none,
// edges = full speed up or down. Returns the new vertical velocity.
export function spinFromPaddle(
  ballY: number,
  paddleTop: number,
  paddleHeight: number,
  speed: number,
): number {
  const center = paddleTop + paddleHeight / 2
  const offset = (ballY - center) / (paddleHeight / 2)
  return offset * speed
}
