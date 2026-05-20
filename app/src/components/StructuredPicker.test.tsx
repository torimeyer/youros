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

  it('always renders an Other chip', () => {
    render(
      <StructuredPicker
        question="Which approach?"
        options={OPTIONS}
        onSelect={vi.fn()}
      />
    )
    expect(screen.getByTestId('picker-option-Other')).toBeInTheDocument()
  })

  it('clicking Other chip opens a text input', async () => {
    const user = userEvent.setup()
    render(
      <StructuredPicker
        question="Which approach?"
        options={OPTIONS}
        onSelect={vi.fn()}
      />
    )
    await user.click(screen.getByTestId('picker-option-Other'))
    expect(screen.getByTestId('picker-other-input')).toBeInTheDocument()
  })

  it('typing in Other input and submitting calls onSelect with the typed text', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <StructuredPicker
        question="Which approach?"
        options={OPTIONS}
        onSelect={onSelect}
      />
    )
    await user.click(screen.getByTestId('picker-option-Other'))
    await user.type(screen.getByTestId('picker-other-input'), 'custom answer')
    await user.click(screen.getByTestId('picker-other-send'))
    expect(onSelect).toHaveBeenCalledWith('custom answer')
  })

  it('chips are disabled after a selection is made', async () => {
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
    expect(screen.getByTestId('picker-option-Option A')).toBeDisabled()
    expect(screen.getByTestId('picker-option-Option B')).toBeDisabled()
    expect(screen.getByTestId('picker-option-Other')).toBeDisabled()
  })

  it('selected chip is highlighted after click', async () => {
    const user = userEvent.setup()
    render(
      <StructuredPicker
        question="Which approach?"
        options={OPTIONS}
        onSelect={vi.fn()}
      />
    )
    await user.click(screen.getByTestId('picker-option-Option A'))
    expect(screen.getByTestId('picker-option-Option A')).toHaveAttribute('data-selected', 'true')
  })

  it('multiSelect: multiple chips can be toggled and Done submits all', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <StructuredPicker
        question="Pick features"
        options={[
          { label: 'Fast', description: 'Speed' },
          { label: 'Cheap', description: 'Low cost' },
          { label: 'Good', description: 'Quality' },
        ]}
        multiSelect
        onSelect={onSelect}
      />
    )
    await user.click(screen.getByTestId('picker-option-Fast'))
    await user.click(screen.getByTestId('picker-option-Good'))
    await user.click(screen.getByTestId('picker-done'))
    expect(onSelect).toHaveBeenCalledWith('Fast, Good')
  })

  it('multiSelect: Done button is disabled until at least one chip is selected', () => {
    render(
      <StructuredPicker
        question="Pick features"
        options={OPTIONS}
        multiSelect
        onSelect={vi.fn()}
      />
    )
    expect(screen.getByTestId('picker-done')).toBeDisabled()
  })
})
