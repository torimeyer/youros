import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import LoadingState from '../LoadingState'

describe('LoadingState', () => {
  it('renders spinner variant by default', () => {
    render(<LoadingState />)
    const el = screen.getByTestId('loading-state')
    expect(el).toBeInTheDocument()
    expect(el.querySelector('.animate-spin')).toBeTruthy()
  })

  it('shows message with spinner variant', () => {
    render(<LoadingState message="Loading your data..." />)
    expect(screen.getByText('Loading your data...')).toBeInTheDocument()
  })

  it('renders skeleton-list variant with 5 rows', () => {
    render(<LoadingState variant="skeleton-list" />)
    const skeletonBlocks = screen.getAllByTestId('skeleton-block')
    expect(skeletonBlocks.length).toBe(5)
  })

  it('renders skeleton-card variant', () => {
    render(<LoadingState variant="skeleton-card" />)
    expect(screen.getByTestId('loading-state')).toBeInTheDocument()
    expect(screen.getAllByTestId('skeleton-line').length).toBeGreaterThanOrEqual(3)
  })

  it('uses progress_activity icon not hourglass', () => {
    render(<LoadingState />)
    const icon = screen.getByTestId('loading-state').querySelector('.material-symbols-outlined')
    expect(icon?.textContent?.trim()).toBe('progress_activity')
  })
})
