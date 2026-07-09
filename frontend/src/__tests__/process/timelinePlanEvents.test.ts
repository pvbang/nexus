import { convertExecutionEventsToTimeline, buildSupplementalTimeline } from '@/components/process/timeline-utils'
import type { ExecutionEvent } from '@/types/execution'

// Backend emits flat events (event_type at top level, payload fields alongside).
// MessageEventExplorer spreads `...fe.data` so assignment fields land at top level.
function ev(overrides: Record<string, unknown> & { event_type: string }): ExecutionEvent {
  return { timestamp: Date.now(), ...overrides } as unknown as ExecutionEvent
}

describe('convertExecutionEventsToTimeline — orchestrator plan events', () => {
  it('renders an Orchestrator Plan card for planning_done (regression: was swallowed by dead else-if)', () => {
    const items = convertExecutionEventsToTimeline([
      ev({
        event_type: 'planning_done',
        complexity: 'medium',
        agents_count: 2,
        turns_planned: 1,
        plan_summary: 'Trả lời lời chào và giới thiệu năng lực',
        selected_agents: ['Knowledge Assistant'],
        assignments: [
          { agent: 'Knowledge Assistant', role: 'researcher', task: 'Chào hỏi và giới thiệu', success_criteria: 'User được chào' },
        ],
      }),
    ])

    const plan = items.find(
      (i) => i.kind === 'agent_thinking' && (i.data as { agentRole: string }).agentRole === 'orchestrator_plan',
    )
    expect(plan).toBeDefined()
    const content = (plan!.data as { content: string }).content
    // assignment detail must be visible
    expect(content).toContain('Knowledge Assistant')
    expect(content).toContain('Chào hỏi và giới thiệu')
  })

  it('renders a card for next_turn_plan with assignments (regression: had no handler → fell through)', () => {
    const items = convertExecutionEventsToTimeline([
      ev({
        event_type: 'next_turn_plan',
        turn: 2,
        objective: 'Đào sâu phân tích',
        assignments: [
          { agent_name: 'Analyst', agent_role: 'analyst', task: 'Phân tích dữ liệu', success_criteria: 'Có bảng số liệu' },
        ],
      }),
    ])

    const card = items.find(
      (i) => i.kind === 'agent_thinking' && (i.data as { agentRole: string }).agentRole === 'orchestrator_plan',
    )
    expect(card).toBeDefined()
    const content = (card!.data as { content: string }).content
    expect(content).toContain('Analyst')
    expect(content).toContain('Phân tích dữ liệu')
  })
})

// Helper: find first agent_thinking card whose content contains a substring
function findCardByContent(items: ReturnType<typeof convertExecutionEventsToTimeline>, needle: string) {
  return items.find(
    (i) => i.kind === 'agent_thinking' && (i.data as { content: string }).content.includes(needle),
  )
}

describe('convertExecutionEventsToTimeline — newly surfaced events', () => {
  it('renders evolution_insight (was lost: no content field → fallback dropped it)', () => {
    const items = convertExecutionEventsToTimeline([
      ev({
        event_type: 'evolution_insight',
        turn: 2,
        improvement_suggestions: ['Thêm kiểm thử biên'],
        blind_spots: ['Chưa xét lỗi mạng'],
        challenge_questions: ['Đã verify chưa?'],
        severity_score: 0.7,
      }),
    ])
    const card = findCardByContent(items, 'Evolution')
    expect(card).toBeDefined()
    expect((card!.data as { content: string }).content).toContain('Thêm kiểm thử biên')
  })

  it('renders metacognitive_insight including the user question when should_ask_user', () => {
    const items = convertExecutionEventsToTimeline([
      ev({
        event_type: 'metacognitive_insight',
        root_cause: 'Vòng lặp tìm kiếm vô ích',
        concrete_action: 'Chuyển sang suy luận từ kiến thức',
        should_ask_user: true,
        user_question: 'Anh muốn ưu tiên tốc độ hay độ chính xác?',
      }),
    ])
    const card = findCardByContent(items, 'Vòng lặp tìm kiếm vô ích')
    expect(card).toBeDefined()
    expect((card!.data as { content: string }).content).toContain('Anh muốn ưu tiên')
  })

  it('renders authorization_required as a blocking card', () => {
    const items = convertExecutionEventsToTimeline([
      ev({ event_type: 'authorization_required', target: '10.0.0.5', reason: 'Out-of-scope target' }),
    ])
    const card = findCardByContent(items, 'Cần cấp phép')
    expect(card).toBeDefined()
    expect((card!.data as { content: string }).content).toContain('Out-of-scope target')
  })

  it('renders agent_failed as an error card with the failure reason', () => {
    const items = convertExecutionEventsToTimeline([
      ev({
        event_type: 'agent_failed',
        agent_name: 'Coder',
        agent_role: 'developer',
        failure_reason: 'Compile error in module X',
      }),
    ])
    const card = findCardByContent(items, 'Compile error in module X')
    expect(card).toBeDefined()
    expect((card!.data as { content: string }).content).toContain('Coder')
  })

  it('renders divergent_thinking_done and workspace_rollback and infrastructure_backoff', () => {
    const items = convertExecutionEventsToTimeline([
      ev({ event_type: 'divergent_thinking_done', strategy: 'Tiếp cận từ rủi ro', key_insights: ['insight A'] }),
      ev({ event_type: 'workspace_rollback', turn: 3, restored_files: ['a.py', 'b.py'] }),
      ev({ event_type: 'infrastructure_backoff', consecutive_failures: 3, wait_seconds: 30, message: 'Waiting 30s' }),
    ])
    expect(findCardByContent(items, 'Tiếp cận từ rủi ro')).toBeDefined()
    expect(findCardByContent(items, 'a.py')).toBeDefined()
    expect(findCardByContent(items, 'Waiting 30s')).toBeDefined()
  })
})

describe('buildSupplementalTimeline — live ProcessStream supplement', () => {
  it('includes orchestrator decision/insight events', () => {
    const items = buildSupplementalTimeline([
      ev({ event_type: 'evolution_insight', turn: 1, improvement_suggestions: ['x'] }),
      ev({ event_type: 'next_turn_plan', turn: 2, objective: 'go', assignments: [{ agent_name: 'A', task: 't' }] }),
      ev({ event_type: 'report_building', section: 'Final Report complete' }),
    ] as unknown as ExecutionEvent[])
    expect(items.length).toBeGreaterThanOrEqual(3)
  })

  it('does NOT include plan/tool/thinking events (buildTimeline already renders those — avoid dupes)', () => {
    const items = buildSupplementalTimeline([
      ev({ event_type: 'planning_done', plan_summary: 'sum', agents_count: 1 }),
      ev({ event_type: 'tool_start', tool: 'search', agent_name: 'A' }),
      ev({ event_type: 'agent_thinking', agent_name: 'A', content: 'hmm' }),
      ev({ event_type: 'synthesis_done' }),
    ] as unknown as ExecutionEvent[])
    expect(items.length).toBe(0)
  })

  it('returns empty for non-array input (defensive)', () => {
    expect(buildSupplementalTimeline(null as unknown as ExecutionEvent[])).toEqual([])
    expect(buildSupplementalTimeline(undefined as unknown as ExecutionEvent[])).toEqual([])
  })
})
