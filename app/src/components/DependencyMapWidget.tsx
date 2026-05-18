// DependencyMapWidget — nested-list view of Jira issue dependencies (→1452)
// No new viz lib. Tree: epic at top, linked issues indented by edge direction.
import { useState, useEffect } from 'react';
import Icon from './Icon';
import { Card, SkeletonLine } from './ui';
import { api } from '../lib/api';

interface DepNode {
  key: string;
  summary: string;
  status: string;
  owner: string;
}

interface DepEdge {
  from: string;
  to: string;
  type: string;
}

interface DepGraph {
  nodes: DepNode[];
  edges: DepEdge[];
}

interface Props {
  epicKey?: string;
}

export default function DependencyMapWidget({ epicKey }: Props) {
  const [graph, setGraph] = useState<DepGraph>({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!epicKey) return;
    const fetchGraph = async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await api.get<DepGraph>(`/coordination/dependencies/${epicKey}`);
        setGraph(res);
      } catch (err) {
        console.error('Failed to fetch dependencies:', err);
        setError('Could not load dependencies');
      } finally {
        setLoading(false);
      }
    };
    fetchGraph();
  }, [epicKey]);

  const nodeMap = new Map(graph.nodes.map((n) => [n.key, n]));
  const rootNode = epicKey ? nodeMap.get(epicKey) ?? null : null;

  // Nodes the root points to (outward)
  const outward = graph.edges.filter((e) => e.from === epicKey).map((e) => ({
    node: nodeMap.get(e.to),
    type: e.type,
  }));
  // Nodes that point TO the root (inward)
  const inward = graph.edges.filter((e) => e.to === epicKey).map((e) => ({
    node: nodeMap.get(e.from),
    type: e.type,
  }));

  const renderNode = (node: DepNode, indent = false) => (
    <div
      key={node.key}
      data-testid={`depmap-node-${node.key}`}
      className={`flex items-start gap-2 py-1.5 ${indent ? 'pl-6 border-l border-slate-700/60' : ''}`}
    >
      <span className="text-[10px] font-mono text-slate-500 shrink-0 mt-0.5">{node.key}</span>
      <div className="flex-1 min-w-0">
        <span className="text-sm text-white truncate block">{node.summary}</span>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[10px] text-slate-400 px-1.5 py-0.5 bg-slate-700/50 rounded">
            {node.status}
          </span>
          {node.owner && (
            <span className="text-[10px] text-slate-500">{node.owner}</span>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <div data-testid="widget-dependency-map">
      <Card hover padding="sm" className="sm:p-6">
        <div className="flex items-center gap-2 mb-4 pr-8">
          <Icon name="account_tree" className="text-blue-400" size={20} />
          <h2 className="text-lg font-semibold">Dependency Map</h2>
        </div>

        {!epicKey ? (
          <p className="text-sm text-slate-500" data-testid="depmap-no-epic">
            No epic selected. Pass an epic key to see its dependencies.
          </p>
        ) : loading ? (
          <div className="space-y-2">
            <SkeletonLine width="w-3/4" />
            <SkeletonLine width="w-1/2" />
          </div>
        ) : error ? (
          <p className="text-sm text-slate-500">{error}</p>
        ) : graph.nodes.length === 0 ? (
          <p className="text-sm text-slate-500" data-testid="depmap-empty">
            No dependencies found for {epicKey}.
          </p>
        ) : (
          <div className="space-y-1">
            {rootNode && (
              <div data-testid="depmap-root">
                {renderNode(rootNode, false)}
              </div>
            )}
            {inward.length > 0 && (
              <div className="mt-2">
                <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1 pl-1">Blocked by</p>
                {inward.map(({ node }) => node ? renderNode(node, true) : null)}
              </div>
            )}
            {outward.length > 0 && (
              <div className="mt-2">
                <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1 pl-1">Blocks</p>
                {outward.map(({ node }) => node ? renderNode(node, true) : null)}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

