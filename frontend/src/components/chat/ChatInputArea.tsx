// ============================================================
// Nexus - ChatInputArea
// Wraps ChatToolbar, TokenStatusBar, and MessageInput.
// Extracted from ChatPanel bottom section for performance isolation.
// React.memo prevents re-render from message list or processing state changes.
// ============================================================

import React from 'react'
import { ChatToolbar } from './ChatToolbar'
import { TokenStatusBar } from './TokenStatusBar'
import { MessageInput, type AttachedFile } from './MessageInput'

// ── Props ─────────────────────────────────────────────────────

export interface ChatInputAreaProps {
  sessionId: string
  input: string
  onInputChange: (value: string) => void
  onSend: () => void
  onCancel: () => void
  isProcessing: boolean
  isStreaming: boolean
  isUploading: boolean
  attachedFiles: AttachedFile[]
  onFileSelect: (files: File[]) => void
  onFileRemove: (index: number) => void
  onPaste: (e: React.ClipboardEvent) => void
  onDrop: (e: React.DragEvent) => void
  onModelChange: (model: string) => void
}

// ── Component ─────────────────────────────────────────────────

export const ChatInputArea = React.memo(function ChatInputArea({
  sessionId,
  input,
  onInputChange,
  onSend,
  onCancel,
  isProcessing,
  isStreaming,
  isUploading,
  attachedFiles,
  onFileSelect,
  onFileRemove,
  onPaste,
  onDrop,
  onModelChange,
}: ChatInputAreaProps) {
  return (
    <>
      {/* Toolbar between messages and input */}
      <ChatToolbar sessionId={sessionId} onModelChange={onModelChange} />

      {/* Token usage status bar */}
      <TokenStatusBar sessionId={sessionId} />

      {/* File preview + Input */}
      <MessageInput
        value={input}
        onChange={onInputChange}
        onSend={onSend}
        onCancel={onCancel}
        isProcessing={isProcessing}
        isStreaming={isStreaming}
        isUploading={isUploading}
        attachedFiles={attachedFiles}
        onFileSelect={onFileSelect}
        onFileRemove={onFileRemove}
        onPaste={onPaste}
        onDrop={onDrop}
      />
    </>
  )
}, (prev, next) => {
  return (
    prev.sessionId === next.sessionId &&
    prev.input === next.input &&
    prev.isProcessing === next.isProcessing &&
    prev.isStreaming === next.isStreaming &&
    prev.isUploading === next.isUploading &&
    prev.attachedFiles === next.attachedFiles &&
    prev.onSend === next.onSend &&
    prev.onCancel === next.onCancel &&
    prev.onInputChange === next.onInputChange &&
    prev.onFileSelect === next.onFileSelect &&
    prev.onFileRemove === next.onFileRemove &&
    prev.onPaste === next.onPaste &&
    prev.onDrop === next.onDrop &&
    prev.onModelChange === next.onModelChange
  )
})
