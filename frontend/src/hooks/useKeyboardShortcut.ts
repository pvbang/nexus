// ============================================================
// MekongAI Nexus - Keyboard shortcut hook
// ============================================================

import { useEffect, useCallback } from 'react'

interface ShortcutOptions {
  /** The key to listen for (e.g., 'k', 'Enter', 'Escape') */
  key: string
  /** Whether Ctrl (Windows/Linux) or Meta (Mac) modifier is required */
  ctrlOrMeta?: boolean
  /** Whether Shift modifier is required */
  shift?: boolean
  /** Whether Alt modifier is required */
  alt?: boolean
  /** Whether to prevent default browser behavior */
  preventDefault?: boolean
}

/**
 * Register a keyboard shortcut declaratively.
 * Handles Ctrl/Meta key automatically for cross-platform compatibility
 * (Mac uses Meta/Cmd, Windows/Linux uses Ctrl).
 * Cleans up the event listener on unmount.
 */
export function useKeyboardShortcut(
  options: ShortcutOptions,
  callback: () => void,
): void {
  const {
    key,
    ctrlOrMeta = false,
    shift = false,
    alt = false,
    preventDefault = true,
  } = options

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      // Check modifier keys
      if (ctrlOrMeta && !(event.ctrlKey || event.metaKey)) return
      if (shift && !event.shiftKey) return
      if (alt && !event.altKey) return

      // If no ctrlOrMeta required but Ctrl/Meta is pressed, skip
      // (avoids firing on unrelated browser shortcuts)
      if (!ctrlOrMeta && (event.ctrlKey || event.metaKey)) return

      if (event.key.toLowerCase() === key.toLowerCase()) {
        if (preventDefault) {
          event.preventDefault()
        }
        callback()
      }
    },
    [key, ctrlOrMeta, shift, alt, preventDefault, callback],
  )

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [handleKeyDown])
}
