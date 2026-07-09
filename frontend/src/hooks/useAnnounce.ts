import { useCallback, useRef, useEffect } from 'react';

type Politeness = 'polite' | 'assertive';

/**
 * Hook that provides a function to announce messages to screen readers
 * via an ARIA live region (injected into DOM on mount).
 */
export function useAnnounce() {
  const regionRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // Create live region element
    const el = document.createElement('div');
    el.setAttribute('aria-live', 'polite');
    el.setAttribute('aria-atomic', 'true');
    el.setAttribute('role', 'status');
    el.style.position = 'absolute';
    el.style.width = '1px';
    el.style.height = '1px';
    el.style.padding = '0';
    el.style.margin = '-1px';
    el.style.overflow = 'hidden';
    el.style.clip = 'rect(0, 0, 0, 0)';
    el.style.whiteSpace = 'nowrap';
    el.style.border = '0';
    el.id = 'nexus-aria-announcer';

    // Avoid duplicates
    const existing = document.getElementById('nexus-aria-announcer');
    if (existing) {
      regionRef.current = existing as HTMLDivElement;
    } else {
      document.body.appendChild(el);
      regionRef.current = el;
    }

    return () => {
      // Don't remove if other instances might use it
    };
  }, []);

  const announce = useCallback(
    (message: string, politeness: Politeness = 'polite') => {
      if (!regionRef.current) return;
      regionRef.current.setAttribute('aria-live', politeness);
      // Clear then set to trigger announcement
      regionRef.current.textContent = '';
      requestAnimationFrame(() => {
        if (regionRef.current) {
          regionRef.current.textContent = message;
        }
      });
    },
    [],
  );

  return announce;
}
