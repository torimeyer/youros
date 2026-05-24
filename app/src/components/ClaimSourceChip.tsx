import { formatRelative } from '../lib/time';

interface ClaimSourceChipProps {
  agent: string;
  source: string;
  started_at: string;
}

export function ClaimSourceChip({ agent, source, started_at }: ClaimSourceChipProps) {
  const shortName = agent.length > 16 ? agent.slice(0, 16) + '…' : agent;
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-purple-500/10 text-purple-300 whitespace-nowrap"
      style={{ fontSize: '9px' }}
      data-testid="claim-source-chip"
    >
      🟣 {shortName} in {source} · {formatRelative(started_at)}
    </span>
  );
}
