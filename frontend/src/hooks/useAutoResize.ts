// ============================================================
// MekongAI Nexus - Auto-resize textarea hook
// ============================================================

import { useEffect, useRef, type RefObject } from 'react'

/**
 * Auto-resize a textarea element based on its content height.
 * Returns a ref to attach to the textarea element.
 *
 * @param maxHeight - Optional maximum height in pixels. Textarea will scroll beyond this.
 * @returns A ref object to attach to the textarea element.
 */
export function useAutoResize(maxHeight?: number): RefObject<HTMLTextAreaElement | null> {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    const resize = () => {
      // Reset height to auto to get accurate scrollHeight measurement
      textarea.style.height = 'auto'
      const newHeight = textarea.scrollHeight

      if (maxHeight && newHeight > maxHeight) {
        textarea.style.height = `${maxHeight}px`
        textarea.style.overflowY = 'auto'
      } else {
        textarea.style.height = `${newHeight}px`
        textarea.style.overflowY = 'hidden'
      }
    }

    textarea.addEventListener('input', resize)

    // Initial resize
    resize()

    return () => {
      textarea.removeEventListener('input', resize)
    }
  }, [maxHeight])

  return textareaRef
}
