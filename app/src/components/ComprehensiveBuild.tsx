/**
 * Build state pill for a task row.
 *
 * Shows "Building" when the agent is actively running and "Queued" when it
 * is waiting for an open slot. Replaces the generic "In progress" badge for
 * tasks that have a comprehensive build agent.
 */

import Icon from "./Icon";

export type BuildState = "running" | "queued";

interface Props {
  taskId: string;
  buildState: BuildState;
}

export function ComprehensiveBuildPill({ taskId, buildState }: Props) {
  if (buildState === "queued") {
    return (
      <span
        data-testid={`build-queued-badge-${taskId}`}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/15 text-amber-400 border border-amber-500/30"
        title="Waiting for a build slot"
      >
        <Icon name="schedule" className="text-[10px]" />
        Queued
      </span>
    );
  }

  return (
    <span
      data-testid={`build-running-badge-${taskId}`}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-500/15 text-green-400 border border-green-500/30"
      title="Build in progress"
    >
      <Icon name="code" className="text-[10px]" />
      Building
    </span>
  );
}
