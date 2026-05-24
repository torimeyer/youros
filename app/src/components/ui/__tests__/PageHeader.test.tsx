import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import PageHeader from '../PageHeader'

describe('PageHeader', () => {
  it('renders with required title', () => {
    render(<PageHeader title="My Page" />)
    const header = screen.getByTestId('page-header')
    expect(header).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('My Page')
  })

  it('renders subtitle when provided', () => {
    render(<PageHeader title="Tasks" subtitle="Your open work items." />)
    expect(screen.getByText('Your open work items.')).toBeInTheDocument()
  })

  it('does not render subtitle when omitted', () => {
    render(<PageHeader title="Tasks" />)
    expect(screen.queryByText(/subtitle/i)).toBeNull()
  })

  it('renders primaryAction when provided', () => {
    render(<PageHeader title="Tasks" primaryAction={<button>New Task</button>} />)
    expect(screen.getByText('New Task')).toBeInTheDocument()
  })

  it('renders overflowMenu when provided', () => {
    render(<PageHeader title="Tasks" overflowMenu={<div>menu</div>} />)
    expect(screen.getByText('menu')).toBeInTheDocument()
  })

  it('renders breadcrumb trail', () => {
    render(
      <PageHeader
        title="Settings"
        breadcrumb={[
          { label: 'Home', href: '/' },
          { label: 'Settings' },
        ]}
      />
    )
    expect(screen.getByText('Home')).toBeInTheDocument()
    expect(screen.getByText('Settings', { selector: 'span' })).toBeInTheDocument()
    const homeLink = screen.getByText('Home').closest('a')
    expect(homeLink?.getAttribute('href')).toBe('/')
  })

  it('does not render breadcrumb nav when breadcrumb is empty', () => {
    render(<PageHeader title="Tasks" breadcrumb={[]} />)
    expect(screen.queryByRole('navigation')).toBeNull()
  })
})
