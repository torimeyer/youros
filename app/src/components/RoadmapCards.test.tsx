import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RoadmapCards } from './RoadmapCards';
import type { RoadmapQuarter } from '../lib/parseRoadmapJson';

const QUARTERS: RoadmapQuarter[] = [
  { quarter: 'Q1 2026', theme: 'Foundation', initiatives: ['Auth', 'Onboarding'] },
  { quarter: 'Q2 2026', theme: 'Growth', initiatives: ['Analytics'] },
];

describe('RoadmapCards', () => {
  it('renders quarter labels and initiative buttons', () => {
    render(<RoadmapCards quarters={QUARTERS} onNavigateToChat={() => {}} />);
    expect(screen.getByText('Q1 2026')).toBeTruthy();
    expect(screen.getByText('Q2 2026')).toBeTruthy();
    expect(screen.getByText('Foundation')).toBeTruthy();
    const buildBtns = screen.getAllByTestId('roadmap-build-btn');
    expect(buildBtns).toHaveLength(3);
  });

  it('sets sessionStorage and calls onNavigateToChat on Build it click', () => {
    const onNavigate = vi.fn();
    render(<RoadmapCards quarters={QUARTERS} onNavigateToChat={onNavigate} />);
    const btns = screen.getAllByTestId('roadmap-build-btn');
    fireEvent.click(btns[0]);
    expect(sessionStorage.getItem('chat_pending_input')).toBe('Build Auth');
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it('returns null for empty quarters array', () => {
    const { container } = render(<RoadmapCards quarters={[]} onNavigateToChat={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
});
