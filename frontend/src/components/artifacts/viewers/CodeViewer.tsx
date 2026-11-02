// ============================================================
// Nexus - CodeViewer Component
// Syntax-highlighted code display with line numbers
// ============================================================

import { useMemo } from 'react';
import { cn } from '../../../lib/cn';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CodeViewerProps {
  content: string;
  language?: string;
  fileName?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const EXTENSION_LANGUAGE_MAP: Record<string, string> = {
  ts: 'typescript',
  tsx: 'typescript',
  js: 'javascript',
  jsx: 'javascript',
  py: 'python',
  rb: 'ruby',
  rs: 'rust',
  go: 'go',
  java: 'java',
  kt: 'kotlin',
  swift: 'swift',
  c: 'c',
  cpp: 'cpp',
  h: 'c',
  hpp: 'cpp',
  cs: 'csharp',
  php: 'php',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  sql: 'sql',
  yaml: 'yaml',
  yml: 'yaml',
  json: 'json',
  jsonl: 'json',
  ndjson: 'json',
  xml: 'xml',
  html: 'html',
  css: 'css',
  scss: 'scss',
  less: 'less',
  toml: 'toml',
  ini: 'ini',
  dockerfile: 'dockerfile',
  makefile: 'makefile',
  lua: 'lua',
  r: 'r',
  dart: 'dart',
  vue: 'vue',
  svelte: 'svelte',
};

function detectLanguage(fileName?: string): string | undefined {
  if (!fileName) return undefined;
  const ext = fileName.split('.').pop()?.toLowerCase();
  if (!ext) return undefined;
  return EXTENSION_LANGUAGE_MAP[ext];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CodeViewer({ content, language, fileName }: CodeViewerProps) {
  const resolvedLanguage = language || detectLanguage(fileName);
  const lines = useMemo(() => content.split('\n'), [content]);
  const lineNumberWidth = String(lines.length).length;

  return (
    <div className="h-full w-full overflow-auto">
      {/* Header */}
      {(fileName || resolvedLanguage) && (
        <div
          className={cn(
            'flex items-center justify-between px-3 py-1.5 border-b sticky top-0 z-10',
            'border-gray-200 bg-gray-50',
          )}
        >
          <span className="text-xs text-gray-600 truncate">
            {fileName || ''}
          </span>
          {resolvedLanguage && (
            <span className="text-[11px] font-mono uppercase tracking-wider text-gray-400">
              {resolvedLanguage}
            </span>
          )}
        </div>
      )}

      {/* Code content */}
      <pre
        className={cn(
          'm-0 p-4 text-[13px] leading-[1.6] font-mono',
          'bg-gray-50 text-gray-800',
        )}
      >
        <code className={resolvedLanguage ? `language-${resolvedLanguage}` : undefined}>
          {lines.map((line, idx) => (
            <div key={idx} className="flex">
              <span
                className={cn(
                  'select-none text-right pr-4 shrink-0',
                  'text-gray-400',
                )}
                style={{ minWidth: `${lineNumberWidth + 1}ch` }}
              >
                {idx + 1}
              </span>
              <span className="flex-1 whitespace-pre-wrap break-all">{line}</span>
            </div>
          ))}
        </code>
      </pre>
    </div>
  );
}
