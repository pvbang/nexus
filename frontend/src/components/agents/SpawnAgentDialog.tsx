// ============================================================
// Nexus - SpawnAgentDialog
// Modal form for creating a new agent with role template
// selection, name, description, personality, skills, tools
// ============================================================

import { useState, useCallback } from 'react'
import { cn } from '../../lib/cn'
import { Badge } from '../ui/Badge'
import { apiPost } from '../../api'

// ---------------------------------------------------------------------------
// Agent role templates (mirrors backend AGENT_TEMPLATES)
// ---------------------------------------------------------------------------

interface RoleTemplate {
  role: string
  label: string
  emoji: string
  description: string
  personality: string
  skills: string[]
  tools: string[]
}

const ROLE_TEMPLATES: RoleTemplate[] = [
  {
    role: 'researcher',
    label: 'Researcher',
    emoji: '🔍',
    description: 'Chuyên gia tìm kiếm, thu thập và xác minh thông tin từ nhiều nguồn web, API, và tài liệu.',
    personality: 'Methodical, curious, detail-oriented. Always verifies sources.',
    skills: ['web_search', 'document_analysis', 'data_extraction', 'fact_checking'],
    tools: ['web_search', 'fetch_url', 'browser_navigate', 'browser_extract'],
  },
  {
    role: 'developer',
    label: 'Developer',
    emoji: '💻',
    description: 'Lập trình viên tạo code, chạy lệnh, và xây dựng giải pháp kỹ thuật trong nhiều ngôn ngữ.',
    personality: 'Pragmatic, efficient, writes clean code. Follows best practices.',
    skills: ['code_generation', 'code_review', 'debugging', 'tool_creation'],
    tools: ['write_file', 'read_file', 'execute_command', 'search_files'],
  },
  {
    role: 'analyst',
    label: 'Analyst',
    emoji: '📊',
    description: 'Phân tích dữ liệu chiến lược, nhận diện patterns, đưa ra insights và dự báo dựa trên số liệu.',
    personality: 'Analytical, data-driven, sees patterns others miss.',
    skills: ['data_analysis', 'pattern_recognition', 'report_generation', 'visualization'],
    tools: ['web_search', 'fetch_url', 'write_file', 'generate_report'],
  },
  {
    role: 'writer',
    label: 'Writer',
    emoji: '✍️',
    description: 'Sáng tạo nội dung, viết báo cáo, tài liệu và bài viết chuyên nghiệp đa ngôn ngữ.',
    personality: 'Creative, articulate, adapts tone to audience.',
    skills: ['content_creation', 'copywriting', 'editing', 'translation'],
    tools: ['web_search', 'fetch_url', 'write_file', 'generate_word_document'],
  },
  {
    role: 'coordinator',
    label: 'Coordinator',
    emoji: '📋',
    description: 'Điều phối team agents, phân công nhiệm vụ, và tổng hợp kết quả thành phản hồi cuối cùng.',
    personality: 'Organized, diplomatic, ensures smooth collaboration.',
    skills: ['task_management', 'conflict_resolution', 'quality_assurance', 'progress_tracking'],
    tools: ['web_search', 'fetch_url', 'write_file', 'send_message'],
  },
  {
    role: 'judge',
    label: 'Judge',
    emoji: '⚖️',
    description: 'Đánh giá chất lượng debate, chấm điểm proposals, và đưa ra phán quyết công bằng dựa trên bằng chứng.',
    personality: 'Fair, critical thinker, evaluates objectively.',
    skills: ['evaluation', 'quality_scoring', 'decision_making'],
    tools: ['web_search', 'fetch_url'],
  },
  {
    role: 'critic',
    label: 'Critic',
    emoji: '🔎',
    description: 'Phân tích phản biện: tìm lỗ hổng, thách thức giả định, đề xuất góc nhìn mới.',
    personality: "Devil's advocate. Challenges assumptions, demands evidence.",
    skills: ['critical_analysis', 'logical_reasoning', 'fact_checking', 'counter_argumentation'],
    tools: ['web_search', 'fetch_url', 'http_request'],
  },
  {
    role: 'architect',
    label: 'Architect',
    emoji: '🏗️',
    description: 'Kiến trúc sư hệ thống — thiết kế architecture, đánh giá trade-offs, đề xuất patterns phù hợp.',
    personality: 'Visionary yet pragmatic. Thinks in systems and patterns.',
    skills: ['system_design', 'architecture_review', 'planning', 'trade_off_analysis'],
    tools: ['web_search', 'fetch_url', 'write_file', 'read_file'],
  },
  {
    role: 'debugger',
    label: 'Debugger',
    emoji: '🪲',
    description: 'Chuyên gia debug — phân tích lỗi, tìm root cause, đề xuất fix và viết test.',
    personality: 'Patient, methodical, traces issues to root cause with precision.',
    skills: ['debugging', 'error_analysis', 'root_cause_finding', 'test_writing'],
    tools: ['read_file', 'write_file', 'execute_command', 'search_files'],
  },
  {
    role: 'assistant',
    label: 'Assistant',
    emoji: '❓',
    description: 'Trợ lý kiến thức — giải thích khái niệm, trả lời câu hỏi, tạo documentation rõ ràng.',
    personality: 'Patient, clear communicator. Explains complex topics simply.',
    skills: ['explanation', 'documentation', 'question_answering', 'knowledge_synthesis'],
    tools: ['web_search', 'fetch_url', 'write_file', 'read_file'],
  },
  {
    role: 'orchestrator',
    label: 'Orchestrator',
    emoji: '🪃',
    description: 'Điều phối đa nhiệm — phân chia task phức tạp, delegate cho agents phù hợp.',
    personality: 'Strategic, decisive, adaptable. Sees the big picture.',
    skills: ['task_decomposition', 'agent_coordination', 'workflow_management', 'result_synthesis'],
    tools: ['web_search', 'fetch_url', 'write_file', 'delegate_task'],
  },
  {
    role: 'security_reviewer',
    label: 'Security',
    emoji: '🔒',
    description: 'Chuyên gia bảo mật — audit code, phát hiện vulnerabilities, đánh giá OWASP risks.',
    personality: 'Vigilant, thorough. Assumes every input is malicious.',
    skills: ['security_audit', 'vulnerability_detection', 'code_review', 'threat_modeling'],
    tools: ['read_file', 'search_files', 'execute_command', 'web_search'],
  },
  {
    role: 'devops',
    label: 'DevOps',
    emoji: '🐳',
    description: 'DevOps chuyên gia — deployment automation, CI/CD, infrastructure as code, monitoring.',
    personality: 'Automation-first mindset. Thinks in pipelines and containers.',
    skills: ['deployment_automation', 'container_management', 'CI_CD_pipelines', 'monitoring_setup'],
    tools: ['execute_command', 'write_file', 'read_file', 'web_search'],
  },
  {
    role: 'data_scientist',
    label: 'Data Scientist',
    emoji: '🔬',
    description: 'Data scientist — machine learning, statistical modeling, data exploration.',
    personality: 'Hypothesis-driven, experimental. Tests assumptions with data.',
    skills: ['machine_learning', 'statistical_analysis', 'data_modeling', 'visualization'],
    tools: ['execute_code', 'write_file', 'read_file', 'generate_chart'],
  },
  {
    role: 'ux_designer',
    label: 'UX Designer',
    emoji: '🎨',
    description: 'UX designer — thiết kế trải nghiệm người dùng, đánh giá usability, accessibility.',
    personality: 'Empathetic, user-centered. Always asks "how does this feel for the user?"',
    skills: ['ux_design', 'usability_analysis', 'accessibility_review', 'prototyping'],
    tools: ['web_search', 'fetch_url', 'write_file', 'read_file'],
  },
  // ── Business & Finance ──
  {
    role: 'quant_trader',
    label: 'Quant Trader',
    emoji: '📈',
    description: 'Giao dịch định lượng — chiến lược thuật toán, backtesting, quản trị vốn.',
    personality: 'Data-obsessed, risk-aware. Thinks in probabilities.',
    skills: ['strategy_design', 'backtesting', 'risk_analysis', 'market_modeling'],
    tools: ['web_search', 'execute_code', 'generate_chart'],
  },
  {
    role: 'macro_economist',
    label: 'Economist',
    emoji: '🏦',
    description: 'Kinh tế vĩ mô — lạm phát, chính sách tiền tệ, dự báo xu hướng kinh tế.',
    personality: 'Big-picture thinker. Connects monetary policy dots.',
    skills: ['economic_analysis', 'policy_interpretation', 'trend_forecasting'],
    tools: ['web_search', 'fetch_url', 'generate_report'],
  },
  {
    role: 'legal_advisor',
    label: 'Legal',
    emoji: '⚖️',
    description: 'Cố vấn pháp lý — hợp đồng, luật doanh nghiệp, sở hữu trí tuệ, tuân thủ.',
    personality: 'Precise, cautious. Every word matters in law.',
    skills: ['contract_analysis', 'legal_research', 'compliance_check'],
    tools: ['web_search', 'fetch_url', 'write_file'],
  },
  {
    role: 'business_strategist',
    label: 'Strategist',
    emoji: '♟️',
    description: 'Chiến lược doanh nghiệp — mô hình kinh doanh, đối thủ, gọi vốn.',
    personality: 'Competitive, visionary. Sees markets as chess boards.',
    skills: ['market_analysis', 'strategy_planning', 'competitive_intelligence'],
    tools: ['web_search', 'fetch_url', 'generate_report'],
  },
  // ── Creative & Multimodal ──
  {
    role: 'multimodal_creator',
    label: 'Creator',
    emoji: '🎬',
    description: 'Sáng tạo đa phương tiện — AI art/video prompt, visual design, content.',
    personality: 'Wildly creative yet technically precise.',
    skills: ['prompt_crafting', 'visual_storytelling', 'content_analysis'],
    tools: ['web_search', 'fetch_url', 'write_file'],
  },
  {
    role: 'storyteller',
    label: 'Storyteller',
    emoji: '📖',
    description: 'Nhà kể chuyện — world-building, phát triển nhân vật, cốt truyện đa tuyến.',
    personality: 'Imaginative, empathetic. Creates characters that feel real.',
    skills: ['storytelling', 'world_building', 'character_design'],
    tools: ['web_search', 'write_file', 'read_file'],
  },
  // ── Humanities & Social Sciences ──
  {
    role: 'psychologist',
    label: 'Psychologist',
    emoji: '🧠',
    description: 'Tâm lý & hành vi — phân tích hành vi, động lực, sức khỏe tinh thần.',
    personality: 'Empathetic listener. Never judges, always seeks to understand.',
    skills: ['behavior_analysis', 'cognitive_assessment', 'motivation_mapping'],
    tools: ['web_search', 'fetch_url', 'write_file'],
  },
  {
    role: 'philosopher',
    label: 'Philosopher',
    emoji: '🦉',
    description: 'Triết học & đạo đức — luân lý, đạo đức AI, tranh biện tư tưởng.',
    personality: 'Socratic questioner. Challenges every assumption.',
    skills: ['ethical_analysis', 'logical_argumentation', 'conceptual_analysis'],
    tools: ['web_search', 'fetch_url', 'write_file'],
  },
  // ── STEM (Non-Software) ──
  {
    role: 'medical_researcher',
    label: 'Medical',
    emoji: '🏥',
    description: 'Nghiên cứu y học — y khoa, dược lý, sinh học phân tử (thông tin, không kê đơn).',
    personality: 'Evidence-based, cautious. Always cites peer-reviewed sources.',
    skills: ['literature_review', 'clinical_analysis', 'research_synthesis'],
    tools: ['web_search', 'fetch_url', 'generate_report'],
  },
  {
    role: 'physicist',
    label: 'Physicist',
    emoji: '⚛️',
    description: 'Vật lý & hóa học — nhiệt động lực học, cơ lượng tử, mô phỏng phản ứng.',
    personality: 'First-principles thinker. Reduces complex phenomena to equations.',
    skills: ['physics_calculation', 'chemical_simulation', 'mathematical_modeling'],
    tools: ['execute_code', 'web_search', 'generate_chart'],
  },
  // ── Life & Everyday ──
  {
    role: 'educator',
    label: 'Educator',
    emoji: '👨‍🏫',
    description: 'Sư phạm — lộ trình học tập, giải thích Feynman, tạo bài kiểm tra.',
    personality: 'Patient, adaptive. Explains until the student truly understands.',
    skills: ['lesson_planning', 'explanation_design', 'assessment_creation'],
    tools: ['web_search', 'write_file', 'read_file'],
  },
  {
    role: 'travel_planner',
    label: 'Travel',
    emoji: '✈️',
    description: 'Quy hoạch hành trình — tối ưu chi phí, lịch trình, visa, thông tin địa phương.',
    personality: 'Detail-oriented, budget-conscious. Knows hidden gems.',
    skills: ['trip_planning', 'budget_optimization', 'destination_research'],
    tools: ['web_search', 'fetch_url', 'write_file'],
  },
]

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SpawnAgentDialogProps {
  isOpen: boolean
  onClose: () => void
  onSpawned: () => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SpawnAgentDialog({ isOpen, onClose, onSpawned }: SpawnAgentDialogProps) {
  const [selectedTemplate, setSelectedTemplate] = useState<RoleTemplate | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [personality, setPersonality] = useState('')
  const [spawning, setSpawning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const resetForm = useCallback(() => {
    setSelectedTemplate(null)
    setName('')
    setDescription('')
    setPersonality('')
    setError(null)
  }, [])

  const handleSelectTemplate = useCallback((template: RoleTemplate) => {
    setSelectedTemplate(template)
    setDescription(template.description)
    setPersonality(template.personality)
    // Auto-generate name if empty
    if (!name) {
      setName(`${template.label} Agent`)
    }
  }, [name])

  const handleSpawn = useCallback(async () => {
    if (!selectedTemplate || !name.trim()) {
      setError('Vui lòng chọn role và nhập tên agent')
      return
    }

    setSpawning(true)
    setError(null)

    try {
      await apiPost('/agents/spawn', {
        name: name.trim(),
        role: selectedTemplate.role,
        description: description.trim() || undefined,
        personality: personality.trim() || undefined,
      })
      onSpawned()
      resetForm()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to spawn agent')
    } finally {
      setSpawning(false)
    }
  }, [selectedTemplate, name, description, personality, onSpawned, resetForm, onClose])

  const handleClose = useCallback(() => {
    resetForm()
    onClose()
  }, [resetForm, onClose])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 dark:bg-black/70"
        onClick={handleClose}
      />

      {/* Dialog */}
      <div className={cn(
        'relative w-full max-w-lg max-h-[85vh] overflow-y-auto mx-4',
        'bg-white dark:bg-[#1a1a2e] rounded-xl shadow-2xl',
        'border border-gray-200 dark:border-[#2a2a45]',
      )}>
        {/* Header */}
        <div className="sticky top-0 bg-white dark:bg-[#1a1a2e] px-6 py-4 border-b border-gray-200 dark:border-[#2a2a45] z-10">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              🤖 Spawn Agent
            </h2>
            <button
              type="button"
              onClick={handleClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg"
            >
              ✕
            </button>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            Chọn role template rồi tùy chỉnh thông tin agent
          </p>
        </div>

        <div className="px-6 py-4 space-y-5">
          {/* Step 1: Role Template Selection */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">
              1. Chọn Role
            </label>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {ROLE_TEMPLATES.map((template) => (
                <button
                  key={template.role}
                  type="button"
                  onClick={() => handleSelectTemplate(template)}
                  className={cn(
                    'p-3 rounded-lg border text-left transition-all',
                    selectedTemplate?.role === template.role
                      ? 'border-brand-500 bg-brand-50 dark:bg-brand-900/20 dark:border-brand-600 ring-1 ring-brand-500'
                      : 'border-gray-200 dark:border-[#2a2a45] hover:border-gray-300 dark:hover:border-[#3a3a55] hover:bg-gray-50 dark:hover:bg-[#22223a]',
                  )}
                >
                  <div className="text-lg mb-1">{template.emoji}</div>
                  <div className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                    {template.label}
                  </div>
                  <div className="text-[10px] text-gray-500 dark:text-gray-400 line-clamp-2 mt-0.5">
                    {template.description}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Step 2: Agent Details (shown after template selection) */}
          {selectedTemplate && (
            <>
              {/* Name */}
              <div>
                <label
                  htmlFor="agent-name"
                  className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-1.5"
                >
                  2. Tên Agent
                </label>
                <input
                  id="agent-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Nhập tên agent..."
                  className={cn(
                    'w-full text-sm py-2 px-3 rounded-lg border transition-colors',
                    'border-gray-200 bg-white text-gray-900 placeholder-gray-400',
                    'focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500',
                    'dark:border-[#2a2a45] dark:bg-[#0f0f23] dark:text-gray-100 dark:placeholder-gray-500',
                  )}
                />
              </div>

              {/* Description */}
              <div>
                <label
                  htmlFor="agent-desc"
                  className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-1.5"
                >
                  3. Mô tả
                </label>
                <textarea
                  id="agent-desc"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                  placeholder="Mô tả ngắn về agent..."
                  className={cn(
                    'w-full text-sm py-2 px-3 rounded-lg border transition-colors resize-none',
                    'border-gray-200 bg-white text-gray-900 placeholder-gray-400',
                    'focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500',
                    'dark:border-[#2a2a45] dark:bg-[#0f0f23] dark:text-gray-100 dark:placeholder-gray-500',
                  )}
                />
              </div>

              {/* Personality */}
              <div>
                <label
                  htmlFor="agent-personality"
                  className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-1.5"
                >
                  4. Tính cách
                </label>
                <input
                  id="agent-personality"
                  type="text"
                  value={personality}
                  onChange={(e) => setPersonality(e.target.value)}
                  placeholder="Mô tả tính cách agent..."
                  className={cn(
                    'w-full text-sm py-2 px-3 rounded-lg border transition-colors',
                    'border-gray-200 bg-white text-gray-900 placeholder-gray-400',
                    'focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500',
                    'dark:border-[#2a2a45] dark:bg-[#0f0f23] dark:text-gray-100 dark:placeholder-gray-500',
                  )}
                />
              </div>

              {/* Preview: Skills & Tools from template */}
              <div className="rounded-lg border border-gray-200 dark:border-[#2a2a45] p-3 bg-gray-50 dark:bg-[#0f0f23]">
                <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">
                  Template Preview
                </p>
                <div className="space-y-2">
                  <div>
                    <span className="text-[10px] font-medium text-gray-500 dark:text-gray-400">Skills:</span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {selectedTemplate.skills.map((s) => (
                        <Badge key={s} variant="success" size="sm">✓ {s.replace(/_/g, ' ')}</Badge>
                      ))}
                    </div>
                  </div>
                  <div>
                    <span className="text-[10px] font-medium text-gray-500 dark:text-gray-400">Tools:</span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {selectedTemplate.tools.map((t) => (
                        <Badge key={t} variant="purple" size="sm">🔧 {t}</Badge>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}

          {/* Error */}
          {error && (
            <p className="text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded-lg">
              ⚠️ {error}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 bg-white dark:bg-[#1a1a2e] px-6 py-4 border-t border-gray-200 dark:border-[#2a2a45] flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={handleClose}
            className={cn(
              'px-4 py-2 text-sm rounded-lg transition-colors',
              'text-gray-700 hover:bg-gray-100',
              'dark:text-gray-300 dark:hover:bg-[#22223a]',
            )}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSpawn}
            disabled={!selectedTemplate || !name.trim() || spawning}
            className={cn(
              'px-4 py-2 text-sm font-medium rounded-lg transition-colors',
              'bg-brand-600 text-white hover:bg-brand-700',
              'disabled:opacity-50 disabled:cursor-not-allowed',
            )}
          >
            {spawning ? 'Spawning...' : '🤖 Spawn Agent'}
          </button>
        </div>
      </div>
    </div>
  )
}
