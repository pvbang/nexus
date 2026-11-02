// ============================================================
// Nexus - MessageInput
// Chat input area with file attachments, paste, drag-drop
// Extracted from ChatPanel.tsx lines 1217-1316
// ============================================================

import { useRef, useCallback, useEffect, useMemo } from 'react'
import {
  Send, Paperclip, X, Square, Loader2,
  FileText, Image, File as FileIcon,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { CHAR_LIMITS } from '../../lib/constants'

// ── Types ─────────────────────────────────────────────────────

export interface AttachedFile {
  file: File
  preview?: string
}

export interface MessageInputProps {
  /** Current input text value */
  value: string
  /** Input text change handler */
  onChange: (value: string) => void
  /** Send message handler */
  onSend: () => void
  /** Cancel ongoing stream */
  onCancel: () => void
  /** Whether the system is processing a request */
  isProcessing: boolean
  /** Whether a stream is in progress */
  isStreaming: boolean
  /** Whether files are being uploaded */
  isUploading: boolean
  /** Currently attached files */
  attachedFiles: AttachedFile[]
  /** File selection handler (from file picker) */
  onFileSelect: (files: File[]) => void
  /** Remove attached file by index */
  onFileRemove: (index: number) => void
  /** Paste event handler (for image paste support) */
  onPaste: (e: React.ClipboardEvent) => void
  /** Drop event handler (for drag-drop file support) */
  onDrop: (e: React.DragEvent) => void
  /** Input placeholder override */
  placeholder?: string
}

// ── Helpers ───────────────────────────────────────────────────

function getFileIcon(file: File) {
  if (file.type.startsWith('image/')) return <Image className="w-4 h-4 text-purple-500" />
  if (file.type === 'application/pdf' || file.name.endsWith('.pdf'))
    return <FileText className="w-4 h-4 text-red-500" />
  if (file.name.match(/\.(docx?|pptx?|xlsx?)$/))
    return <FileText className="w-4 h-4 text-blue-500" />
  return <FileIcon className="w-4 h-4 text-gray-500" />
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const ACCEPTED_FILE_TYPES = [
  '.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
  '.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml',
  '.html', '.htm', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp',
  '.wav', '.mp3', '.zip',
  '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.c', '.cpp',
  '.go', '.rs', '.rb', '.php', '.sql', '.sh',
].join(',')

/** Format number with locale separators */
function formatCharCount(n: number): string {
  return n.toLocaleString('vi-VN')
}

/** Determine warning level from char count */
function getCharLevel(len: number): 'none' | 'info' | 'warning' | 'danger' | 'limit' {
  if (len >= CHAR_LIMITS.LIMIT) return 'limit'
  if (len >= CHAR_LIMITS.DANGER) return 'danger'
  if (len >= CHAR_LIMITS.WARNING) return 'warning'
  if (len >= CHAR_LIMITS.INFO) return 'info'
  return 'none'
}

// ── Component ─────────────────────────────────────────────────

export function MessageInput({
  value,
  onChange,
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
  placeholder,
}: MessageInputProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── Character counter logic ───────────────────────────────
  const charLevel = useMemo(() => getCharLevel(value.length), [value.length])
  const isOverLimit = charLevel === 'limit'

  // FIX: Reset textarea height when value is cleared (after send)
  useEffect(() => {
    if (inputRef.current && !value) {
      inputRef.current.style.height = 'auto'
      inputRef.current.style.height = '48px'  // Reset to min-height explicitly
    }
  }, [value])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        // CHAT-INPUT-FIX: Reset height immediately before send to avoid
        // race condition where parent hasn't cleared value yet
        if (inputRef.current) {
          inputRef.current.style.height = 'auto'
          inputRef.current.style.height = '48px'
        }
        onSend()
      }
    },
    [onSend],
  )

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange(e.target.value)
      const textarea = e.target
      textarea.style.height = 'auto'
      textarea.style.height = Math.min(textarea.scrollHeight, 160) + 'px'
    },
    [onChange],
  )

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || [])
      if (files.length > 0) {
        onFileSelect(files)
      }
      if (fileInputRef.current) fileInputRef.current.value = ''
    },
    [onFileSelect],
  )

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
  }, [])

  const resolvedPlaceholder =
    placeholder ??
    (attachedFiles.length > 0
      ? 'Mô tả yêu cầu cho tài liệu đính kèm...'
      : 'Nhập yêu cầu - agents sẽ làm mọi thứ...')

  return (
    <div onDrop={onDrop} onDragOver={handleDragOver}>
      {/* Attached Files Preview */}
      {attachedFiles.length > 0 && (
        <div
          className={cn(
            'border-t px-4 sm:px-6 py-2.5',
            'border-[#e5e7eb] bg-[#fafbfc]',
            'dark:border-[#2a2a45] dark:bg-[#141425]',
          )}
        >
          <div className="flex items-center gap-1.5 mb-1.5">
            <Paperclip className="w-3.5 h-3.5 text-text-muted dark:text-gray-500" />
            <span className="text-[11px] font-semibold text-text-muted dark:text-gray-500 uppercase tracking-wider">
              {attachedFiles.length} file{attachedFiles.length > 1 ? 's' : ''} đính kèm
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {attachedFiles.map((af, idx) => (
              <div
                key={idx}
                className={cn(
                  'group relative flex items-center gap-2 px-2.5 py-1.5 rounded-[8px] border transition-colors',
                  'bg-white border-[#e5e7eb] hover:border-[#bfdbfe]',
                  'dark:bg-[#1a1a2e] dark:border-[#2a2a45] dark:hover:border-[#3b82f6]/40',
                )}
              >
                {af.preview ? (
                  <img src={af.preview} alt="" className="w-8 h-8 rounded object-cover" />
                ) : (
                  getFileIcon(af.file)
                )}
                <div className="max-w-[120px]">
                  <div className="text-[12px] text-text-primary dark:text-gray-200 font-medium truncate">
                    {af.file.name}
                  </div>
                  <div className="text-[10px] text-text-muted dark:text-gray-500">
                    {formatFileSize(af.file.size)}
                  </div>
                </div>
                <button
                  onClick={() => onFileRemove(idx)}
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-red-500 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Input Area */}
      <div
        className={cn(
          'flex-shrink-0 border-t px-4 sm:px-6 py-3',
          'border-[#e5e7eb] bg-white',
          'dark:border-[#2a2a45] dark:bg-[#0f0f1e]',
        )}
      >
        <div className="flex items-center gap-2.5">
          {/* File attach button */}
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isProcessing || isUploading}
            className={cn(
              'shrink-0 w-[48px] h-[48px] rounded-[12px] flex items-center justify-center transition-all duration-200',
              'bg-[#f4f5f7] text-text-secondary hover:bg-[#e5e7eb] hover:text-text-dark active:scale-[0.97]',
              'dark:bg-[#1e1e35] dark:text-gray-400 dark:hover:bg-[#2a2a45] dark:hover:text-gray-200',
              'disabled:opacity-30 disabled:cursor-not-allowed',
            )}
            title="Đính kèm tài liệu"
          >
            {isUploading
              ? <Loader2 className="w-[18px] h-[18px] animate-spin" />
              : <Paperclip className="w-[18px] h-[18px]" strokeWidth={2} />}
          </button>

          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_FILE_TYPES}
            onChange={handleFileInputChange}
            className="hidden"
          />

          {/* Textarea */}
          <div className="flex-1 relative">
            <textarea
              ref={inputRef}
              value={value}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              onPaste={onPaste}
              placeholder={resolvedPlaceholder}
              className={cn(
                'w-full px-4 py-3 rounded-[12px] border text-[14px] resize-none min-h-[48px] max-h-[160px] transition-all duration-200',
                'border-[#e5e7eb] bg-[#fafbfc] text-text-primary placeholder-text-muted',
                'focus:outline-none focus:border-[#3b82f6] focus:ring-[3px] focus:ring-[#3b82f6]/10 focus:bg-white',
                'dark:border-[#2a2a45] dark:bg-[#141425] dark:text-gray-200 dark:placeholder-gray-600',
                'dark:focus:border-[#3b82f6] dark:focus:ring-[#3b82f6]/20 dark:focus:bg-[#1a1a2e]',
              )}
              rows={1}
              disabled={isProcessing}
            />
          </div>

          {/* Send / Cancel button */}
          {isStreaming ? (
            <button
              onClick={onCancel}
              className={cn(
                'shrink-0 w-[48px] h-[48px] rounded-[12px] flex items-center justify-center transition-all duration-200 active:scale-[0.97]',
                'bg-red-500 text-white hover:bg-red-600',
              )}
              title="Cancel streaming"
            >
              <Square className="w-[16px] h-[16px]" fill="white" strokeWidth={0} />
            </button>
          ) : (
            <button
              type="button"
              onClick={onSend}
              disabled={isProcessing || isOverLimit || (!value.trim() && attachedFiles.length === 0)}
              className={cn(
                'shrink-0 w-[48px] h-[48px] rounded-[12px] flex items-center justify-center transition-all duration-200 active:scale-[0.97]',
                (value.trim() || attachedFiles.length > 0) && !isProcessing && !isOverLimit
                  ? 'bg-[#1a1a2e] text-white hover:bg-[#2a2a3e] dark:bg-[#3b82f6] dark:hover:bg-[#2563eb] cursor-pointer'
                  : 'bg-gray-300 dark:bg-gray-700 text-gray-500 dark:text-gray-400 opacity-50 cursor-not-allowed',
              )}
              title={isOverLimit ? 'Vượt giới hạn ký tự cho phép' : isProcessing ? 'Đang xử lý...' : (!value.trim() && attachedFiles.length === 0) ? 'Nhập tin nhắn trước' : 'Gửi tin nhắn (Enter)'}
            >
              <Send className="w-[18px] h-[18px]" strokeWidth={2} />
            </button>
          )}
        </div>

        {/* Character Counter - only shown when text exceeds INFO threshold */}
        {charLevel !== 'none' && (
          <div className="flex items-center justify-between px-1 pt-1.5">
            <span
              className={cn(
                'text-[11px]',
                charLevel === 'info' && 'text-gray-400 dark:text-gray-500',
                charLevel === 'warning' && 'text-amber-500 dark:text-amber-400',
                charLevel === 'danger' && 'text-red-500 dark:text-red-400',
                charLevel === 'limit' && 'text-red-600 dark:text-red-400 font-semibold',
              )}
            >
              {formatCharCount(value.length)} / {formatCharCount(CHAR_LIMITS.LIMIT)} ký tự
            </span>
            <span
              className={cn(
                'text-[11px]',
                charLevel === 'warning' && 'text-amber-600 dark:text-amber-400',
                charLevel === 'danger' && 'text-red-500 dark:text-red-400',
                charLevel === 'limit' && 'text-red-600 dark:text-red-400 font-semibold',
              )}
            >
              {charLevel === 'warning' && 'Văn bản dài sẽ được xử lý từng phần'}
              {charLevel === 'danger' && 'Gần đạt giới hạn'}
              {charLevel === 'limit' && 'Vượt giới hạn cho phép'}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
