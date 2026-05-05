import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Footer } from './Footer'

function renderFooter() {
  return render(
    <MemoryRouter>
      <Footer />
    </MemoryRouter>
  )
}

describe('Footer', () => {
  it('renders footer element with data-testid', () => {
    renderFooter()
    expect(screen.getByTestId('footer')).toBeInTheDocument()
  })

  it('renders About link with correct href', () => {
    renderFooter()
    const link = screen.getByTestId('footer-link-about')
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/about')
    expect(link).toHaveTextContent('About')
  })

  it('renders Privacy link with correct href', () => {
    renderFooter()
    const link = screen.getByTestId('footer-link-privacy')
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', '/privacy')
    expect(link).toHaveTextContent('Privacy')
  })

  it('both data-testids are present', () => {
    renderFooter()
    expect(screen.getByTestId('footer-link-about')).toBeInTheDocument()
    expect(screen.getByTestId('footer-link-privacy')).toBeInTheDocument()
  })
})
