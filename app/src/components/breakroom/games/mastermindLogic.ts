// Pure logic for Mastermind. No React, no DOM, unit-testable headless.

export interface PegScore {
  exact: number
  partial: number
}

// Exact = right color in right spot. Partial = right color, wrong spot,
// counted without double-crediting duplicate colors (the classic bug).
export function scorePegs(secret: string[], guess: string[]): PegScore {
  let exact = 0
  const sRest: Record<string, number> = {}
  const gRest: Record<string, number> = {}
  for (let i = 0; i < secret.length; i++) {
    if (secret[i] === guess[i]) {
      exact++
    } else {
      sRest[secret[i]] = (sRest[secret[i]] || 0) + 1
      gRest[guess[i]] = (gRest[guess[i]] || 0) + 1
    }
  }
  let partial = 0
  for (const color in gRest) {
    if (sRest[color]) partial += Math.min(sRest[color], gRest[color])
  }
  return { exact, partial }
}

export function isWin(score: PegScore, codeLength: number): boolean {
  return score.exact === codeLength
}

export function randomCode(colors: string[], length: number, rng: () => number = Math.random): string[] {
  const out: string[] = []
  for (let i = 0; i < length; i++) out.push(colors[Math.floor(rng() * colors.length)])
  return out
}
