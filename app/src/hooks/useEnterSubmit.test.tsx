import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useEnterSubmit } from './useEnterSubmit'

function InputFixture({ onSubmit }: { onSubmit: () => void }) {
  const handlers = useEnterSubmit(onSubmit)
  return (
    <input
      data-testid="input"
      {...handlers}
      onChange={() => {}}
    />
  )
}

function TextareaFixture({ onSubmit }: { onSubmit: () => void }) {
  const handlers = useEnterSubmit(onSubmit)
  return (
    <textarea
      data-testid="textarea"
      {...handlers}
      onChange={() => {}}
    />
  )
}

describe('useEnterSubmit', () => {
  it('calls handler when Enter is pressed on an input', () => {
    const onSubmit = vi.fn()
    render(<InputFixture onSubmit={onSubmit} />)
    fireEvent.keyDown(screen.getByTestId('input'), { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('does NOT call handler when Shift+Enter is pressed', () => {
    const onSubmit = vi.fn()
    render(<InputFixture onSubmit={onSubmit} />)
    fireEvent.keyDown(screen.getByTestId('input'), { key: 'Enter', shiftKey: true })
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('calls handler when Enter is pressed on a textarea', () => {
    const onSubmit = vi.fn()
    render(<TextareaFixture onSubmit={onSubmit} />)
    fireEvent.keyDown(screen.getByTestId('textarea'), { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('does NOT call handler when Shift+Enter is pressed on textarea', () => {
    const onSubmit = vi.fn()
    render(<TextareaFixture onSubmit={onSubmit} />)
    fireEvent.keyDown(screen.getByTestId('textarea'), { key: 'Enter', shiftKey: true })
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('does NOT call handler for non-Enter keys', () => {
    const onSubmit = vi.fn()
    render(<InputFixture onSubmit={onSubmit} />)
    fireEvent.keyDown(screen.getByTestId('input'), { key: 'a' })
    fireEvent.keyDown(screen.getByTestId('input'), { key: 'Tab' })
    fireEvent.keyDown(screen.getByTestId('input'), { key: 'Escape' })
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
