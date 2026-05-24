import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import ProvenanceChip from './ProvenanceChip';

const AGENT_PROVENANCE = {
  agent_name: 'Builder',
  task_id: '42',
  created_at: new Date(Date.now() - 7200000).toISOString(),
  prompt_summary: 'Build the thing',
  source: 'agent' as const,
};

describe('ProvenanceChip', () => {
  it('renders agent/task line for agent provenance', () => {
    render(<ProvenanceChip provenance={AGENT_PROVENANCE} />);
    const chip = screen.getByTestId('provenance-chip');
    expect(chip.textContent).toContain('Created by Builder');
    expect(chip.textContent).toContain('task #42');
  });

  it("renders 'Uploaded' for upload source", () => {
    render(
      <ProvenanceChip
        provenance={{ created_at: new Date().toISOString(), source: 'upload' }}
      />
    );
    expect(screen.getByTestId('provenance-chip').textContent).toContain('Uploaded');
  });

  it("renders 'Imported from Drive' for drive_import source", () => {
    render(
      <ProvenanceChip
        provenance={{ created_at: new Date().toISOString(), source: 'drive_import' }}
      />
    );
    expect(screen.getByTestId('provenance-chip').textContent).toContain('Imported from Drive');
  });

  it("falls back to 'Unknown source' when provenance is null", () => {
    render(<ProvenanceChip provenance={null} />);
    expect(screen.getByTestId('provenance-chip').textContent).toContain('Unknown source');
  });

  it('infers agent source from agent_name when source field is absent', () => {
    render(
      <ProvenanceChip
        provenance={{ agent_name: 'Writer', created_at: new Date().toISOString() }}
      />
    );
    expect(screen.getByTestId('provenance-chip').textContent).toContain('Created by Writer');
  });

  it('shows time-ago in plain language', () => {
    render(<ProvenanceChip provenance={AGENT_PROVENANCE} />);
    const text = screen.getByTestId('provenance-chip').textContent ?? '';
    expect(text).toMatch(/ago|just now/i);
  });
});
