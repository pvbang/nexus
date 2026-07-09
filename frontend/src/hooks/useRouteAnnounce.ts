import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useAnnounce } from './useAnnounce';

/**
 * Announces route changes to screen readers.
 * Place once in the app root.
 */
export function useRouteAnnounce(routeNameMap?: Record<string, string>) {
  const location = useLocation();
  const announce = useAnnounce();

  useEffect(() => {
    const path = location.pathname;
    const defaultNames: Record<string, string> = {
      '/': 'Chat',
      '/chat': 'Chat',
      '/agents': 'Agents',
      '/dashboard': 'Dashboard',
      '/analytics': 'Analytics',
      '/tasks': 'Tasks',
      '/settings': 'Settings',
      '/artifacts': 'Artifacts',
      '/skills': 'Skills',
      '/xskill': 'XSkill',
      '/graph': 'Knowledge Graph',
      '/terminal': 'Terminal',
      '/swarms': 'Swarms',
      '/process': 'Process',
      '/login': 'Login',
      ...routeNameMap,
    };

    const pageName = defaultNames[path] || path.split('/').pop() || 'Page';
    announce(`Navigated to ${pageName}`, 'polite');
  }, [location.pathname, announce, routeNameMap]);
}
