import { useEffect, useState } from "react";

interface ProvenanceData {
  agent_name?: string;
  task_id?: string;
  created_at?: string;
  prompt_summary?: string;
}

function timeAgo(isoStr: string): string {
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
  return `${Math.floor(diff / 86400)} days ago`;
}

export default function ProvenanceChip({ filePath }: { filePath: string }) {
  const [data, setData] = useState<ProvenanceData | null>(null);

  useEffect(() => {
    fetch(`/api/files/provenance?path=${encodeURIComponent(filePath)}`)
      .then((r) => r.json())
      .then((d: ProvenanceData) => {
        if (d && d.agent_name) setData(d);
      })
      .catch(() => {});
  }, [filePath]);

  if (!data) return null;

  const parts = [
    `Created by ${data.agent_name}`,
    data.task_id ? `for Task #${data.task_id}` : null,
    data.created_at ? timeAgo(data.created_at) : null,
  ].filter(Boolean);

  return (
    <span
      data-testid="provenance-chip"
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontSize: "0.75rem",
        color: "#6b7280",
        backgroundColor: "#f3f4f6",
        borderRadius: "9999px",
        padding: "2px 8px",
      }}
    >
      {parts.join(", ")}
    </span>
  );
}
