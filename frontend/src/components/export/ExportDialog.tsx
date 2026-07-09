// ============================================================
// MekongAI Nexus - Export Dialog
// Modal for exporting chat/task results in multiple formats
// ============================================================

import { useState, useMemo, useCallback } from 'react'
import {
  Download,
  FileText,
  FileJson,
  FileCode,
  Globe,
  MessageSquare,
  ListTree,
  FileStack,
  Eye,
} from 'lucide-react'
import { cn } from '../../lib/cn'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { Tabs } from '../ui/Tabs'
import { Card } from '../ui/Card'
import type { SessionMessage, ActionEntry } from '../../api'
import { format } from 'date-fns'

// ============================================================
// Types
// ============================================================

export type ExportFormat = 'markdown' | 'text' | 'json' | 'html'
export type ExportScope = 'messages' | 'messages_events' | 'full_report'

export interface ExportData {
  sessionId: string
  title: string
  messages: SessionMessage[]
  events?: ActionEntry[]
  exportedAt: string
}

export interface ExportDialogProps {
  open: boolean
  onClose: () => void
  sessionId: string
  sessionTitle: string
  messages: SessionMessage[]
  events?: ActionEntry[]
}

// ============================================================
// Format config
// ============================================================

const formatOptions: Array<{
  id: ExportFormat
  label: string
  icon: typeof FileText
  ext: string
  mime: string
}> = [
  { id: 'markdown', label: 'Markdown', icon: FileText, ext: '.md', mime: 'text/markdown' },
  { id: 'text', label: 'Plain Text', icon: FileCode, ext: '.txt', mime: 'text/plain' },
  { id: 'json', label: 'JSON', icon: FileJson, ext: '.json', mime: 'application/json' },
  { id: 'html', label: 'HTML', icon: Globe, ext: '.html', mime: 'text/html' },
]

const scopeTabs = [
  { id: 'messages' as ExportScope, label: 'Messages', icon: <MessageSquare className="h-4 w-4" /> },
  { id: 'messages_events' as ExportScope, label: 'Messages + Events', icon: <ListTree className="h-4 w-4" /> },
  { id: 'full_report' as ExportScope, label: 'Full Report', icon: <FileStack className="h-4 w-4" /> },
]

// ============================================================
// Export formatters
// ============================================================

function formatTimestamp(dateStr: string): string {
  try {
    return format(new Date(dateStr), 'yyyy-MM-dd HH:mm:ss')
  } catch {
    return dateStr
  }
}

function exportAsMarkdown(data: ExportData, scope: ExportScope): string {
  const lines: string[] = [
    `# ${data.title}`,
    '',
    `**Session:** ${data.sessionId}`,
    `**Exported:** ${data.exportedAt}`,
    `**Messages:** ${data.messages.length}`,
    '',
    '---',
    '',
  ]

  // Messages
  lines.push('## Conversation', '')
  for (const msg of data.messages) {
    const roleLabel = msg.role === 'user' ? '👤 User' : msg.role === 'assistant' ? '🤖 Assistant' : '⚙️ System'
    lines.push(`### ${roleLabel}`)
    lines.push(`*${formatTimestamp(msg.created_at)}*`, '')
    lines.push(msg.content, '')
  }

  // Events
  if (scope !== 'messages' && data.events && data.events.length > 0) {
    lines.push('---', '', '## Process Events', '')
    for (const evt of data.events) {
      const agent = evt.agent_name ? ` [${evt.agent_name}]` : ''
      const duration = evt.duration_ms ? ` (${evt.duration_ms}ms)` : ''
      lines.push(`- **${evt.action_type}**${agent}${duration} — ${formatTimestamp(evt.timestamp)}`)
      lines.push(`  ${evt.content}`, '')
    }
  }

  // Full report stats
  if (scope === 'full_report') {
    lines.push('---', '', '## Summary', '')
    const userMsgs = data.messages.filter((m) => m.role === 'user').length
    const assistantMsgs = data.messages.filter((m) => m.role === 'assistant').length
    lines.push(`- User messages: ${userMsgs}`)
    lines.push(`- Assistant messages: ${assistantMsgs}`)
    if (data.events) {
      lines.push(`- Total events: ${data.events.length}`)
      const actionTypes = new Map<string, number>()
      for (const evt of data.events) {
        actionTypes.set(evt.action_type, (actionTypes.get(evt.action_type) ?? 0) + 1)
      }
      for (const [type, count] of actionTypes) {
        lines.push(`  - ${type}: ${count}`)
      }
    }
  }

  return lines.join('\n')
}

function exportAsText(data: ExportData, scope: ExportScope): string {
  const lines: string[] = [
    data.title,
    '='.repeat(data.title.length),
    '',
    `Session: ${data.sessionId}`,
    `Exported: ${data.exportedAt}`,
    '',
  ]

  for (const msg of data.messages) {
    const role = msg.role.toUpperCase()
    lines.push(`[${formatTimestamp(msg.created_at)}] ${role}:`)
    lines.push(msg.content, '')
  }

  if (scope !== 'messages' && data.events && data.events.length > 0) {
    lines.push('', 'PROCESS EVENTS', '-'.repeat(14), '')
    for (const evt of data.events) {
      const agent = evt.agent_name ? ` [${evt.agent_name}]` : ''
      lines.push(`[${formatTimestamp(evt.timestamp)}] ${evt.action_type}${agent}: ${evt.content}`)
    }
  }

  return lines.join('\n')
}

function exportAsJSON(data: ExportData, scope: ExportScope): string {
  const payload: Record<string, unknown> = {
    sessionId: data.sessionId,
    title: data.title,
    exportedAt: data.exportedAt,
    messages: data.messages,
  }

  if (scope !== 'messages' && data.events) {
    payload.events = data.events
  }

  if (scope === 'full_report') {
    const userMsgs = data.messages.filter((m) => m.role === 'user').length
    const assistantMsgs = data.messages.filter((m) => m.role === 'assistant').length
    payload.summary = {
      totalMessages: data.messages.length,
      userMessages: userMsgs,
      assistantMessages: assistantMsgs,
      totalEvents: data.events?.length ?? 0,
    }
  }

  return JSON.stringify(payload, null, 2)
}

function exportAsHTML(data: ExportData, scope: ExportScope): string {
  const messageHTML = data.messages
    .map((msg) => {
      const roleClass = msg.role === 'user' ? 'user' : msg.role === 'assistant' ? 'assistant' : 'system'
      const roleLabel = msg.role === 'user' ? '👤 User' : msg.role === 'assistant' ? '🤖 Assistant' : '⚙️ System'
      const escapedContent = msg.content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\n/g, '<br/>')
      return `<div class="message ${roleClass}"><div class="role">${roleLabel}</div><div class="time">${formatTimestamp(msg.created_at)}</div><div class="content">${escapedContent}</div></div>`
    })
    .join('\n')

  let eventsHTML = ''
  if (scope !== 'messages' && data.events && data.events.length > 0) {
    const items = data.events
      .map((evt) => {
        const agent = evt.agent_name ? ` <span class="agent">[${evt.agent_name}]</span>` : ''
        return `<li><strong>${evt.action_type}</strong>${agent} — ${formatTimestamp(evt.timestamp)}<br/>${evt.content}</li>`
      })
      .join('\n')
    eventsHTML = `<h2>Process Events</h2><ul class="events">${items}</ul>`
  }

  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>${data.title}</title>
<style>
body{font-family:system-ui,sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;background:#0f0f23;color:#e0e0e0}
h1{color:#fff}h2{color:#aaa;margin-top:2rem}
.message{margin:1rem 0;padding:1rem;border-radius:8px;border:1px solid #2a2a45}
.message.user{background:#1a1a3e}.message.assistant{background:#1a2e1a}.message.system{background:#2e2e1a}
.role{font-weight:700;margin-bottom:4px}.time{font-size:0.75rem;color:#888;margin-bottom:8px}
.content{white-space:pre-wrap;line-height:1.5}
.events li{margin:0.5rem 0}.agent{color:#7c9aff}
</style></head><body>
<h1>${data.title}</h1>
<p>Session: ${data.sessionId} &bull; Exported: ${data.exportedAt}</p>
<h2>Conversation</h2>
${messageHTML}
${eventsHTML}
</body></html>`
}

// ============================================================
// Download trigger
// ============================================================

function triggerDownload(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// ============================================================
// ExportDialog component
// ============================================================

export function ExportDialog({
  open,
  onClose,
  sessionId,
  sessionTitle,
  messages,
  events,
}: ExportDialogProps) {
  const [selectedFormat, setSelectedFormat] = useState<ExportFormat>('markdown')
  const [scope, setScope] = useState<ExportScope>('messages')
  const [showPreview, setShowPreview] = useState(false)

  const formatConfig = formatOptions.find((f) => f.id === selectedFormat)!

  const defaultFilename = useMemo(() => {
    const safe = sessionTitle.replace(/[^a-zA-Z0-9-_ ]/g, '').replace(/\s+/g, '-').toLowerCase()
    return `${safe || 'export'}-${sessionId.slice(0, 8)}${formatConfig.ext}`
  }, [sessionTitle, sessionId, formatConfig.ext])

  const [filename, setFilename] = useState(defaultFilename)

  // Update filename when format changes
  const handleFormatChange = useCallback(
    (fmt: ExportFormat) => {
      setSelectedFormat(fmt)
      const newExt = formatOptions.find((f) => f.id === fmt)!.ext
      setFilename((prev) => {
        const base = prev.replace(/\.[^.]+$/, '')
        return `${base}${newExt}`
      })
    },
    [],
  )

  const exportData: ExportData = useMemo(
    () => ({
      sessionId,
      title: sessionTitle,
      messages,
      events,
      exportedAt: new Date().toISOString(),
    }),
    [sessionId, sessionTitle, messages, events],
  )

  const generatedContent = useMemo(() => {
    switch (selectedFormat) {
      case 'markdown':
        return exportAsMarkdown(exportData, scope)
      case 'text':
        return exportAsText(exportData, scope)
      case 'json':
        return exportAsJSON(exportData, scope)
      case 'html':
        return exportAsHTML(exportData, scope)
    }
  }, [exportData, selectedFormat, scope])

  const handleDownload = useCallback(() => {
    triggerDownload(generatedContent, filename, formatConfig.mime)
    onClose()
  }, [generatedContent, filename, formatConfig.mime, onClose])

  return (
    <Modal open={open} onClose={onClose} title="Export Conversation" size="lg">
      <div className="space-y-5">
        {/* Scope tabs */}
        <div>
          <label className="mb-2 block text-xs font-medium uppercase tracking-wider text-text-muted dark:text-gray-500">
            Content Scope
          </label>
          <Tabs
            tabs={scopeTabs}
            activeTab={scope}
            onChange={(id) => setScope(id as ExportScope)}
            variant="pill"
            size="sm"
          />
        </div>

        {/* Format selection */}
        <div>
          <label className="mb-2 block text-xs font-medium uppercase tracking-wider text-text-muted dark:text-gray-500">
            Format
          </label>
          <div className="grid grid-cols-4 gap-2">
            {formatOptions.map((fmt) => {
              const Icon = fmt.icon
              const isSelected = selectedFormat === fmt.id
              return (
                <button
                  key={fmt.id}
                  type="button"
                  onClick={() => handleFormatChange(fmt.id)}
                  className={cn(
                    'flex flex-col items-center gap-1.5 rounded-lg border p-3 transition-all',
                    isSelected
                      ? 'border-brand-500 bg-brand-50 text-brand-700 dark:border-brand-400 dark:bg-brand-900/20 dark:text-brand-300'
                      : 'border-border hover:border-gray-300 dark:border-[#2a2a45] dark:hover:border-[#3a3a55]',
                  )}
                >
                  <Icon className={cn('h-5 w-5', isSelected ? '' : 'text-text-muted dark:text-gray-500')} />
                  <span className="text-xs font-medium">{fmt.label}</span>
                  <span className="text-[10px] text-text-muted dark:text-gray-500">{fmt.ext}</span>
                </button>
              )
            })}
          </div>
        </div>

        {/* Filename */}
        <div>
          <label
            htmlFor="export-filename"
            className="mb-2 block text-xs font-medium uppercase tracking-wider text-text-muted dark:text-gray-500"
          >
            Filename
          </label>
          <input
            id="export-filename"
            type="text"
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            className={cn(
              'w-full rounded-lg border px-3 py-2 text-sm',
              'border-border bg-white text-text-primary',
              'dark:border-[#2a2a45] dark:bg-[#12122a] dark:text-gray-100',
              'focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30',
            )}
          />
        </div>

        {/* Preview toggle */}
        <div>
          <button
            type="button"
            onClick={() => setShowPreview((prev) => !prev)}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-text-muted hover:text-text-primary dark:text-gray-500 dark:hover:text-gray-300"
          >
            <Eye className="h-3.5 w-3.5" />
            {showPreview ? 'Hide Preview' : 'Show Preview'}
          </button>

          {showPreview && (
            <Card variant="default" padding="sm" className="mt-2 max-h-48 overflow-auto">
              <pre className="whitespace-pre-wrap text-xs leading-relaxed text-text-secondary dark:text-gray-400">
                {generatedContent.slice(0, 500)}
                {generatedContent.length > 500 && '\n\n... (truncated)'}
              </pre>
            </Card>
          )}
        </div>

        {/* Stats line */}
        <p className="text-xs text-text-muted dark:text-gray-500">
          {messages.length} messages
          {events && scope !== 'messages' ? ` · ${events.length} events` : ''}
          {' · '}
          ~{(new Blob([generatedContent]).size / 1024).toFixed(1)} KB
        </p>

        {/* Actions */}
        <div className="flex items-center justify-end gap-2 border-t border-border pt-4 dark:border-[#2a2a45]">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            icon={<Download />}
            onClick={handleDownload}
            disabled={!filename.trim()}
          >
            Download {formatConfig.ext}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
