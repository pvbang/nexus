import { useState } from 'react';
import { useProjectStore } from '../../stores/projectStore';

// File type → icon mapping
const FILE_ICONS: Record<string, string> = {
  '.tsx': '⚛️', '.jsx': '⚛️',
  '.ts': '🔷', '.js': '🟡',
  '.py': '🐍',
  '.html': '🌐', '.css': '🎨',
  '.json': '📋', '.yaml': '📋', '.yml': '📋',
  '.md': '📝',
  '.toml': '⚙️', '.cfg': '⚙️',
  '.env': '🔒',
  '.sql': '🗃️',
  '.sh': '🖥️',
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'text-zinc-500',
  writing: 'text-yellow-400 animate-pulse',
  done: 'text-green-400',
  error: 'text-red-400',
};

interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  children?: TreeNode[];
  status?: string;
  language?: string;
}

function buildTree(files: Array<{ path: string; status: string; language: string }>): TreeNode[] {
  const root: TreeNode[] = [];

  for (const file of files) {
    const parts = file.path.split('/');
    let current = root;

    for (let i = 0; i < parts.length; i++) {
      const name = parts[i];
      const isFile = i === parts.length - 1;
      const path = parts.slice(0, i + 1).join('/');

      let existing = current.find(n => n.name === name);
      if (!existing) {
        existing = {
          name,
          path,
          type: isFile ? 'file' : 'dir',
          children: isFile ? undefined : [],
          status: isFile ? file.status : undefined,
          language: isFile ? file.language : undefined,
        };
        current.push(existing);
      }

      if (!isFile && existing.children) {
        current = existing.children;
      }
    }
  }

  // Sort: dirs first, then files, alphabetically
  const sortTree = (nodes: TreeNode[]): TreeNode[] => {
    return nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
      return a.name.localeCompare(b.name);
    }).map(n => ({
      ...n,
      children: n.children ? sortTree(n.children) : undefined,
    }));
  };

  return sortTree(root);
}

function TreeItem({ node, depth = 0 }: { node: TreeNode; depth?: number }) {
  const [expanded, setExpanded] = useState(depth < 2); // Auto-expand first 2 levels

  const ext = node.type === 'file' ? '.' + node.name.split('.').pop() : '';
  const icon = node.type === 'dir'
    ? (expanded ? '📂' : '📁')
    : (FILE_ICONS[ext] || '📄');
  const statusColor = node.status ? STATUS_COLORS[node.status] || '' : '';

  return (
    <div>
      <button
        onClick={() => node.type === 'dir' && setExpanded(!expanded)}
        className={`w-full flex items-center gap-1.5 px-2 py-0.5 text-xs hover:bg-zinc-700/50 rounded ${
          node.type === 'dir' ? 'cursor-pointer' : 'cursor-default'
        }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        <span className="shrink-0">{icon}</span>
        <span className={`truncate ${node.type === 'dir' ? 'text-zinc-300 font-medium' : 'text-zinc-400'}`}>
          {node.name}
        </span>
        {node.status && (
          <span className={`ml-auto shrink-0 ${statusColor}`}>
            {node.status === 'done' ? '✓' : node.status === 'writing' ? '⟳' : node.status === 'error' ? '✕' : '○'}
          </span>
        )}
      </button>

      {expanded && node.children && (
        <div>
          {node.children.map(child => (
            <TreeItem key={child.path} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export function ProjectFileBrowser() {
  const { files, blueprint, isBuilding } = useProjectStore();

  if (!files.length && !blueprint) return null;

  const tree = buildTree(files);
  const doneCount = files.filter(f => f.status === 'done').length;
  const errorCount = files.filter(f => f.status === 'error').length;

  return (
    <div className="bg-zinc-800/50 rounded-lg border border-zinc-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-700 bg-zinc-800">
        <span className="text-sm font-medium text-zinc-300">📁 Project Files</span>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-green-400">{doneCount} ✓</span>
          {errorCount > 0 && <span className="text-red-400">{errorCount} ✕</span>}
          <span className="text-zinc-500">{files.length} total</span>
        </div>
      </div>

      {/* Project name */}
      {blueprint && (
        <div className="px-3 py-1.5 text-xs text-zinc-500 border-b border-zinc-700/50">
          <span className="font-mono text-zinc-400">{blueprint.name}/</span>
        </div>
      )}

      {/* Tree */}
      <div className="py-1 max-h-64 overflow-y-auto">
        {tree.length > 0 ? (
          tree.map(node => <TreeItem key={node.path} node={node} />)
        ) : (
          <div className="px-3 py-4 text-center text-xs text-zinc-600">
            {isBuilding ? 'Đang tạo files...' : 'Chưa có files'}
          </div>
        )}
      </div>
    </div>
  );
}
