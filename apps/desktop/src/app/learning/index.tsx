import { useStore } from '@nanostores/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import {
  analyzeLearningRequirement,
  finalizeLearningRequirement,
  getLearningGoals,
  getLearningStats,
  type LearningGoal,
  type LearningRequirementAnalysis,
  type LearningRequirementDraft,
  type LearningRequirementQuestion,
  type LearningStats
} from '@/hermes'
import { ArrowRight, Brain, Calendar, CheckCircle2, Clock, Loader2, RefreshCw, Send, Target } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $activeGatewayProfile } from '@/store/profile'

import {
  LEARNING_CARD_CLASS,
  LEARNING_CONTENT_CLASS,
  LEARNING_ICON_AMBER_CLASS,
  LEARNING_ICON_GREEN_CLASS,
  LEARNING_ICON_PRIMARY_CLASS,
  LEARNING_ICON_WARM_CLASS,
  LEARNING_MUTED_TEXT_CLASS,
  LEARNING_PAGE_CLASS,
  LEARNING_PANEL_CLASS,
  LEARNING_PROGRESS_CLASS
} from '../learning-theme'
import { useMidnightRefresh } from '../use-midnight-refresh'

const EMPTY_SELECT = '__empty__'

const EXAMPLES = [
  '半年学会 Java 后端找实习',
  '三个月准备英语六级，每天 90 分钟，偏好刷题',
  '零基础学 Python 数据分析，想做作品集'
]

const PENDING_PLAN_STORAGE_KEY = 'hermes-learning-pending-plan'

function errorText(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }

  if (typeof error === 'string') {
    return error
  }

  try {
    return JSON.stringify(error)
  } catch {
    return String(error)
  }
}

function isTransientPlanConnectionError(error: unknown): boolean {
  return /ECONNRESET|ECONNREFUSED|ETIMEDOUT|Timed out connecting|Failed to fetch|NetworkError/i.test(errorText(error))
}

function readPendingPlanAnalysis(): LearningRequirementAnalysis | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    const raw = window.localStorage.getItem(PENDING_PLAN_STORAGE_KEY)

    if (!raw) {
      return null
    }

    const parsed = JSON.parse(raw) as Partial<LearningRequirementAnalysis>

    if (parsed && typeof parsed === 'object' && parsed.draft && parsed.session_id) {
      return parsed as LearningRequirementAnalysis
    }
  } catch {
    window.localStorage.removeItem(PENDING_PLAN_STORAGE_KEY)
  }

  return null
}

function savePendingPlanAnalysis(analysis: LearningRequirementAnalysis) {
  if (typeof window === 'undefined') {
    return
  }

  window.localStorage.setItem(PENDING_PLAN_STORAGE_KEY, JSON.stringify(analysis))
}

function clearPendingPlanAnalysis() {
  if (typeof window === 'undefined') {
    return
  }

  window.localStorage.removeItem(PENDING_PLAN_STORAGE_KEY)
}

const DRAFT_LABELS: Record<keyof LearningRequirementDraft | string, string> = {
  goal_name: '目标',
  purpose: '用途',
  current_level: '基础',
  target_level: '目标水平',
  daily_minutes: '每天时间',
  timeframe: '周期',
  deadline: '截止日期',
  preference: '偏好',
  goal_type: '类型'
}

type OverviewData = {
  goals: LearningGoal[]
  stats: LearningStats
}

function formatMinutes(minutes: number | undefined): string {
  if (!minutes || minutes <= 0) {
    return '-'
  }

  if (minutes < 60) {
    return `${minutes} 分钟`
  }

  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60

  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`
}

function formatNumber(value: null | number | undefined, suffix = ''): string {
  return value == null ? '-' : `${value}${suffix}`
}

function roundHours(value: number): number {
  return Math.round(value * 10) / 10
}

function localStartOfToday(): Date {
  const now = new Date()

  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

function daysUntilDate(value: null | string | undefined): null | number {
  if (!value) {
    return null
  }

  const date = new Date(`${value}T00:00:00`)

  if (Number.isNaN(date.getTime())) {
    return null
  }

  const diff = Math.ceil((date.getTime() - localStartOfToday().getTime()) / 86_400_000)

  return diff > 0 ? diff : null
}

function dateAfterDays(days: number): string {
  const date = localStartOfToday()
  date.setDate(date.getDate() + Math.max(1, Math.round(days)))

  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')

  return `${year}-${month}-${day}`
}

function parseTimeframeDays(value: null | string | undefined): null | number {
  const text = (value || '').trim()

  if (!text) {
    return null
  }

  const dayMatch = /(\d+(?:\.\d+)?)\s*(?:天|日|day|days)/i.exec(text)

  if (dayMatch) {
    return Math.max(1, Math.round(Number(dayMatch[1])))
  }

  const weekMatch = /(\d+(?:\.\d+)?)\s*(?:周|星期|week|weeks)/i.exec(text)

  if (weekMatch) {
    return Math.max(1, Math.round(Number(weekMatch[1]) * 7))
  }

  const monthMatch = /(\d+(?:\.\d+)?)\s*(?:个月|月|month|months)/i.exec(text)

  if (monthMatch) {
    return Math.max(1, Math.round(Number(monthMatch[1]) * 30))
  }

  const plainNumber = /^\d+(?:\.\d+)?$/.exec(text)

  if (plainNumber) {
    return Math.max(1, Math.round(Number(text)))
  }

  return null
}

function draftPeriodDays(draft: LearningRequirementDraft): null | number {
  if (draft.timeframe_days && draft.timeframe_days > 0) {
    return Math.round(draft.timeframe_days)
  }

  return parseTimeframeDays(draft.timeframe) ?? daysUntilDate(draft.deadline)
}

function derivedDailyMinutes(
  estimatedHours: null | number | undefined,
  periodDays: null | number | undefined
): null | number {
  if (!estimatedHours || !periodDays || periodDays <= 0) {
    return null
  }

  return Math.max(1, Math.round(estimatedHours * 60 / periodDays))
}

function syncDraftDailyMinutes(
  draft: LearningRequirementDraft,
  estimatedHours: null | number | undefined,
  fallbackDays?: null | number
): LearningRequirementDraft {
  const periodDays = draftPeriodDays(draft) ?? fallbackDays
  const dailyMinutes = derivedDailyMinutes(estimatedHours, periodDays)

  return dailyMinutes ? { ...draft, daily_minutes: dailyMinutes } : draft
}

function syncRequirementAnalysis(analysis: LearningRequirementAnalysis): LearningRequirementAnalysis {
  const draft = syncDraftDailyMinutes(
    analysis.draft,
    analysis.estimate.estimated_hours,
    analysis.estimate.estimated_days
  )

  const missingFields = missingDraftFields(draft)

  return {
    ...analysis,
    draft,
    missing_fields: missingFields,
    ready: missingFields.length === 0
  }
}

function draftSummary(
  analysis: LearningRequirementAnalysis
): {
  dailyMinutes: null | number
  estimatedHours: null | number
  periodDays: null | number
} {
  const periodDays = draftPeriodDays(analysis.draft) ?? analysis.estimate.estimated_days
  const estimatedHours = analysis.estimate.estimated_hours ?? null
  const dailyMinutes = derivedDailyMinutes(estimatedHours, periodDays)

  return { dailyMinutes, estimatedHours: estimatedHours == null ? null : roundHours(estimatedHours), periodDays }
}

function fitLabel(fit: string): string {
  if (fit === 'comfortable') {
    return '节奏充足'
  }

  if (fit === 'tight') {
    return '节奏偏紧'
  }

  if (fit === 'at_risk') {
    return '风险较高'
  }

  return '待补充'
}

function answerPayload(answers: Record<string, string>, questions: LearningRequirementQuestion[]) {
  const numericFields = new Set(questions.filter(q => q.type === 'number').map(q => q.field))

  return Object.fromEntries(
    Object.entries(answers)
      .map(([field, value]) => {
        const trimmed = value.trim()

        if (!trimmed) {
          return null
        }

        if (numericFields.has(field)) {
          const numeric = Number(trimmed)

          return Number.isFinite(numeric) ? [field, numeric] : null
        }

        return [field, trimmed]
      })
      .filter((entry): entry is [string, number | string] => Boolean(entry))
  )
}

function draftValue(draft: LearningRequirementDraft, field: keyof LearningRequirementDraft | string): string {
  const value = draft[field as keyof LearningRequirementDraft]

  if (value == null) {
    return ''
  }

  return Array.isArray(value) ? value.join('、') : String(value)
}

type DraftEditField =
  | 'raw_text'
  | 'goal_name'
  | 'purpose'
  | 'current_level'
  | 'target_level'
  | 'daily_minutes'
  | 'timeframe'
  | 'timeframe_days'
  | 'deadline'
  | 'preference'
  | 'goal_type'
  | 'tags'

const REQUIRED_DRAFT_FIELDS: DraftEditField[] = [
  'goal_name',
  'purpose',
  'current_level',
  'target_level',
  'preference'
]

const DRAFT_EDIT_FIELDS: DraftEditField[] = [
  'goal_name',
  'purpose',
  'current_level',
  'target_level',
  'timeframe',
  'timeframe_days',
  'deadline',
  'preference',
  'goal_type',
  'tags',
  'raw_text'
]

const DRAFT_EDIT_LABELS: Record<DraftEditField, string> = {
  raw_text: '原始需求',
  goal_name: DRAFT_LABELS.goal_name,
  purpose: DRAFT_LABELS.purpose,
  current_level: DRAFT_LABELS.current_level,
  target_level: DRAFT_LABELS.target_level,
  daily_minutes: '每天投入（分钟）',
  timeframe: DRAFT_LABELS.timeframe,
  timeframe_days: '周期天数',
  deadline: DRAFT_LABELS.deadline,
  preference: DRAFT_LABELS.preference,
  goal_type: DRAFT_LABELS.goal_type,
  tags: '标签'
}

const GOAL_TYPE_OPTIONS = [
  { label: '技能提升', value: 'skill' },
  { label: '知识课程', value: 'knowledge' },
  { label: '考试证书', value: 'certification' },
  { label: '项目实践', value: 'project' }
]

function hasDraftFieldValue(draft: LearningRequirementDraft, field: DraftEditField): boolean {
  if (field === 'timeframe') {
    return Boolean(draft.timeframe || draft.deadline || draft.timeframe_days)
  }

  if (field === 'daily_minutes') {
    return Number(draft.daily_minutes) > 0
  }

  return Boolean(draftValue(draft, field).trim())
}

function missingDraftFields(draft: LearningRequirementDraft | null): DraftEditField[] {
  if (!draft) {
    return REQUIRED_DRAFT_FIELDS
  }

  return REQUIRED_DRAFT_FIELDS.filter(field => !hasDraftFieldValue(draft, field))
}

function normalizeDraftEditValue(field: DraftEditField, value: string): string | number | string[] | undefined {
  const trimmed = value.trim()

  if (!trimmed && field !== 'deadline') {
    return undefined
  }

  if (field === 'daily_minutes' || field === 'timeframe_days') {
    const parsed = Number(trimmed)

    return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : undefined
  }

  if (field === 'deadline') {
    return trimmed || undefined
  }

  if (field === 'tags') {
    return trimmed
      .split(/[，,、]/)
      .map(item => item.trim())
      .filter(Boolean)
  }

  return trimmed
}

function QuestionControl({
  onChange,
  question,
  value
}: {
  onChange: (value: string) => void
  question: LearningRequirementQuestion
  value: string
}) {
  if (question.options?.length) {
    return (
      <Select onValueChange={next => onChange(next === EMPTY_SELECT ? '' : next)} value={value || EMPTY_SELECT}>
        <SelectTrigger className="border-[var(--learn-border)] bg-[var(--learn-card)]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={EMPTY_SELECT}>未选择</SelectItem>
          {question.options.map(option => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  return (
    <Input
      className="border-[var(--learn-border)] bg-[var(--learn-card)]"
      min={question.type === 'number' ? 1 : undefined}
      onChange={event => onChange(event.target.value)}
      placeholder={question.placeholder}
      type={question.type === 'number' ? 'number' : 'text'}
      value={value}
    />
  )
}

function DraftEditor({
  draft,
  missingFields,
  onChange
}: {
  draft: LearningRequirementDraft
  missingFields: DraftEditField[]
  onChange: (field: DraftEditField, value: string) => void
}) {
  const missingSet = new Set<DraftEditField>(missingFields)

  return (
    <div className={cn(LEARNING_PANEL_CLASS, 'grid gap-4 p-4')}>
      <div className="grid gap-4 md:grid-cols-2">
        {DRAFT_EDIT_FIELDS.map(field => {
          const required = REQUIRED_DRAFT_FIELDS.includes(field)
          const isMissing = missingSet.has(field)

          const label = (
            <span className={cn('text-sm font-medium', isMissing ? 'text-[var(--learn-red)]' : LEARNING_MUTED_TEXT_CLASS)}>
              {DRAFT_EDIT_LABELS[field]}
              {required && <span className="ml-1 text-[var(--learn-red)]">必填</span>}
            </span>
          )

          if (field === 'raw_text') {
            return (
              <label className="grid gap-2 md:col-span-2" key={field}>
                {label}
                <Textarea
                  className="min-h-24 resize-y border-[var(--learn-border)] bg-[var(--learn-card)]"
                  onChange={event => onChange(field, event.target.value)}
                  value={draftValue(draft, field)}
                />
              </label>
            )
          }

          if (field === 'goal_type') {
            return (
              <label className="grid gap-2" key={field}>
                {label}
                <Select onValueChange={value => onChange(field, value)} value={draft.goal_type || 'skill'}>
                  <SelectTrigger className="border-[var(--learn-border)] bg-[var(--learn-card)]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {GOAL_TYPE_OPTIONS.map(option => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
            )
          }

          return (
            <label className="grid gap-2" key={field}>
              {label}
              <Input
                className={cn('border-[var(--learn-border)] bg-[var(--learn-card)]', isMissing && 'border-[var(--learn-red)]')}
                min={field === 'daily_minutes' || field === 'timeframe_days' ? 1 : undefined}
                onChange={event => onChange(field, event.target.value)}
                type={field === 'daily_minutes' || field === 'timeframe_days' ? 'number' : field === 'deadline' ? 'date' : 'text'}
                value={draftValue(draft, field)}
              />
            </label>
          )
        })}
      </div>
    </div>
  )
}

function Metric({
  icon: Icon,
  label,
  tone,
  value
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  tone: 'amber' | 'green' | 'primary' | 'warm'
  value: string
}) {
  const iconClass =
    tone === 'green'
      ? LEARNING_ICON_GREEN_CLASS
      : tone === 'amber'
        ? LEARNING_ICON_AMBER_CLASS
        : tone === 'warm'
          ? LEARNING_ICON_WARM_CLASS
          : LEARNING_ICON_PRIMARY_CLASS

  return (
    <div className={cn(LEARNING_CARD_CLASS, 'p-4')}>
      <div className="flex items-center gap-4">
        <div className={cn('flex size-12 shrink-0 items-center justify-center rounded-lg', iconClass)}>
          <Icon className="size-6" />
        </div>
        <div>
          <div className={cn('text-sm', LEARNING_MUTED_TEXT_CLASS)}>{label}</div>
          <div className="mt-1 text-xl font-bold text-foreground">{value}</div>
        </div>
      </div>
    </div>
  )
}

export function LearningView(): React.ReactElement {
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [message, setMessage] = useState(() => searchParams.get('q') ?? '')
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [analysis, setAnalysis] = useState<LearningRequirementAnalysis | null>(null)
  const [isPlanGenerating, setIsPlanGenerating] = useState(false)
  const [pendingPlanAnalysis, setPendingPlanAnalysis] = useState<LearningRequirementAnalysis | null>(null)
  const [planPollDelaySeconds, setPlanPollDelaySeconds] = useState(5)
  const [planGenerationFailed, setPlanGenerationFailed] = useState(false)

  const overviewQuery = useQuery({
    queryFn: async (): Promise<OverviewData> => {
      const [goals, stats] = await Promise.all([getLearningGoals(), getLearningStats()])

      return { goals, stats }
    },
    queryKey: ['learning-overview', activeGatewayProfile],
    retry: 1,
    staleTime: 10_000
  })

  useMidnightRefresh(() => {
    void overviewQuery.refetch()
  })

  const analyzeMutation = useMutation({ mutationFn: analyzeLearningRequirement })
  const finalizeMutation = useMutation({ mutationFn: finalizeLearningRequirement })

  useEffect(() => {
    const storedAnalysis = readPendingPlanAnalysis()

    if (!storedAnalysis) {
      return
    }

    setAnalysis(storedAnalysis)
    setPendingPlanAnalysis(storedAnalysis)
    setPlanPollDelaySeconds(2)
    setIsPlanGenerating(true)
    setPlanGenerationFailed(false)
  }, [])

  const activeGoals = useMemo(
    () => overviewQuery.data?.goals.filter(goal => goal.status === 'active').slice(0, 4) ?? [],
    [overviewQuery.data?.goals]
  )

  const setAnswer = useCallback((field: string, value: string) => {
    setAnswers(prev => ({ ...prev, [field]: value }))
  }, [])

  const updateDraftField = useCallback((field: DraftEditField, value: string) => {
    setAnalysis(prev => {
      if (!prev) {
        return prev
      }

      const nextDraft = { ...prev.draft }
      const normalized = normalizeDraftEditValue(field, value)

      if (normalized === undefined) {
        delete (nextDraft as Record<string, unknown>)[field]
      } else {
        ;(nextDraft as Record<string, unknown>)[field] = normalized
      }

      if (field === 'timeframe_days' && typeof normalized === 'number') {
        nextDraft.timeframe = `${normalized}天`
        nextDraft.deadline = dateAfterDays(normalized)
      }

      if (field === 'timeframe' && typeof normalized === 'string') {
        const parsedDays = parseTimeframeDays(normalized)

        if (parsedDays) {
          nextDraft.timeframe_days = parsedDays
          nextDraft.deadline = dateAfterDays(parsedDays)
        }
      }

      if (field === 'deadline' && typeof normalized === 'string') {
        const parsedDays = daysUntilDate(normalized)

        if (parsedDays) {
          nextDraft.timeframe_days = parsedDays
          nextDraft.timeframe = `${parsedDays}天`
        }
      }

      const syncedDraft = syncDraftDailyMinutes(
        nextDraft,
        prev.estimate.estimated_hours,
        prev.estimate.estimated_days
      )

      const nextMissing = missingDraftFields(syncedDraft)

      return {
        ...prev,
        draft: syncedDraft,
        missing_fields: nextMissing,
        ready: nextMissing.length === 0
      }
    })
  }, [])

  const finishRequirement = useCallback(
    async (requirementAnalysis: LearningRequirementAnalysis, options?: { forceRetry?: boolean; silentGenerating?: boolean }) => {
      try {
        const result = await finalizeMutation.mutateAsync({
          draft: requirementAnalysis.draft,
          force_retry: options?.forceRetry,
          session_id: requirementAnalysis.session_id
        })

        if (result.plan_status === 'generating') {
          const nextAnalysis = {
            ...requirementAnalysis,
            session_id: result.session_id || requirementAnalysis.session_id
          }

          setAnalysis(nextAnalysis)
          setPendingPlanAnalysis(nextAnalysis)
          setPlanPollDelaySeconds(Math.max(2, Math.min(30, result.retry_after_seconds ?? 5)))
          setIsPlanGenerating(true)
          setPlanGenerationFailed(false)
          savePendingPlanAnalysis(nextAnalysis)

          if (!options?.silentGenerating) {
            notify({
              durationMs: 5000,
              kind: 'info',
              message: result.message || '已保留本次请求，完成后会自动创建计划。',
              title: '模型正在生成计划'
            })
          }

          return
        }

        setIsPlanGenerating(false)
        setPendingPlanAnalysis(null)
        setPlanGenerationFailed(false)
        clearPendingPlanAnalysis()

        const successMessage = result.duplicate_goal
          ? result.message
          : result.goal_created
            ? `已创建 ${result.plans.length} 个阶段，${result.tasks.length} 个任务。`
            : `已补充 ${result.plans.length} 个阶段，${result.tasks.length} 个任务。`

        notify({
          kind: 'success',
          message: successMessage,
          title: result.goal?.name ?? requirementAnalysis.draft.goal_name ?? '学习计划'
        })
        setAnalysis(null)
        setMessage('')
        setAnswers({})
        void queryClient.invalidateQueries({ queryKey: ['learning-overview'] })
        void queryClient.invalidateQueries({ queryKey: ['learning-dashboard'] })
        void queryClient.invalidateQueries({ queryKey: ['study-today-tasks'] })
        void queryClient.invalidateQueries({ queryKey: ['today-tasks-page'] })
      } catch (error) {
        if (isTransientPlanConnectionError(error) && (options?.silentGenerating || requirementAnalysis.session_id)) {
          setIsPlanGenerating(true)
          setPendingPlanAnalysis(requirementAnalysis)
          setPlanGenerationFailed(false)
          setPlanPollDelaySeconds(current => Math.max(8, current))
          savePendingPlanAnalysis(requirementAnalysis)

          if (!options?.silentGenerating) {
            notify({
              durationMs: 5000,
              kind: 'info',
              message: '连接刚才中断了一下，计划请求会继续保留并自动重新获取结果。',
              title: '仍在等待模型结果'
            })
          }

          return
        }

        setIsPlanGenerating(false)
        setPendingPlanAnalysis(null)
        setPlanGenerationFailed(true)
        clearPendingPlanAnalysis()
        notifyError(error, '学习计划生成失败')
      }
    },
    [finalizeMutation, queryClient]
  )

  useEffect(() => {
    if (!isPlanGenerating || !pendingPlanAnalysis || finalizeMutation.isPending) {
      return undefined
    }

    const timer = window.setTimeout(() => {
      void finishRequirement(pendingPlanAnalysis, { silentGenerating: true })
    }, planPollDelaySeconds * 1000)

    return () => window.clearTimeout(timer)
  }, [finalizeMutation.isPending, finishRequirement, isPlanGenerating, pendingPlanAnalysis, planPollDelaySeconds])

  const runAnalyze = useCallback(async () => {
    const trimmed = message.trim()
    const payload = answerPayload(answers, analysis?.questions ?? [])

    if (!trimmed && !Object.keys(payload).length) {
      notify({ kind: 'warning', message: '先描述你的学习目标，或者回答下方问题。' })

      return
    }

    try {
      const result = await analyzeMutation.mutateAsync({
        answers: payload,
        message: trimmed || undefined,
        session_id: analysis?.session_id
      })

      setAnalysis(syncRequirementAnalysis(result))
      setAnswers({})
      setPlanGenerationFailed(false)
    } catch (error) {
      notifyError(error, '需求分析失败')
    }
  }, [analysis?.questions, analysis?.session_id, analyzeMutation, answers, message])

  const finalize = useCallback(async () => {
    if (!analysis) {
      return
    }

    await finishRequirement(analysis, { forceRetry: planGenerationFailed })
  }, [analysis, finishRequirement, planGenerationFailed])

  if (overviewQuery.isLoading) {
    return <PageLoader label="正在读取学习数据" />
  }

  if (overviewQuery.isError && !overviewQuery.data) {
    return (
      <div className="grid h-full place-items-center bg-[#f7f4ef] px-6">
        <ErrorState description="请稍后重试，或者先检查桌面后端是否已经启动。" title="学习数据加载失败">
          <Button onClick={() => void overviewQuery.refetch()} size="sm" variant="outline">
            <RefreshCw />
            重试
          </Button>
        </ErrorState>
      </div>
    )
  }

  const stats = overviewQuery.data?.stats
  const questions = analysis?.questions ?? []
  const draftMissing = missingDraftFields(analysis?.draft ?? null)
  const activeQuestions = questions.filter(question => draftMissing.includes(question.field as DraftEditField))
  const ready = Boolean(analysis && draftMissing.length === 0)
  const currentSummary = analysis ? draftSummary(analysis) : null

  return (
    <div className={LEARNING_PAGE_CLASS}>
      <div className={LEARNING_CONTENT_CLASS}>
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-4xl font-bold tracking-normal text-foreground">学习需求智能体</h1>
              <Badge className="bg-[var(--learn-primary-soft)] text-[var(--learn-primary)] hover:bg-[var(--learn-primary-soft)]">
                {isPlanGenerating ? '生成中' : ready ? '可生成' : '交互获取需求'}
              </Badge>
            </div>
            <p className={cn('mt-2 max-w-3xl text-base leading-7', LEARNING_MUTED_TEXT_CLASS)}>
              先把目标说清楚；Hermes 会调用当前模型分析你的个人信息、基础和可用时间，再追问缺口并生成学习计划。
            </p>
          </div>
          <Button
            className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
            onClick={() => void overviewQuery.refetch()}
            variant="outline"
          >
            <RefreshCw />
            刷新
          </Button>
        </header>

        <div className="grid gap-5 md:grid-cols-4">
          <Metric icon={Target} label="进行中的目标" tone="primary" value={formatNumber(stats?.active_goals)} />
          <Metric icon={CheckCircle2} label="今日任务" tone="green" value={`${stats?.today_completed ?? 0}/${stats?.today_tasks ?? 0}`} />
          <Metric icon={Clock} label="累计学习" tone="warm" value={formatNumber(stats?.total_hours_learned, ' 小时')} />
          <Metric icon={Calendar} label="连续学习" tone="amber" value={formatNumber(stats?.streak_days, ' 天')} />
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,24rem)]">
          <main className="grid gap-6">
            <section className={LEARNING_CARD_CLASS}>
              <div className="border-b border-[var(--learn-border)] px-5 py-4">
                <h2 className="text-lg font-bold text-foreground">描述学习需求</h2>
              </div>
              <div className="grid gap-4 p-5">
                <Textarea
                  className="min-h-36 resize-y border-[var(--learn-border)] bg-[var(--learn-soft)] text-base"
                  onChange={event => setMessage(event.target.value)}
                  placeholder="例如：我是计算机大三，算法一般，想半年内学 Java 后端找实习，工作日每天 60 分钟，周末 3 小时。"
                  value={message}
                />
                <div className="flex flex-wrap gap-2">
                  {EXAMPLES.map(example => (
                    <Button
                      className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
                      key={example}
                      onClick={() => setMessage(example)}
                      size="xs"
                      type="button"
                      variant="outline"
                    >
                      {example}
                    </Button>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    className="bg-[var(--learn-primary)] text-white hover:bg-[#718a8b]"
                    disabled={analyzeMutation.isPending || finalizeMutation.isPending || isPlanGenerating}
                    onClick={() => void runAnalyze()}
                    type="button"
                  >
                    {analyzeMutation.isPending || finalizeMutation.isPending || isPlanGenerating ? <Loader2 className="animate-spin" /> : <Send />}
                    {isPlanGenerating ? '模型生成中' : finalizeMutation.isPending ? '正在生成计划' : analysis ? '继续分析需求' : '分析需求'}
                  </Button>
                  {analysis && (
                    <Button
                      className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
                      disabled={!ready || finalizeMutation.isPending || isPlanGenerating}
                      onClick={() => void finalize()}
                      type="button"
                      variant="outline"
                    >
                      {finalizeMutation.isPending || isPlanGenerating ? <Loader2 className="animate-spin" /> : <ArrowRight />}
                      {isPlanGenerating ? '等待模型结果' : planGenerationFailed ? '重新调用模型生成计划' : '调用模型生成计划'}
                    </Button>
                  )}
                </div>
              </div>
            </section>

            {analysis && (
              <section className={LEARNING_CARD_CLASS}>
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--learn-border)] px-5 py-4">
                  <div className="flex items-center gap-3">
                    <div className={cn('flex size-10 items-center justify-center rounded-lg', LEARNING_ICON_PRIMARY_CLASS)}>
                      <Target className="size-5" />
                    </div>
                    <div>
                      <h2 className="text-lg font-bold text-foreground">需求草稿</h2>
                      <div className={cn('text-sm', LEARNING_MUTED_TEXT_CLASS)}>{analysis.classification_label}</div>
                    </div>
                  </div>
                  <Badge className="bg-[var(--learn-amber-soft)] text-[var(--learn-amber)] hover:bg-[var(--learn-amber-soft)]">
                    {fitLabel(analysis.estimate.fit)}
                  </Badge>
                </div>
                <div className="grid gap-5 p-5">
                  <div className="grid gap-4 md:grid-cols-3">
                    <Metric icon={Clock} label="预计投入" tone="warm" value={formatNumber(currentSummary?.estimatedHours, ' 小时')} />
                    <Metric icon={Calendar} label="预计周期" tone="amber" value={formatNumber(currentSummary?.periodDays, ' 天')} />
                    <Metric icon={Brain} label="每日学习" tone="primary" value={formatMinutes(currentSummary?.dailyMinutes ?? undefined)} />
                  </div>

                  <DraftEditor draft={analysis.draft} missingFields={draftMissing} onChange={updateDraftField} />

                  {activeQuestions.length ? (
                    <div className={cn(LEARNING_PANEL_CLASS, 'grid gap-4 p-4')}>
                      <div className="text-base font-bold text-foreground">还需要补充</div>
                      <div className="grid gap-4 md:grid-cols-2">
                        {activeQuestions.map(question => (
                          <label className="grid gap-2" key={question.field}>
                            <span className={cn('text-sm font-medium', LEARNING_MUTED_TEXT_CLASS)}>{question.question}</span>
                            <QuestionControl
                              onChange={value => setAnswer(question.field, value)}
                              question={question}
                              value={answers[question.field] ?? ''}
                            />
                          </label>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className={cn(LEARNING_PANEL_CLASS, 'flex items-center gap-3 px-4 py-3 text-sm text-foreground')}>
                      {isPlanGenerating ? (
                        <Loader2 className="size-5 animate-spin text-[var(--learn-primary)]" />
                      ) : (
                        <CheckCircle2 className="size-5 text-[var(--learn-green)]" />
                      )}
                      {isPlanGenerating ? '模型正在生成阶段计划和任务池，完成后会自动创建。' : '信息已足够，会继续调用模型创建目标、阶段计划和任务池。'}
                    </div>
                  )}
                </div>
              </section>
            )}
          </main>

          <aside className="grid h-fit gap-6">
            <section className={LEARNING_CARD_CLASS}>
              <div className="flex items-center justify-between border-b border-[var(--learn-border)] px-5 py-4">
                <h2 className="text-lg font-bold text-foreground">进行中的目标</h2>
                <Button onClick={() => navigate('/tasks')} size="micro" variant="textStrong">
                  任务
                </Button>
              </div>
              <div className="grid gap-3 p-4">
                {activeGoals.length ? (
                  activeGoals.map(goal => (
                    <div className="rounded-lg border border-[var(--learn-border)] bg-[var(--learn-soft)] px-4 py-3" key={goal.id}>
                      <div className="truncate text-sm font-semibold text-foreground">{goal.name}</div>
                      <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#ebe5db]">
                        <div className={cn('h-full', LEARNING_PROGRESS_CLASS)} style={{ width: `${Math.max(0, Math.min(100, goal.progress))}%` }} />
                      </div>
                    </div>
                  ))
                ) : (
                  <EmptyState className="min-h-32" description="先在左侧描述一个目标。" title="还没有学习目标" />
                )}
              </div>
            </section>
          </aside>
        </div>
      </div>
    </div>
  )
}
