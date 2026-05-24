import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { SkeletonLine, SkeletonBlock, SkeletonAvatar } from '../Skeleton'

describe('SkeletonLine', () => {
  it('renders with animate-pulse and default width', () => {
    render(<SkeletonLine />)
    const el = screen.getByTestId('skeleton-line')
    expect(el).toBeInTheDocument()
    expect(el.className).toContain('animate-pulse')
    expect(el.className).toContain('w-full')
    expect(el.className).toContain('h-4')
  })

  it('applies custom width', () => {
    render(<SkeletonLine width="w-1/2" />)
    expect(screen.getByTestId('skeleton-line').className).toContain('w-1/2')
  })
})

describe('SkeletonBlock', () => {
  it('renders with animate-pulse and defaults', () => {
    render(<SkeletonBlock />)
    const el = screen.getByTestId('skeleton-block')
    expect(el).toBeInTheDocument()
    expect(el.className).toContain('animate-pulse')
    expect(el.className).toContain('w-full')
    expect(el.className).toContain('h-24')
  })

  it('applies custom width and height', () => {
    render(<SkeletonBlock width="w-32" height="h-48" />)
    const el = screen.getByTestId('skeleton-block')
    expect(el.className).toContain('w-32')
    expect(el.className).toContain('h-48')
  })
})

describe('SkeletonAvatar', () => {
  it('renders with default md size', () => {
    render(<SkeletonAvatar />)
    const el = screen.getByTestId('skeleton-avatar')
    expect(el).toBeInTheDocument()
    expect(el.className).toContain('w-10 h-10')
    expect(el.className).toContain('rounded-full')
    expect(el.className).toContain('animate-pulse')
  })

  it('applies sm size classes', () => {
    render(<SkeletonAvatar size="sm" />)
    expect(screen.getByTestId('skeleton-avatar').className).toContain('w-6 h-6')
  })

  it('applies lg size classes', () => {
    render(<SkeletonAvatar size="lg" />)
    expect(screen.getByTestId('skeleton-avatar').className).toContain('w-14 h-14')
  })
})
