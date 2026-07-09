import { useRef, useEffect, useCallback, useState, type ReactNode, type RefObject } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

interface VirtualMessageListProps {
  messages: Array<{ id: string; [key: string]: unknown }>;
  renderMessage: (message: any, index: number) => ReactNode;
  estimateSize?: number;
  autoScrollToBottom?: boolean;
  className?: string;
  /** Optional external scroll container ref. If provided, uses it instead of internal one. */
  scrollContainerRef?: RefObject<HTMLDivElement | null>;
}

/** Estimate row height based on content length for better initial layout */
function estimateRowHeight(message: { [key: string]: unknown }, fallback: number): number {
  const content = (message.content as string) || (message.text as string) || ''
  const len = content.length
  // Check if message has execution_events (expandable content = larger estimate)
  const meta = message.metadata as Record<string, unknown> | undefined
  const hasEvents = !!meta?.execution_events
  const baseHeight = hasEvents ? fallback * 1.5 : fallback
  if (len < 80) return Math.max(60, baseHeight * 0.6)
  if (len < 300) return Math.max(120, baseHeight)
  return Math.max(200, baseHeight * 2)
}

/** Threshold (in px) to consider the user "at the bottom" */
const SCROLL_BOTTOM_THRESHOLD = 80

export function VirtualMessageList({
  messages,
  renderMessage,
  estimateSize = 100,
  autoScrollToBottom = true,
  className = '',
  scrollContainerRef: externalScrollRef,
}: VirtualMessageListProps) {
  const internalParentRef = useRef<HTMLDivElement>(null);
  const parentRef = externalScrollRef ?? internalParentRef;
  const containerRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(messages.length);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [scrollMargin, setScrollMargin] = useState(0);

  // Calculate scrollMargin (offset of virtual container from scroll element top)
  // This is needed when the virtual list is inside a scroll container with other content above it
  useEffect(() => {
    if (!externalScrollRef || !containerRef.current || !parentRef.current) return;
    const updateMargin = () => {
      const container = containerRef.current;
      const scrollEl = parentRef.current;
      if (container && scrollEl) {
        setScrollMargin(container.offsetTop);
      }
    };
    updateMargin();
    // Re-measure when messages change (load more may appear/disappear)
    const observer = new ResizeObserver(updateMargin);
    if (parentRef.current) observer.observe(parentRef.current);
    return () => observer.disconnect();
  }, [externalScrollRef, parentRef, messages.length]);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (index) => estimateRowHeight(messages[index], estimateSize),
    overscan: 8,
    scrollMargin,
  });

  // Track whether user is scrolled to bottom
  const handleScroll = useCallback(() => {
    const el = parentRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setIsAtBottom(distanceFromBottom < SCROLL_BOTTOM_THRESHOLD);
  }, [parentRef]);

  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, [handleScroll, parentRef]);

  // Auto-scroll to bottom when new messages arrive (only if user is at bottom)
  useEffect(() => {
    if (!autoScrollToBottom) return;
    if (messages.length > prevCountRef.current && isAtBottom) {
      virtualizer.scrollToIndex(messages.length - 1, { align: 'end', behavior: 'smooth' });
    }
    prevCountRef.current = messages.length;
  }, [messages.length, autoScrollToBottom, isAtBottom, virtualizer]);

  const items = virtualizer.getVirtualItems();

  // When using external scroll container, don't wrap in another scrollable div
  const useExternalScroll = !!externalScrollRef;

  return (
    <div
      ref={useExternalScroll ? containerRef : internalParentRef}
      className={useExternalScroll ? className : `overflow-auto ${className}`}
    >
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {items.map((virtualItem) => (
          <div
            key={messages[virtualItem.index].id}
            data-index={virtualItem.index}
            ref={virtualizer.measureElement}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              transform: `translateY(${virtualItem.start - scrollMargin}px)`,
            }}
          >
            {renderMessage(messages[virtualItem.index], virtualItem.index)}
          </div>
        ))}
      </div>
    </div>
  );
}
