import { useStore } from '@nanostores/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import {
  type DailyTask,
  getLearningTaskGuidance,
  getTodayTasks,
  type LearningStats,
  type LearningTaskGuidance,
  type TodayTasksResponse,
  updateTaskStatus,
  type UpdateTaskStatusRequest
} from '@/hermes'
import {
  BookOpen,
  Calendar,
  CheckCircle2,
  ChevronRight,
  CircleIcon,
  Clock,
  ListTodo,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Sparkles,
  Target
} from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $activeGatewayProfile } from '@/store/profile'

import {
  LEARNING_CARD_CLASS,
  LEARNING_CONTENT_CLASS,
  LEARNING_ICON_AMBER_CLASS,
  LEARNING_ICON_GREEN_CLASS,
  LEARNING_ICON_PRIMARY_CLASS,
  LEARNING_MUTED_TEXT_CLASS,
  LEARNING_PAGE_CLASS
} from '../learning-theme'
import { useMidnightRefresh } from '../use-midnight-refresh'

type TimerMode = 'countdown' | 'stopwatch'

function formatDate(value: string): string {
  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date.toLocaleDateString('zh-CN', { day: 'numeric', month: 'short' })
}

function formatDuration(minutes: number): string {
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60)
    const rest = minutes % 60

    return rest ? `${hours}小时${rest}分钟` : `${hours}小时`
  }

  return `${minutes}分钟`
}

function formatTimer(seconds: number): string {
  const normalized = Math.max(0, seconds)
  const hrs = Math.floor(normalized / 3600)
  const mins = Math.floor((normalized % 3600) / 60)
  const secs = normalized % 60

  return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

function durationSeconds(task: DailyTask | null): number {
  if (!task) {
    return 0
  }

  return Math.max(60, Math.round(task.estimated_minutes || 30) * 60)
}

function timerActualMinutes(
  task: DailyTask | null,
  elapsedSeconds: number,
  remainingSeconds: number,
  mode: TimerMode,
  hasTimerStarted: boolean
): number {
  if (!task) {
    return 1
  }

  const plannedMinutes = Math.max(1, Math.round(task.estimated_minutes || 30))

  if (mode === 'stopwatch') {
    if (elapsedSeconds > 0) {
      return Math.max(1, Math.ceil(elapsedSeconds / 60))
    }

    return plannedMinutes
  }

  const plannedSeconds = durationSeconds(task)
  const usedSeconds = Math.max(0, plannedSeconds - remainingSeconds)

  if (hasTimerStarted || usedSeconds > 0) {
    return Math.max(1, Math.ceil(usedSeconds / 60))
  }

  return plannedMinutes
}

function completedTaskActualMinutes(task: DailyTask): number {
  const minutes = Number(task.learning_feedback?.actual_minutes || task.estimated_minutes || 0)

  return Number.isFinite(minutes) ? Math.max(0, Math.round(minutes)) : 0
}

function replaceCompletedTask(task: DailyTask, completedTask: DailyTask): DailyTask {
  return task.id === completedTask.id ? { ...task, ...completedTask } : task
}

function markTaskCompletedInTodayResponse(
  data: TodayTasksResponse | undefined,
  completedTask: DailyTask
): TodayTasksResponse | undefined {
  if (!data) {
    return data
  }

  return {
    ...data,
    ahead_tasks: data.ahead_tasks?.map(task => replaceCompletedTask(task, completedTask)),
    tasks: data.tasks.map(task => replaceCompletedTask(task, completedTask))
  }
}

function bumpStatsAfterCompletion(stats: LearningStats, completedTask: DailyTask, isTodayTask: boolean, wasCompleted: boolean): LearningStats {
  if (wasCompleted) {
    return stats
  }

  const actualHours = completedTaskActualMinutes(completedTask) / 60

  return {
    ...stats,
    today_completed: isTodayTask ? Math.min(stats.today_tasks, stats.today_completed + 1) : stats.today_completed,
    total_hours_learned: Math.round((stats.total_hours_learned + actualHours) * 10) / 10,
    total_tasks_completed: stats.total_tasks_completed + 1
  }
}

interface DashboardCacheData {
  stats: LearningStats
  today: TodayTasksResponse
}

export function StudyView(): React.ReactElement {
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedTaskId = searchParams.get('taskId') ?? ''
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [hasTimerStarted, setHasTimerStarted] = useState(false)
  const [isRunning, setIsRunning] = useState(false)
  const [remainingSeconds, setRemainingSeconds] = useState(0)
  const [timerMode, setTimerMode] = useState<TimerMode>('countdown')
  const guidanceRefreshRef = useRef(false)

  const tasksQuery = useQuery({
    queryFn: getTodayTasks,
    queryKey: ['study-today-tasks', activeGatewayProfile],
    retry: 1,
    staleTime: 10_000
  })

  useMidnightRefresh(() => {
    void tasksQuery.refetch()
  })

  const completeTaskMutation = useMutation({
    mutationFn: ({ body, taskId }: { body: UpdateTaskStatusRequest; taskId: string }) => updateTaskStatus(taskId, body)
  })

  const todayTasks = useMemo(() => tasksQuery.data?.tasks ?? [], [tasksQuery.data?.tasks])
  const aheadTasks = useMemo(() => tasksQuery.data?.ahead_tasks ?? [], [tasksQuery.data?.ahead_tasks])
  const tasks = useMemo(() => [...todayTasks, ...aheadTasks], [aheadTasks, todayTasks])
  const goalNames = useMemo(() => tasksQuery.data?.goal_names ?? {}, [tasksQuery.data?.goal_names])

  const selectedTask = useMemo(
    () => tasks.find(task => task.id === selectedTaskId) ?? null,
    [selectedTaskId, tasks]
  )

  const selectedDurationSeconds = durationSeconds(selectedTask)
  const canStart = Boolean(selectedTask && selectedTask.status !== 'completed')

  const guidanceQuery = useQuery({
    enabled: Boolean(selectedTask),
    queryFn: () => {
      if (!selectedTask) {
        throw new Error('No task selected')
      }

      const refresh = guidanceRefreshRef.current
      guidanceRefreshRef.current = false

      return getLearningTaskGuidance(selectedTask.id, { refresh })
    },
    queryKey: ['learning-task-guidance', activeGatewayProfile, selectedTask?.id],
    refetchInterval: query => {
      const data = query.state.data as LearningTaskGuidance | undefined

      return data?.status === 'generating' ? 5000 : false
    },
    gcTime: 30 * 60_000,
    retry: false,
    staleTime: 10 * 60_000
  })

  const retryGuidance = useCallback(() => {
    guidanceRefreshRef.current = true
    void guidanceQuery.refetch()
  }, [guidanceQuery])

  useEffect(() => {
    setElapsedSeconds(0)
    setHasTimerStarted(false)
    setIsRunning(false)
    setRemainingSeconds(selectedDurationSeconds)
  }, [selectedDurationSeconds, selectedTask?.id])

  useEffect(() => {
    if (!isRunning || !selectedTask) {
      return undefined
    }

    const timer = window.setInterval(() => {
      if (timerMode === 'countdown') {
        setRemainingSeconds(prev => Math.max(0, prev - 1))

        return
      }

      setElapsedSeconds(prev => prev + 1)
    }, 1000)

    return () => window.clearInterval(timer)
  }, [isRunning, selectedTask, timerMode])

  useEffect(() => {
    if (timerMode === 'countdown' && remainingSeconds === 0) {
      setIsRunning(false)
    }
  }, [remainingSeconds, timerMode])

  const handleModeChange = useCallback(
    (mode: TimerMode) => {
      setTimerMode(mode)
      setIsRunning(false)
      setHasTimerStarted(false)
      setElapsedSeconds(0)
      setRemainingSeconds(selectedDurationSeconds)
    },
    [selectedDurationSeconds]
  )

  const handleReset = useCallback(() => {
    setElapsedSeconds(0)
    setHasTimerStarted(false)
    setIsRunning(false)
    setRemainingSeconds(selectedDurationSeconds)
  }, [selectedDurationSeconds])

  const handleSelectTask = useCallback(
    (taskId: string) => {
      setSearchParams({ taskId })
    },
    [setSearchParams]
  )

  const handleToggle = useCallback(() => {
    if (!canStart) {
      return
    }

    if (timerMode === 'countdown' && remainingSeconds <= 0) {
      setRemainingSeconds(selectedDurationSeconds)
    }

    setIsRunning(prev => {
      const next = !prev

      if (next) {
        setHasTimerStarted(true)
      }

      return next
    })
  }, [canStart, remainingSeconds, selectedDurationSeconds, timerMode])

  const handleCompleteRequest = useCallback(async () => {
    if (!selectedTask || selectedTask.status === 'completed') {
      return
    }

    setIsRunning(false)
    setHasTimerStarted(false)
    const actualMinutes = timerActualMinutes(selectedTask, elapsedSeconds, remainingSeconds, timerMode, hasTimerStarted)

    try {
      const completedTask = await completeTaskMutation.mutateAsync({
        body: {
          actual_minutes: actualMinutes,
          status: 'completed'
        },
        taskId: selectedTask.id
      })

      notify({
        kind: 'success',
        message: `已记录本次用时 ${formatDuration(actualMinutes)}。`,
        title: '任务已完成'
      })
      queryClient.setQueriesData<TodayTasksResponse>(
        { queryKey: ['study-today-tasks'] },
        data => markTaskCompletedInTodayResponse(data, completedTask)
      )
      queryClient.setQueriesData<TodayTasksResponse>(
        { queryKey: ['today-tasks-page'] },
        data => markTaskCompletedInTodayResponse(data, completedTask)
      )
      queryClient.setQueriesData<DashboardCacheData>(
        { queryKey: ['learning-dashboard'] },
        data => {
          if (!data) {
            return data
          }

          const wasCompleted = data.today.tasks.some(task => task.id === completedTask.id && task.status === 'completed')
            || (data.today.ahead_tasks ?? []).some(task => task.id === completedTask.id && task.status === 'completed')
          const isTodayTask = data.today.tasks.some(task => task.id === completedTask.id)

          return {
            ...data,
            stats: bumpStatsAfterCompletion(data.stats, completedTask, isTodayTask, wasCompleted),
            today: markTaskCompletedInTodayResponse(data.today, completedTask) ?? data.today
          }
        }
      )
    } catch (error) {
      notifyError(error, '任务完成失败')
    }
  }, [completeTaskMutation, elapsedSeconds, hasTimerStarted, queryClient, remainingSeconds, selectedTask, timerMode])

  const displaySeconds = timerMode === 'countdown' ? remainingSeconds : elapsedSeconds

  if (tasksQuery.isLoading) {
    return <PageLoader label="正在读取今日任务" />
  }

  return (
    <div className={LEARNING_PAGE_CLASS}>
      <div className={cn(LEARNING_CONTENT_CLASS, 'gap-6')}>
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-4xl font-bold tracking-normal text-foreground">专注学习</h1>
            <p className={cn('mt-2 text-base', LEARNING_MUTED_TEXT_CLASS)}>选择今天的一项任务，按预设时长进入专注。</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button
              className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
              onClick={() => navigate('/tasks')}
              type="button"
              variant="outline"
            >
              <ListTodo />
              今日任务
            </Button>
            <Button
              className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
              onClick={() => void tasksQuery.refetch()}
              type="button"
              variant="outline"
            >
              <RefreshCw />
              刷新
            </Button>
          </div>
        </header>

        <section className="grid items-start gap-5 xl:grid-cols-[minmax(0,32rem)_minmax(0,1fr)]">
          <TimerPanel
            canStart={canStart}
            completePending={completeTaskMutation.isPending}
            displaySeconds={displaySeconds}
            isRunning={isRunning}
            mode={timerMode}
            onComplete={handleCompleteRequest}
            onModeChange={handleModeChange}
            onReset={handleReset}
            onToggle={handleToggle}
            task={selectedTask}
          />

          <TaskGuidancePanel
            error={guidanceQuery.error}
            goalName={selectedTask ? goalNames[selectedTask.goal_id] : undefined}
            guidance={guidanceQuery.data}
            isError={guidanceQuery.isError}
            isLoading={guidanceQuery.isLoading}
            onRetry={retryGuidance}
            task={selectedTask}
          />
        </section>

        <TaskSelector
          goalNames={goalNames}
          onSelect={handleSelectTask}
          selectedTaskId={selectedTaskId}
          tasks={tasks}
        />
      </div>
    </div>
  )
}

function TimerPanel({
  canStart,
  completePending,
  displaySeconds,
  isRunning,
  mode,
  onComplete,
  onModeChange,
  onReset,
  onToggle,
  task
}: {
  canStart: boolean
  completePending: boolean
  displaySeconds: number
  isRunning: boolean
  mode: TimerMode
  onComplete: () => void
  onModeChange: (mode: TimerMode) => void
  onReset: () => void
  onToggle: () => void
  task: DailyTask | null
}) {
  const durationLabel = task ? formatDuration(task.estimated_minutes) : '未选择任务'
  const done = task?.status === 'completed'

  return (
    <section className={cn(LEARNING_CARD_CLASS, 'overflow-hidden')}>
      <div className="border-b border-[var(--learn-border)] px-5 py-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className={cn('grid size-11 place-items-center rounded-lg', LEARNING_ICON_PRIMARY_CLASS)}>
              <Clock className="size-6" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-foreground">专注计时</h2>
              <p className={cn('mt-1 text-sm', LEARNING_MUTED_TEXT_CLASS)}>{durationLabel}</p>
            </div>
          </div>
          {done && <Badge className={cn('rounded-full px-3 py-1', LEARNING_ICON_GREEN_CLASS)}>已完成</Badge>}
        </div>
      </div>

      <div className="grid gap-5 p-5">
        <div className="flex rounded-lg bg-[var(--learn-primary-soft)] p-1">
          <ModeButton active={mode === 'countdown'} label="倒计时" onClick={() => onModeChange('countdown')} />
          <ModeButton active={mode === 'stopwatch'} label="计时" onClick={() => onModeChange('stopwatch')} />
        </div>

        <div className="rounded-xl border border-[var(--learn-border)] bg-[var(--learn-soft)] p-6 text-center">
          <p className={cn('text-sm font-medium', LEARNING_MUTED_TEXT_CLASS)}>
            {mode === 'countdown' ? '剩余时间' : '已专注时间'}
          </p>
          <div className="mt-4 font-mono text-5xl font-bold leading-none tabular-nums text-foreground">{formatTimer(displaySeconds)}</div>
          <p className={cn('mt-4 text-sm', LEARNING_MUTED_TEXT_CLASS)}>
            {task ? `预设完成时间 ${task.estimated_minutes} 分钟` : '从下方选择任务后开始'}
          </p>
        </div>

        <div className="grid gap-3 lg:grid-cols-3">
          <Button disabled={!canStart} onClick={onToggle} size="lg" type="button">
            {isRunning ? <Pause /> : <Play />}
            {isRunning ? '暂停' : mode === 'countdown' && displaySeconds === 0 ? '重新开始' : '开始任务'}
          </Button>
          <Button
            className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
            disabled={!task}
            onClick={onReset}
            size="lg"
            type="button"
            variant="outline"
          >
            <RefreshCw />
            重置
          </Button>
          <Button
            className="bg-[var(--learn-primary)] text-white hover:bg-[#718a8b]"
            disabled={!canStart || completePending}
            onClick={onComplete}
            size="lg"
            type="button"
          >
            {completePending ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
            完成任务
          </Button>
        </div>
      </div>
    </section>
  )
}

function ModeButton({
  active,
  label,
  onClick
}: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      aria-pressed={active}
      className={cn(
        'min-h-10 flex-1 rounded-md px-4 text-sm font-semibold transition-colors',
        active ? 'bg-[var(--learn-card)] text-foreground shadow-sm' : cn(LEARNING_MUTED_TEXT_CLASS, 'hover:text-foreground')
      )}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  )
}

function TaskGuidancePanel({
  error,
  goalName,
  guidance,
  isError,
  isLoading,
  onRetry,
  task
}: {
  error: unknown
  goalName?: string
  guidance?: LearningTaskGuidance
  isError: boolean
  isLoading: boolean
  onRetry: () => void
  task: DailyTask | null
}) {
  return (
    <section className={cn(LEARNING_CARD_CLASS, 'min-h-[28rem] overflow-hidden')}>
      <div className="flex items-center justify-between gap-4 border-b border-[var(--learn-border)] px-5 py-4">
        <div className="flex items-center gap-3">
          <div className={cn('grid size-11 place-items-center rounded-lg', LEARNING_ICON_AMBER_CLASS)}>
            <Target className="size-6" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-foreground">当前任务</h2>
            <p className={cn('mt-1 text-sm', LEARNING_MUTED_TEXT_CLASS)}>任务细节与本次执行建议</p>
          </div>
        </div>
        {guidance && guidance.status !== 'generating' && (
          <Badge className="rounded-full bg-[var(--learn-primary-soft)] px-3 py-1 text-[var(--learn-primary)] hover:bg-[var(--learn-primary-soft)]">
            {guidance.status === 'failed'
              ? '生成失败'
              : guidance.generated_by === 'learning-skill' || guidance.generated_by === 'learning-skill-cache'
                ? '智能建议'
                : '智能生成'}
          </Badge>
        )}
      </div>

      {task ? (
        <div className="grid gap-5 p-5">
          <div>
            <h3 className="text-2xl font-bold text-foreground">{task.title}</h3>
            <div className={cn('mt-3 flex flex-wrap items-center gap-3 text-sm', LEARNING_MUTED_TEXT_CLASS)}>
              {goalName && (
                <span className="inline-flex items-center gap-1.5">
                  <BookOpen className="size-4" />
                  {goalName}
                </span>
              )}
              <span className="inline-flex items-center gap-1.5">
                <Clock className="size-4" />
                {task.estimated_minutes} 分钟
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Calendar className="size-4" />
                {formatDate(task.scheduled_date)}
              </span>
            </div>
            {task.description && <p className={cn('mt-4 text-base leading-7', LEARNING_MUTED_TEXT_CLASS)}>{task.description}</p>}
          </div>

          <div className="rounded-xl border border-[var(--learn-border)] bg-[var(--learn-soft)] p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 font-bold text-foreground">
                <Sparkles className="size-5 text-[var(--learn-amber)]" />
                细节建议
              </div>
              <Button
                className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-card)]"
                disabled={isLoading || guidance?.status === 'generating'}
                onClick={onRetry}
                size="sm"
                type="button"
                variant="outline"
              >
                <RefreshCw className={cn('size-4', (isLoading || guidance?.status === 'generating') && 'animate-spin')} />
                重新生成
              </Button>
            </div>
            <GuidanceContent error={error} guidance={guidance} isError={isError} isLoading={isLoading} onRetry={onRetry} />
          </div>
        </div>
      ) : (
        <EmptyState className="min-h-[24rem]" description="从下方选择今日任务，或从首页任务列表点击开始任务。" title="还没有选择任务" />
      )}
    </section>
  )
}

function GuidanceContent({
  error,
  guidance,
  isError,
  isLoading,
  onRetry
}: {
  error: unknown
  guidance?: LearningTaskGuidance
  isError: boolean
  isLoading: boolean
  onRetry: () => void
}) {
  if (isLoading || guidance?.status === 'generating') {
    return (
      <div className={cn('flex min-h-48 items-center justify-center gap-3 text-sm', LEARNING_MUTED_TEXT_CLASS)}>
        <Loader2 className="size-5 animate-spin" />
        智能体正在生成本次任务建议，完成后会自动更新
      </div>
    )
  }

  if (isError || !guidance || guidance.status === 'failed') {
    const message = guidance?.error || (error instanceof Error && error.message ? error.message : '模型建议生成失败，可以稍后再试。')

    return (
      <div className="grid min-h-48 place-items-center">
        <div className="text-center">
          <p className={cn('text-sm', LEARNING_MUTED_TEXT_CLASS)}>{message}</p>
          <Button className="mt-4" onClick={onRetry} size="sm" type="button">
            <RefreshCw />
            重试
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="grid gap-4">
      <p className={cn('text-base leading-7', LEARNING_MUTED_TEXT_CLASS)}>{guidance.summary}</p>
      <div className="grid gap-4 lg:grid-cols-2">
        <GuidanceSection items={guidance.steps} title="执行步骤" />
        <GuidanceSection items={guidance.focus_points} title="专注重点" />
        <GuidanceSection items={guidance.pitfalls} title="容易卡住" />
        <GuidanceSection items={guidance.checklist} title="完成检查" />
      </div>
    </div>
  )
}

function GuidanceSection({ items, title }: { items: string[]; title: string }) {
  return (
    <div className="rounded-lg border border-[var(--learn-border)] bg-[var(--learn-card)] p-4">
      <h4 className="text-sm font-bold text-foreground">{title}</h4>
      <ol className="mt-3 grid gap-2">
        {items.map((item, index) => (
          <li className={cn('grid grid-cols-[1.5rem_minmax(0,1fr)] gap-2 text-sm leading-6', LEARNING_MUTED_TEXT_CLASS)} key={item}>
            <span className="grid size-6 place-items-center rounded-full bg-[var(--learn-primary-soft)] text-xs font-bold text-[var(--learn-primary)]">
              {index + 1}
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

function TaskSelector({
  goalNames,
  onSelect,
  selectedTaskId,
  tasks
}: {
  goalNames: Record<string, string>
  onSelect: (taskId: string) => void
  selectedTaskId: string
  tasks: DailyTask[]
}) {
  return (
    <section className={cn(LEARNING_CARD_CLASS, 'overflow-hidden')}>
      <div className="border-b border-[var(--learn-border)] px-5 py-4">
        <h2 className="text-lg font-bold text-foreground">选择要进行的任务</h2>
        <p className={cn('mt-1 text-sm', LEARNING_MUTED_TEXT_CLASS)}>直接从侧边栏进入时，可以在这里选择今天要开始的任务。</p>
      </div>
      <div className="max-h-96 overflow-y-auto p-4">
        {tasks.length ? (
          <div className="grid gap-3">
            {tasks.map((task, index) => (
              <TaskOption
                goalName={goalNames[task.goal_id]}
                isSelected={task.id === selectedTaskId}
                key={task.id}
                number={index + 1}
                onClick={() => onSelect(task.id)}
                task={task}
              />
            ))}
          </div>
        ) : (
          <EmptyState className="min-h-56" description="学习目标页创建目标后，会自动生成今天的任务。" title="今天没有任务" />
        )}
      </div>
    </section>
  )
}

function TaskOption({
  goalName,
  isSelected,
  number,
  onClick,
  task
}: {
  goalName?: string
  isSelected: boolean
  number: number
  onClick: () => void
  task: DailyTask
}) {
  const done = task.status === 'completed'

  return (
    <button
      className={cn(
        'grid min-h-20 items-center gap-3 rounded-xl border border-[var(--learn-border)] bg-[var(--learn-card)] px-4 py-3 text-left transition-colors hover:bg-[var(--learn-soft)] md:grid-cols-[auto_minmax(0,1fr)_auto]',
        isSelected && 'border-[var(--learn-primary)] bg-[var(--learn-primary-soft)]',
        done && 'opacity-70'
      )}
      onClick={onClick}
      type="button"
    >
      <div className={cn('grid size-9 place-items-center rounded-lg text-sm font-bold', LEARNING_ICON_AMBER_CLASS)}>
        {number}
      </div>
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn('truncate font-bold text-foreground', done && 'line-through')}>{task.title}</span>
          {task.ahead && (
            <Badge className="shrink-0 bg-[var(--learn-amber-soft)] text-[var(--learn-amber)] hover:bg-[var(--learn-amber-soft)]">
              提前
            </Badge>
          )}
          {done ? <CheckCircle2 className="size-4 shrink-0 text-[var(--learn-green)]" /> : <CircleIcon className="size-3 shrink-0 text-muted-foreground" />}
        </div>
        <div className={cn('mt-1 flex flex-wrap items-center gap-3 text-sm', LEARNING_MUTED_TEXT_CLASS)}>
          {goalName && <span className="truncate">{goalName}</span>}
          <span>{formatDuration(task.estimated_minutes)}</span>
          <span>{formatDate(task.scheduled_date)}</span>
        </div>
      </div>
      <ChevronRight className={cn('hidden size-5 md:block', LEARNING_MUTED_TEXT_CLASS)} />
    </button>
  )
}
