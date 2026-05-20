import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StructuredPicker } from './StructuredPicker'

const OPTIONS = [
  { label: 'Option A', description: 'The first choice' },
  { label: 'Option B', description: 'The second choice' },
]

describe('StructuredPicker', () => {
  it('renders a chip for each option', () => {
    render(
      <StructuredPicker
        question="Which approach?"
        options={OPTIONS}
        onSelect={vi.fn()}
      />
    )
    expect(screen.getByTestId('structured-picker')).toBeInTheDocument()
    expect(screen.getByTestId('picker-option-Option A')).toBeInTheDocument()
    expect(screen.getByTestId('picker-option-Option B')).toBeInTheDocument()
  })

  it('renders the question text', () => {
    render(
      <StructuredPicker
        question="What do you want to do?"
        options={OPTIONS}
        onSelect={vi.fn()}
      />
    )
    expect(screen.getByText('What do you want to do?')).toBeInTheDocument()
  })

  it('renders option labels', () => {
    render(
      <StructuredPicker
        question="Pick one"
        options={OPTIONS}
        onSelect={vi.fn()}
      />
    )
    expect(screen.getByText('Option A')).toBeInTheDocument()
    expect(screen.getByText('Option B')).toBeInTheDocument()
  })

  it('calls onSelect with the label when a chip is clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()

    render(
      <StructuredPicker
        question="Which approach?"
        options={OPTIONS}
        onSelect={onSelect}
      />
    )

    await user.click(screen.getByTestId('picker-option-Option A'))
    expect(onSelect).toHaveBeenCalledWith('Option A')
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('calls onSelect with the correct label for the second option', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()

    render(
      <StructuredPicker
        question="Which approach?"
        options={OPTIONS}
        onSelect={onSelect}
      />
    )

    await user.click(screen.getByTestId('picker-option-Option B'))
    expect(onSelect).toHaveBeenCalledWith('Option B')
  })
})
