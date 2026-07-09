import { useRef, useCallback } from 'react';

/**
 * Returns a stable function reference that always calls the latest version.
 * Useful to avoid unnecessary re-renders in memoized children.
 */
export function useStableCallback<T extends (...args: any[]) => any>(callback: T): T {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;
  return useCallback((...args: any[]) => callbackRef.current(...args), []) as T;
}
