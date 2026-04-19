import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SpecTemplateDetailsModal from './SpecTemplateDetailsModal'

const sampleTemplate = {
  id: 'build-a-website',
  name: 'Build a Website',
  description: 'Plan, design, build, and review a new website end to end.',
  icon: 'web',
  acceptance_criteria: ['A short PRD exists.', 'The landing page renders.'],
  tasks: ['Write the PRD', 'Build the frontend landing page'],
}

describe('SpecTemplateDetailsModal', () => {
  it('opens the modal and submits with the user-entered plan name and note', async () => {
    const onSubmit = vi.fn()
    const onClose = vi.fn()

    render(
      <SpecTemplateDetailsModal
        open={true}
        template={sampleTemplate}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    )

    // Modal is visible and shows the template name and description.
    expect(screen.getByTestId('spec-template-details-modal')).toBeInTheDocument()
    expect(screen.getByTestId('spec-template-details-description')).toHaveTextContent(
      sampleTemplate.description,
    )

    // The plan name input is prefilled with the template's name so the
    // user can ship as-is or tweak it.
    const titleInput = screen.getByTestId('spec-template-title-input') as HTMLInputElement
    expect(titleInput.value).toBe('Build a Website')

    // User edits both fields.
    fireEvent.change(titleInput, { target: { value: 'Launch Marketing Site' } })
    const noteInput = screen.getByTestId('spec-template-note-input') as HTMLTextAreaElement
    fireEvent.change(noteInput, {
      target: { value: 'Ship by end of quarter for the PM community.' },
    })

    // Click Create plan. onSubmit fires with the exact entered fields.
    fireEvent.click(screen.getByTestId('spec-template-apply'))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1)
    })
    expect(onSubmit).toHaveBeenCalledWith({
      title: 'Launch Marketing Site',
      note: 'Ship by end of quarter for the PM community.',
    })
  })

  it('does not render when closed', () => {
    const onSubmit = vi.fn()
    const onClose = vi.fn()

    render(
      <SpecTemplateDetailsModal
        open={false}
        template={sampleTemplate}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    )

    expect(screen.queryByTestId('spec-template-details-modal')).toBeNull()
  })

  it('disables Create plan when the name is empty', () => {
    const onSubmit = vi.fn()
    const onClose = vi.fn()

    render(
      <SpecTemplateDetailsModal
        open={true}
        template={sampleTemplate}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    )

    const titleInput = screen.getByTestId('spec-template-title-input') as HTMLInputElement
    fireEvent.change(titleInput, { target: { value: '   ' } })

    const applyButton = screen.getByTestId('spec-template-apply') as HTMLButtonElement
    expect(applyButton.disabled).toBe(true)

    fireEvent.click(applyButton)
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
