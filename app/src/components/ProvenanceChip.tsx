import { formatRelative } from '../lib/time';

export interface Provenance {
  agent_id?: string;
  agent_name?: string;
  task_id?: string;
  created_at?: string;
  prompt_summary?: string;
  source?: 'agent' | 'upload' | 'drive_import';
}

export default function ProvenanceChip({
  provenance,
}: {
  provenance: Provenance | null | undefined;
}) {
  const agentName = provenance?.agent_name ?? provenance?.agent_id;
  const relTime = provenance?.created_at ? formatRelative(provenance.created_at) : '';
  const source = provenance?.source ?? (agentName ? 'agent' : 'upload');

  let text: string;
  if (!provenance) {
    text = 'Unknown source';
  } else if (source === 'drive_import') {
    text = relTime ? `Imported from Drive ${relTime}` : 'Imported from Drive';
  } else if (source === 'upload') {
    text = relTime ? `Uploaded ${relTime}` : 'Uploaded';
  } else {
    const parts: string[] = [`Created by ${agentName ?? 'agent'}`];
    if (provenance.task_id) parts.push(`for task #${provenance.task_id}`);
    if (relTime) parts.push(relTime);
    text = parts.join(' • ');
  }

  return (
    <span
      data-testid="provenance-chip"
      className="inline-flex items-center text-xs text-slate-400 bg-slate-800/80 rounded-full px-2 py-0.5 border border-slate-700/50"
    >
      {text}
    </span>
  );
}
