interface SkipLinkProps {
  targetId?: string;
  children?: string;
}

/**
 * Skip-to-content link that becomes visible on focus.
 * Place at the very top of the App component.
 */
export function SkipLink({
  targetId = 'main-content',
  children = 'Skip to main content',
}: SkipLinkProps) {
  return (
    <a
      href={`#${targetId}`}
      className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[9999] focus:px-4 focus:py-2 focus:bg-brand-500 focus:text-white focus:rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-300 focus:text-sm focus:font-medium"
    >
      {children}
    </a>
  );
}
