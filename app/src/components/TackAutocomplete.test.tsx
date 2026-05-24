import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { useRef } from 'react'
import TackAutocomplete from './TackAutocomplete'

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

import { api } from '../lib/api'
const mockApiGet = vi.mocked(api.get)

function Wrapper({ inputValue, onSelect }: { inputValue: string; onSelect: (s: string) => void }) {
  const keyHandlerRef = useRef<((e: React.KeyboardEvent) => boolean) | null>(null)
  return <TackAutocomplete inputValue={inputValue} onSelect={onSelect} keyHandlerRef={keyHandlerRef} />
}

function WrapperWithRef({
  inputValue,
  onSelect,
  keyHandlerRef,
}: {
  inputValue: string
  onSelect: (s: string) => void
  keyHandlerRef: React.MutableRefObject<((e: React.KeyboardEvent) => boolean) | null>
}) {
  return <TackAutocomplete inputValue={inputValue} onSelect={onSelect} keyHandlerRef={keyHandlerRef} />
}

beforeEach(() => {
  vi.clearAllMocks()
  mockApiGet.mockRejectedValue(new Error('Not found'))
})

describe('TackAutocomplete', () => {
  it('renders nothing when input does not start with a trigger prefix', () => {
    render(<Wrapper inputValue="hello world" onSelect={vi.fn()} />)
    expect(screen.queryByTestId('tack-autocomplete')).toBeNull()
  })

  it('renders static fallback suggestions when input is :co and api returns 404', async () => {
    mockApiGet.mockRejectedValue(Object.assign(new Error('Not found'), { status: 404 }))
    render(<Wrapper inputValue=":co" onSelect={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByTestId('tack-autocomplete')).toBeInTheDocument()
    })
    // :compile starts with :co
    expect(screen.getByText(':compile')).toBeInTheDocument()
  })

  it('calls onSelect with the suggestion value when ArrowDown + Tab is pressed', async () => {
    mockApiGet.mockRejectedValue(new Error('Not found'))
    const onSelect = vi.fn()
    const keyHandlerRef = { current: null as ((e: React.KeyboardEvent) => boolean) | null }

    render(<WrapperWithRef inputValue=":" onSelect={onSelect} keyHandlerRef={keyHandlerRef} />)

    await waitFor(() => {
      expect(screen.getByTestId('tack-autocomplete')).toBeInTheDocument()
    })

    // Move down once — index 0 → 1
    act(() => {
      keyHandlerRef.current?.({ key: 'ArrowDown', preventDefault: vi.fn() } as unknown as React.KeyboardEvent)
    })

    // Tab should select index 1 (:status)
    act(() => {
      keyHandlerRef.current?.({ key: 'Tab', preventDefault: vi.fn() } as unknown as React.KeyboardEvent)
    })

    expect(onSelect).toHaveBeenCalledWith(':status')
  })

  it('clears the dropdown when Escape is pressed', async () => {
    mockApiGet.mockRejectedValue(new Error('Not found'))
    const keyHandlerRef = { current: null as ((e: React.KeyboardEvent) => boolean) | null }

    render(<WrapperWithRef inputValue=":" onSelect={vi.fn()} keyHandlerRef={keyHandlerRef} />)

    await waitFor(() => {
      expect(screen.getByTestId('tack-autocomplete')).toBeInTheDocument()
    })

    act(() => {
      keyHandlerRef.current?.({ key: 'Escape', preventDefault: vi.fn() } as unknown as React.KeyboardEvent)
    })

    await waitFor(() => {
      expect(screen.queryByTestId('tack-autocomplete')).toBeNull()
    })
  })

  it('falls back to static suggestions when api.get rejects', async () => {
    mockApiGet.mockRejectedValue(new Error('Network error'))
    render(<Wrapper inputValue="/" onSelect={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByTestId('tack-autocomplete')).toBeInTheDocument()
    })
    expect(screen.getByText('/help')).toBeInTheDocument()
  })
})
