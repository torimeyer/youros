import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import BreakRoom from './BreakRoom'

// Mock the api module so TopBar polls never hit the network.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

// Replace the real game registry with two tiny fake games so the page's
// orchestration (tile grid, how-to gate, restart remount) is tested
// without booting real game loops in jsdom. The mount counter proves the
// restart control actually remounts the active game.
vi.mock('../components/breakroom/registry', async () => {
  const React = await import('react')
  let mounts = 0
  function FakeGame() {
    const [mountNumber] = React.useState(() => {
      mounts += 1
      return mounts
    })
    return React.createElement('div', { 'data-testid': 'fake-game' }, `mount-${mountNumber}`)
  }
  return {
    GAMES: [
      {
        id: 'alpha',
        name: 'Alpha',
        icon: 'style',
        lane: 'logic',
        blurb: 'A tiny fake logic game.',
        howTo: ['Step one.', 'Step two.'],
        component: FakeGame,
      },
      {
        id: 'beta',
        name: 'Beta',
        icon: 'grid_on',
        lane: 'retro',
        blurb: 'A tiny fake arcade game.',
        howTo: ['Only step.'],
        component: FakeGame,
      },
    ],
  }
})

// jsdom does not provide window.matchMedia. Provide a minimal stub so
// components that use responsive breakpoints do not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

function renderBreakRoom() {
  return render(
    <MemoryRouter>
      <BreakRoom />
    </MemoryRouter>
  )
}

describe('BreakRoom page', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('renders a tile for every game in the registry', () => {
    renderBreakRoom()
    expect(screen.getByTestId('breakroom-grid')).toBeInTheDocument()
    expect(screen.getByTestId('game-tile-alpha')).toBeInTheDocument()
    expect(screen.getByTestId('game-tile-beta')).toBeInTheDocument()
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('A tiny fake logic game.')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
  })

  it('clicking a tile mounts the game with the first-time how-to splash', () => {
    renderBreakRoom()
    fireEvent.click(screen.getByTestId('game-tile-alpha'))
    // The game is mounted underneath (the splash overlays, never blocks mounting)
    expect(screen.getByTestId('fake-game')).toBeInTheDocument()
    expect(screen.queryByTestId('breakroom-grid')).not.toBeInTheDocument()
    // First play: the how-to splash shows the game's steps
    expect(screen.getByTestId('howto-backdrop')).toBeInTheDocument()
    expect(screen.getByText('How to play Alpha')).toBeInTheDocument()
    expect(screen.getByText('Step one.')).toBeInTheDocument()
    // Play dismisses the splash and the game stays mounted
    fireEvent.click(screen.getByTestId('howto-play'))
    expect(screen.queryByTestId('howto-backdrop')).not.toBeInTheDocument()
    expect(screen.getByTestId('fake-game')).toBeInTheDocument()
  })

  it('checking "do not show again" skips the splash on the next play', () => {
    renderBreakRoom()
    fireEvent.click(screen.getByTestId('game-tile-alpha'))
    fireEvent.click(screen.getByTestId('howto-never'))
    fireEvent.click(screen.getByTestId('howto-play'))
    // Leave and re-enter the same game
    fireEvent.click(screen.getByTestId('breakroom-back'))
    expect(screen.getByTestId('breakroom-grid')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('game-tile-alpha'))
    expect(screen.queryByTestId('howto-backdrop')).not.toBeInTheDocument()
    expect(screen.getByTestId('fake-game')).toBeInTheDocument()
  })

  it('without opting out, the splash returns on the next play', () => {
    renderBreakRoom()
    fireEvent.click(screen.getByTestId('game-tile-alpha'))
    fireEvent.click(screen.getByTestId('howto-play'))
    fireEvent.click(screen.getByTestId('breakroom-back'))
    fireEvent.click(screen.getByTestId('game-tile-alpha'))
    expect(screen.getByTestId('howto-backdrop')).toBeInTheDocument()
  })

  it('the opt-out is per game, not global', () => {
    renderBreakRoom()
    fireEvent.click(screen.getByTestId('game-tile-alpha'))
    fireEvent.click(screen.getByTestId('howto-never'))
    fireEvent.click(screen.getByTestId('howto-play'))
    fireEvent.click(screen.getByTestId('breakroom-back'))
    // A different game still gets its first-time splash
    fireEvent.click(screen.getByTestId('game-tile-beta'))
    expect(screen.getByTestId('howto-backdrop')).toBeInTheDocument()
    expect(screen.getByText('How to play Beta')).toBeInTheDocument()
  })

  it('the restart control remounts the active game without re-opening the splash', () => {
    renderBreakRoom()
    fireEvent.click(screen.getByTestId('game-tile-beta'))
    fireEvent.click(screen.getByTestId('howto-play'))
    const before = screen.getByTestId('fake-game').textContent
    fireEvent.click(screen.getByTestId('breakroom-restart'))
    const after = screen.getByTestId('fake-game').textContent
    // The key flip remounted the component (fresh mount counter value)
    expect(after).not.toBe(before)
    expect(screen.queryByTestId('howto-backdrop')).not.toBeInTheDocument()
  })

  it('the help control re-opens the how-to splash on demand', () => {
    renderBreakRoom()
    fireEvent.click(screen.getByTestId('game-tile-beta'))
    fireEvent.click(screen.getByTestId('howto-play'))
    expect(screen.queryByTestId('howto-backdrop')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('breakroom-help'))
    expect(screen.getByTestId('howto-backdrop')).toBeInTheDocument()
    expect(screen.getByText('How to play Beta')).toBeInTheDocument()
  })

  it('the back control returns to the game grid', () => {
    renderBreakRoom()
    fireEvent.click(screen.getByTestId('game-tile-alpha'))
    fireEvent.click(screen.getByTestId('breakroom-back'))
    expect(screen.getByTestId('breakroom-grid')).toBeInTheDocument()
    expect(screen.queryByTestId('fake-game')).not.toBeInTheDocument()
  })
})
