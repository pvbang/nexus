// ============================================================
// useLazyTree - Lazy-loading directory tree state management
// ============================================================

import { useCallback, useState } from 'react';
import { fetchDirectoryTree } from '../api/artifacts';
import type { LazyTreeNode, TreeEntry } from '../types/artifacts';

interface UseLazyTreeOptions {
  taskId?: string;
  showHidden?: boolean;
}

export function useLazyTree(options: UseLazyTreeOptions = {}) {
  const [roots, setRoots] = useState<LazyTreeNode[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load root level
  const loadRoot = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const entries = await fetchDirectoryTree({
        path: '',
        depth: 1,
        task_id: options.taskId,
        show_hidden: options.showHidden,
      });
      setRoots(entries.map((e) => entryToNode(e, 0)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tree');
    } finally {
      setIsLoading(false);
    }
  }, [options.taskId, options.showHidden]);

  // Toggle expand/collapse a directory node
  const toggleNode = useCallback(
    async (nodePath: string) => {
      setRoots((prev) =>
        updateNodeInTree(prev, nodePath, (node) => {
          if (node.isExpanded) {
            // Collapse
            return { ...node, isExpanded: false };
          }
          // If children already loaded, just expand
          if (node.children !== null) {
            return { ...node, isExpanded: true };
          }
          // Need to load children
          return { ...node, isExpanded: true, isLoading: true };
        }),
      );

      // Check if we need to fetch
      const targetNode = findNodeInTree(roots, nodePath);
      if (targetNode && targetNode.children === null) {
        try {
          const entries = await fetchDirectoryTree({
            path: nodePath,
            depth: 1,
            task_id: options.taskId,
            show_hidden: options.showHidden,
          });
          setRoots((prev) =>
            updateNodeInTree(prev, nodePath, (node) => ({
              ...node,
              isLoading: false,
              children: entries.map((e) => entryToNode(e, node.depth + 1)),
            })),
          );
        } catch {
          setRoots((prev) =>
            updateNodeInTree(prev, nodePath, (node) => ({
              ...node,
              isLoading: false,
              children: [],
            })),
          );
        }
      }
    },
    [roots, options.taskId, options.showHidden],
  );

  // Flatten tree for virtualized rendering
  const flatRows = flattenTree(roots);

  return { roots, flatRows, isLoading, error, loadRoot, toggleNode };
}

// Helper: convert API entry to tree node
function entryToNode(entry: TreeEntry, depth: number): LazyTreeNode {
  return {
    entry,
    children: entry.type === 'directory' ? null : [],
    isExpanded: false,
    isLoading: false,
    depth,
  };
}

// Helper: find node by path
function findNodeInTree(nodes: LazyTreeNode[], path: string): LazyTreeNode | null {
  for (const node of nodes) {
    if (node.entry.path === path) return node;
    if (node.children) {
      const found = findNodeInTree(node.children, path);
      if (found) return found;
    }
  }
  return null;
}

// Helper: immutably update a specific node
function updateNodeInTree(
  nodes: LazyTreeNode[],
  path: string,
  updater: (node: LazyTreeNode) => LazyTreeNode,
): LazyTreeNode[] {
  return nodes.map((node) => {
    if (node.entry.path === path) return updater(node);
    if (node.children) {
      const newChildren = updateNodeInTree(node.children, path, updater);
      if (newChildren !== node.children) return { ...node, children: newChildren };
    }
    return node;
  });
}

// Helper: flatten tree into flat rows for virtualization
function flattenTree(nodes: LazyTreeNode[]): LazyTreeNode[] {
  const result: LazyTreeNode[] = [];
  function walk(list: LazyTreeNode[]) {
    for (const node of list) {
      result.push(node);
      if (node.isExpanded && node.children) {
        walk(node.children);
      }
    }
  }
  walk(nodes);
  return result;
}
