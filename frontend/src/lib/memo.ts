import { memo, type ComponentType } from 'react';

/**
 * Typed React.memo wrapper with displayName preservation
 */
export function typedMemo<T extends ComponentType<any>>(
  component: T,
  propsAreEqual?: (
    prev: Readonly<React.ComponentProps<T>>,
    next: Readonly<React.ComponentProps<T>>,
  ) => boolean,
): T {
  const memoized = memo(component, propsAreEqual) as unknown as T;
  (memoized as any).displayName = `Memo(${component.displayName || component.name || 'Component'})`;
  return memoized;
}
