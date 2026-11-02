// ============================================================
// Nexus - SessionGroupedTree
// Displays artifacts in a hierarchical structure:
// Session > Task > (Context Docs | Artifacts)
// Uses GET /artifacts/grouped endpoint for data.
// ============================================================

import { useState, useEffect, useCallback, memo } from 'react';
import {
  ChevronRight, Folder, FolderOpen, Loader2,
  MessageSquare, FileText, Brain, Download,
} from 'lucide-react';
import { apiGet } from '../../api';
import { getDownloadUrl, getZipDownloadUrl, getTaskZipDownloadUrl, getSessionZipDownloadUrl } from '../../api/artifacts';
import { cn } from '../../lib/cn';

// ---------------------------------------------------------------------------
// Types (matching backend /artifacts/grouped response)
// ---------------------------------------------------------------------------

interface ArtifactFile {
  path: string;
  name: string;
  size: number;
  modified: number;
  type: string;
  is_binary?: boolean;
  is_context_doc?: boolean;
  task_id?: string;
  download_url?: string;
}

interface TaskGroup {
  task_id: string;
  task_title: string;
  file_count: number;
  total_size: number;
  latest_modified: number;
  context_docs: ArtifactFile[];
  artifacts: ArtifactFile[];
  files: ArtifactFile[];
}

interface SessionGroup {
  session_id: string;
  session_title: string;
  task_count: number;
  file_count: number;
  total_size: number;
  latest_modified: number;
  tasks: TaskGroup[];
}

interface GroupedResponse {
  sessions: SessionGroup[];
  orphan_tasks: TaskGroup[];
  global: ArtifactFile[];
  total_files: number;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface SessionGroupedTreeProps {
  onFileSelect: (filePath: string) => void;
  selectedFile?: string | null;
}

// ---------------------------------------------------------------------------
// File icon helper
// ---------------------------------------------------------------------------

const EXT_ICONS: Record<string, string> = {
  py: '🐍', js: '🟨', ts: '🔷', tsx: '🔷', md: '📝',
  json: '📋', html: '🌐', css: '🎨', pdf: '📕', csv: '📊',
};

function getFileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  return EXT_ICONS[ext] || '📄';
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// FileRow
// ---------------------------------------------------------------------------

const FileRow = memo(function FileRow({
  file,
  depth,
  onSelect,
  isSelected,
  taskId,
}: {
  file: ArtifactFile;
  depth: number;
  onSelect: (path: string) => void;
  isSelected: boolean;
  taskId?: string;
}) {
  const fullPath = taskId ? `tasks/${taskId}/${file.path}` : file.path;
  const paddingLeft = depth * 16 + 20;

  return (
    <div
      className={cn(
        'group flex items-center gap-1.5 px-2 rounded transition-colors cursor-pointer',
        isSelected
          ? 'bg-blue-50 border border-blue-200'
          : 'hover:bg-gray-100 border border-transparent',
      )}
      style={{ paddingLeft: `${paddingLeft}px`, height: '28px' }}
      onClick={() => onSelect(fullPath)}
    >
      <span className="text-sm shrink-0">{file.is_context_doc ? '📋' : getFileIcon(file.name)}</span>
      <span className={cn('text-xs font-medium truncate flex-1', isSelected ? 'text-blue-700' : 'text-gray-900')}>
        {file.name}
      </span>
      <span className="text-[10px] text-gray-400 shrink-0">{formatSize(file.size)}</span>
      <a
        href={getDownloadUrl(fullPath)}
        download
        onClick={(e) => e.stopPropagation()}
        className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-gray-600 transition-all shrink-0"
      >
        <Download size={11} />
      </a>
    </div>
  );
});

// ---------------------------------------------------------------------------
// Build tree from flat file list
// ---------------------------------------------------------------------------

interface TreeNode {
  name: string;
  path: string; // relative path from task root
  children: Map<string, TreeNode>;
  file?: ArtifactFile;
}

function buildFileTree(files: ArtifactFile[], stripPrefix?: string): TreeNode {
  const root: TreeNode = { name: '', path: stripPrefix?.replace(/\/$/, '') ?? '', children: new Map() };
  for (const file of files) {
    const displayPath = stripPrefix && file.path.startsWith(stripPrefix)
      ? file.path.slice(stripPrefix.length)
      : file.path;
    const parts = displayPath.split('/');
    let current = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const dirName = parts[i];
      if (!current.children.has(dirName)) {
        const fullDirPath = stripPrefix
          ? stripPrefix + parts.slice(0, i + 1).join('/')
          : parts.slice(0, i + 1).join('/');
        current.children.set(dirName, {
          name: dirName,
          path: fullDirPath,
          children: new Map(),
        });
      }
      current = current.children.get(dirName)!;
    }
    const fileName = parts[parts.length - 1];
    current.children.set(fileName, {
      name: fileName,
      path: file.path,
      children: new Map(),
      file,
    });
  }
  return root;
}

// ---------------------------------------------------------------------------
// Recursive tree renderer
// ---------------------------------------------------------------------------

function FileTreeNodes({
  node,
  depth,
  taskId,
  onSelect,
  selectedFile,
}: {
  node: TreeNode;
  depth: number;
  taskId?: string;
  onSelect: (path: string) => void;
  selectedFile?: string | null;
}) {
  const sorted = Array.from(node.children.values()).sort((a, b) => {
    const aIsDir = !a.file;
    const bIsDir = !b.file;
    if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <>
      {sorted.map((child) => {
        if (child.file) {
          return (
            <FileRow
              key={child.path}
              file={child.file}
              depth={depth}
              onSelect={onSelect}
              isSelected={selectedFile === (taskId ? `tasks/${taskId}/${child.file.path}` : child.file.path)}
              taskId={taskId}
            />
          );
        }
        return (
          <CollapsibleSection
            key={child.path}
            label={child.name}
            icon={<Folder className="w-3 h-3 text-amber-500 shrink-0" />}
            depth={depth}
            defaultOpen={false}
            downloadUrl={getZipDownloadUrl(child.path, taskId)}
          >
            {() => (
              <FileTreeNodes
                node={child}
                depth={depth + 1}
                taskId={taskId}
                onSelect={onSelect}
                selectedFile={selectedFile}
              />
            )}
          </CollapsibleSection>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// Collapsible section
// ---------------------------------------------------------------------------

function CollapsibleSection({
  label,
  icon,
  count,
  depth,
  defaultOpen = false,
  downloadUrl,
  children,
}: {
  label: string;
  icon: React.ReactNode;
  count?: number;
  depth: number;
  defaultOpen?: boolean;
  downloadUrl?: string;
  // Function form is rendered lazily: children() runs only when the section
  // is open, so heavy work (e.g. buildFileTree) is skipped while collapsed.
  children: React.ReactNode | (() => React.ReactNode);
}) {
  const [open, setOpen] = useState(defaultOpen);
  const paddingLeft = depth * 16 + 4;

  return (
    <div>
      <div
        className="group w-full flex items-center gap-1.5 px-2 py-1 hover:bg-gray-100 rounded transition-colors"
        style={{ paddingLeft: `${paddingLeft}px` }}
      >
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
        >
          <ChevronRight className={cn('w-3 h-3 text-gray-400 transition-transform shrink-0', open && 'rotate-90')} />
          {icon}
          <span className="text-xs font-semibold text-gray-700 truncate flex-1">{label}</span>
        </button>
        {count != null && (
          <span className="text-[10px] text-gray-400 shrink-0 bg-gray-100 px-1.5 rounded-full">{count}</span>
        )}
        {downloadUrl && (
          <a
            href={downloadUrl}
            download
            onClick={(e) => e.stopPropagation()}
            title="Download as ZIP"
            className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-all shrink-0"
          >
            <Download size={12} />
          </a>
        )}
      </div>
      {open && <div>{typeof children === 'function' ? children() : children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SessionGroupedTree({ onFileSelect, selectedFile }: SessionGroupedTreeProps) {
  const [data, setData] = useState<GroupedResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await apiGet<GroupedResponse>('/artifacts/grouped');
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
        Loading artifacts...
      </div>
    );
  }

  if (error) {
    return <div className="p-4 text-sm text-red-600">Error: {error}</div>;
  }

  if (!data || data.total_files === 0) {
    return <div className="p-4 text-sm text-gray-500">No artifacts yet.</div>;
  }

  return (
    <div className="space-y-1">
      {/* Sessions */}
      {data.sessions.map((session) => (
        <CollapsibleSection
          key={session.session_id}
          label={session.session_title}
          icon={<MessageSquare className="w-3.5 h-3.5 text-blue-500 shrink-0" />}
          count={session.file_count}
          depth={0}
          defaultOpen={false}
          downloadUrl={getSessionZipDownloadUrl(session.session_id)}
        >
          {() => session.tasks.map((task) => (
            <CollapsibleSection
              key={task.task_id}
              label={task.task_title}
              icon={<Folder className="w-3.5 h-3.5 text-amber-500 shrink-0" />}
              count={task.file_count}
              depth={1}
              defaultOpen={false}
              downloadUrl={getTaskZipDownloadUrl(task.task_id)}
            >
              {() => (
                <>
                  {/* Context docs section */}
                  {task.context_docs.length > 0 && (
                    <CollapsibleSection
                      label="Context"
                      icon={<Brain className="w-3 h-3 text-purple-500 shrink-0" />}
                      count={task.context_docs.length}
                      depth={2}
                      defaultOpen={false}
                      downloadUrl={getZipDownloadUrl('_context', task.task_id)}
                    >
                      {() => (
                        <FileTreeNodes
                          node={buildFileTree(task.context_docs, '_context/')}
                          depth={3}
                          taskId={task.task_id}
                          onSelect={onFileSelect}
                          selectedFile={selectedFile}
                        />
                      )}
                    </CollapsibleSection>
                  )}
                  {/* Work / Artifact files */}
                  {task.artifacts.length > 0 && (
                    <CollapsibleSection
                      label="Work"
                      icon={<FileText className="w-3 h-3 text-blue-500 shrink-0" />}
                      count={task.artifacts.length}
                      depth={2}
                      defaultOpen={false}
                      downloadUrl={getZipDownloadUrl('_work', task.task_id)}
                    >
                      {() => (
                        <FileTreeNodes
                          node={buildFileTree(task.artifacts, '_work/')}
                          depth={3}
                          taskId={task.task_id}
                          onSelect={onFileSelect}
                          selectedFile={selectedFile}
                        />
                      )}
                    </CollapsibleSection>
                  )}
                </>
              )}
            </CollapsibleSection>
          ))}
        </CollapsibleSection>
      ))}

      {/* Orphan tasks */}
      {data.orphan_tasks.length > 0 && (
        <CollapsibleSection
          label="Other Tasks"
          icon={<FolderOpen className="w-3.5 h-3.5 text-gray-500 shrink-0" />}
          count={data.orphan_tasks.reduce((sum, t) => sum + t.file_count, 0)}
          depth={0}
          defaultOpen={false}
        >
          {() => data.orphan_tasks.map((task) => (
            <CollapsibleSection
              key={task.task_id}
              label={task.task_title}
              icon={<Folder className="w-3.5 h-3.5 text-amber-500 shrink-0" />}
              count={task.file_count}
              depth={1}
              defaultOpen={false}
              downloadUrl={getTaskZipDownloadUrl(task.task_id)}
            >
              {() => (
                <FileTreeNodes
                  node={buildFileTree(task.files)}
                  depth={2}
                  taskId={task.task_id}
                  onSelect={onFileSelect}
                  selectedFile={selectedFile}
                />
              )}
            </CollapsibleSection>
          ))}
        </CollapsibleSection>
      )}

      {/* Global files */}
      {data.global.length > 0 && (
        <CollapsibleSection
          label="Global Files"
          icon={<FileText className="w-3.5 h-3.5 text-gray-500 shrink-0" />}
          count={data.global.length}
          depth={0}
          defaultOpen={false}
          downloadUrl={getZipDownloadUrl('')}
        >
          {() => (
            <FileTreeNodes
              node={buildFileTree(data.global)}
              depth={1}
              onSelect={onFileSelect}
              selectedFile={selectedFile}
            />
          )}
        </CollapsibleSection>
      )}
    </div>
  );
}
