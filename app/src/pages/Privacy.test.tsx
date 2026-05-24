import { describe, it, expect, beforeAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Privacy from './Privacy';

beforeAll(() => {
  // TopBar calls window.matchMedia which jsdom does not implement.
  if (!window.matchMedia) {
    // @ts-expect-error assign a matchMedia stub for jsdom
    window.matchMedia = () => ({
      matches: false,
      media: '',
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    });
  }
});

describe('Privacy page', () => {
  it('renders the plain-language privacy summary', () => {
    render(
      <MemoryRouter>
        <Privacy />
      </MemoryRouter>
    );
    expect(screen.getByText(/torios runs on your computer/i)).toBeInTheDocument();
    expect(screen.getByText(/What torios stores, and where/i)).toBeInTheDocument();
    expect(screen.getByText(/What leaves your computer/i)).toBeInTheDocument();
    expect(screen.getByText(/There is no telemetry/i)).toBeInTheDocument();
  });
});
