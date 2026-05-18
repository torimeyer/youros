import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import ConflictDialog from './ConflictDialog'

describe('ConflictDialog — scaffold', () => {
  it('renders without crashing', () => {
    const { container } = render(<ConflictDialog />)
    expect(container).toBeDefined()
  })
})
