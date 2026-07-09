import { useState } from 'react';

interface DiffLine {
  type: 'added' | 'removed' | 'unchanged';
  content: string;
  lineNumber: number;
}

interface DiffHunk {
  filePath: string;
  hunks: DiffLine[];
}

interface CodeDiffViewerProps {
  diffs: DiffHunk[];
  title?: string;
  collapsible?: boolean;
}

export function CodeDiffViewer({ diffs, title = 'Changes', collapsible = true }: CodeDiffViewerProps) {
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set(diffs.map(d => d.filePath)));

  if (!diffs.length) return null;

  const toggleFile = (path: string) => {
    const next = new Set(expandedFiles);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setExpandedFiles(next);
  };

  // Stats
  const totalAdded = diffs.reduce((acc, d) => acc + d.hunks.filter(l => l.type === 'added').length, 0);
  const totalRemoved = diffs.reduce((acc, d) => acc + d.hunks.filter(l => l.type === 'removed').length, 0);

  return (
    <div className="bg-zinc-800/50 rounded-lg border border-zinc-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-700 bg-zinc-800">
        <span className="text-sm font-medium text-zinc-300">{title}</span>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-green-400">+{totalAdded}</span>
          <span className="text-red-400">-{totalRemoved}</span>
          <span className="text-zinc-500">{diffs.length} file(s)</span>
        </div>
      </div>

      {/* Files */}
      {diffs.map(diff => (
        <div key={diff.filePath}>
          {/* File header */}
          <button
            onClick={() => collapsible && toggleFile(diff.filePath)}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-xs bg-zinc-800/80 border-b border-zinc-700/50 hover:bg-zinc-700/50"
          >
            <span className="text-zinc-500">{expandedFiles.has(diff.filePath) ? '▼' : '▶'}</span>
            <span className="text-blue-400 font-mono">{diff.filePath}</span>
            <span className="text-zinc-600 ml-auto">
              +{diff.hunks.filter(l => l.type === 'added').length}
              -{diff.hunks.filter(l => l.type === 'removed').length}
            </span>
          </button>

          {/* Diff content */}
          {expandedFiles.has(diff.filePath) && (
            <div className="font-mono text-xs overflow-x-auto">
              {diff.hunks.map((line, idx) => (
                <div
                  key={idx}
                  className={`flex px-3 py-0 leading-5 ${
                    line.type === 'added' ? 'bg-green-500/10 text-green-300' :
                    line.type === 'removed' ? 'bg-red-500/10 text-red-300' :
                    'text-zinc-400'
                  }`}
                >
                  <span className="w-10 text-right pr-3 text-zinc-600 select-none shrink-0">
                    {line.lineNumber}
                  </span>
                  <span className="w-4 text-center select-none shrink-0">
                    {line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '}
                  </span>
                  <span className="whitespace-pre">{line.content}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// Helper: Parse simple diff text into DiffHunk format
export function parseDiffText(diffText: string, filePath: string = 'file'): DiffHunk {
  const lines = diffText.split('\n');
  const hunks: DiffLine[] = [];
  let lineNum = 1;

  for (const line of lines) {
    if (line.startsWith('+')) {
      hunks.push({ type: 'added', content: line.slice(1), lineNumber: lineNum++ });
    } else if (line.startsWith('-')) {
      hunks.push({ type: 'removed', content: line.slice(1), lineNumber: lineNum });
    } else {
      hunks.push({ type: 'unchanged', content: line, lineNumber: lineNum++ });
    }
  }

  return { filePath, hunks };
}
