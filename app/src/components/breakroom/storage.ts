// Tiny localStorage helper for Break Room games.
// Key shape: myos.breakroom.<gameId>.best.v1
// Dot-style keys are NOT swept on app reset (only myos-ephemeral- keys are),
// so best scores persist. Every access is wrapped so private-mode / quota /
// corrupt JSON never throws into a game.

export interface BestRecord {
  v: 1
  updatedAt: number
  best?: number // higher-is-better (Set count, Snake length, Pong wins)
  bestMs?: number // lower-is-better (Solitaire / Minesweeper completion time)
  bestRolls?: number // lower-is-better (Cats & Snakes fewest rolls to finish)
}

function keyFor(gameId: string): string {
  return `myos.breakroom.${gameId}.best.v1`
}

export function loadBest(gameId: string): BestRecord | null {
  try {
    const raw = localStorage.getItem(keyFor(gameId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as BestRecord
    return parsed && parsed.v === 1 ? parsed : null
  } catch {
    return null
  }
}

export function saveBest(gameId: string, rec: Omit<BestRecord, 'v' | 'updatedAt'>): void {
  try {
    const full: BestRecord = { v: 1, updatedAt: Date.now(), ...rec }
    localStorage.setItem(keyFor(gameId), JSON.stringify(full))
  } catch {
    // ignore (private mode / quota)
  }
}

/** Record a higher-is-better score. Returns true if it beat the previous best. */
export function recordHighScore(gameId: string, score: number): boolean {
  const prev = loadBest(gameId)
  if (prev?.best != null && prev.best >= score) return false
  saveBest(gameId, { best: score })
  return true
}

/** Record a lower-is-better time in ms. Returns true if it beat the previous best. */
export function recordBestTime(gameId: string, ms: number): boolean {
  const prev = loadBest(gameId)
  if (prev?.bestMs != null && prev.bestMs <= ms) return false
  saveBest(gameId, { bestMs: ms })
  return true
}

// --- How-to-play splash visibility (per game) ---

function howToKey(gameId: string): string {
  return `myos.breakroom.${gameId}.howto.v1`
}

/** True once the user has chosen "don't show again" for this game's how-to-play. */
export function hasSeenHowTo(gameId: string): boolean {
  try {
    return localStorage.getItem(howToKey(gameId)) === 'hidden'
  } catch {
    return false
  }
}

/** Remember not to auto-show this game's how-to-play splash again. */
export function setNeverShowHowTo(gameId: string): void {
  try {
    localStorage.setItem(howToKey(gameId), 'hidden')
  } catch {
    // ignore (private mode / quota)
  }
}

/**
 * True while any modal/dialog overlay is open. Game loops call this to pause
 * themselves while the how-to-play splash or a win/lose dialog is showing.
 */
export function isOverlayOpen(): boolean {
  if (typeof document === 'undefined') return false
  return !!document.querySelector('[role="dialog"]')
}
