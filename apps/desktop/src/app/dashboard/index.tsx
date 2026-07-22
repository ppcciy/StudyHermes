import { useStore } from '@nanostores/react'
import { useQuery } from '@tanstack/react-query'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import {
  type DailyTask,
  getLearningProfile,
  getLearningStats,
  getTodayTasks,
  type LearningProfile,
  type LearningStats,
  type TodayTasksResponse
} from '@/hermes'
import { CheckCircle2, CircleIcon, Clock, Flame, ListTodo, Play, RefreshCw, Target } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { $activeGatewayProfile } from '@/store/profile'

import {
  LEARNING_CARD_CLASS,
  LEARNING_CONTENT_CLASS,
  LEARNING_ICON_AMBER_CLASS,
  LEARNING_ICON_GREEN_CLASS,
  LEARNING_ICON_PRIMARY_CLASS,
  LEARNING_ICON_WARM_CLASS,
  LEARNING_MUTED_TEXT_CLASS,
  LEARNING_PAGE_CLASS
} from '../learning-theme'
import { useMidnightRefresh } from '../use-midnight-refresh'

interface DashboardData {
  profile: LearningProfile | null
  stats: LearningStats
  today: TodayTasksResponse
}

interface TimelineEntry {
  durationMinutes: number
  endMinutes: number
  overflow: boolean
  startMinutes: number
  task: DailyTask
}

const DAY_START_MINUTES = 8 * 60
const DAY_END_MINUTES = 22 * 60
const DAY_TOTAL_MINUTES = DAY_END_MINUTES - DAY_START_MINUTES
const HOUR_MARKS = Array.from({ length: (DAY_END_MINUTES - DAY_START_MINUTES) / 60 + 1 }, (_, index) => DAY_START_MINUTES + index * 60)
const TIMELINE_BREAK_MINUTES = 10
const TIMELINE_STORAGE_PREFIX = 'hermes:learning-dashboard-timeline'
const TIMELINE_SLOT_MINUTES = 15

function formatHours(value: null | number | undefined): string {
  return value == null ? '-' : `${value} 小时`
}

function localDateKey(date = new Date()): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')

  return `${year}-${month}-${day}`
}

function formatClock(minutes: number): string {
  const normalized = Math.max(0, minutes)
  const hours = Math.floor(normalized / 60)
  const rest = normalized % 60

  return `${String(hours).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

function parseClock(value: string | undefined): null | number {
  const match = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(value || '')

  if (!match) {
    return null
  }

  return Number(match[1]) * 60 + Number(match[2])
}

function minuteToPercent(minutes: number): number {
  return (Math.max(DAY_START_MINUTES, minutes) - DAY_START_MINUTES) / DAY_TOTAL_MINUTES * 100
}

function snapToSlot(minutes: number): number {
  return Math.round(minutes / TIMELINE_SLOT_MINUTES) * TIMELINE_SLOT_MINUTES
}

function taskDurationMinutes(task: DailyTask): number {
  const minutes = Number(task.estimated_minutes || 30)

  return Math.max(15, Math.min(DAY_TOTAL_MINUTES, Math.round(minutes)))
}

function clampStartForTask(minutes: number, task: DailyTask): number {
  const latestStart = Math.max(DAY_START_MINUTES, DAY_END_MINUTES - taskDurationMinutes(task))

  return Math.max(DAY_START_MINUTES, Math.min(latestStart, snapToSlot(minutes)))
}

function entriesOverlap(leftStart: number, leftDuration: number, rightStart: number, rightDuration: number): boolean {
  return leftStart < rightStart + rightDuration && leftStart + leftDuration > rightStart
}

function hasTimelineCollision(
  task: DailyTask,
  startMinutes: number,
  tasks: DailyTask[],
  starts: Record<string, number>,
  movingTaskId?: string
): boolean {
  const duration = taskDurationMinutes(task)

  return tasks.some(otherTask => {
    if (otherTask.id === task.id || otherTask.id === movingTaskId) {
      return false
    }

    const otherStart = starts[otherTask.id]

    if (typeof otherStart !== 'number' || !Number.isFinite(otherStart) || otherStart >= DAY_END_MINUTES) {
      return false
    }

    return entriesOverlap(startMinutes, duration, otherStart, taskDurationMinutes(otherTask))
  })
}

interface TimelineStartBounds {
  maxStart?: number
  minStart?: number
}

function nearestAvailableTimelineStart(
  task: DailyTask,
  desiredStart: number,
  tasks: DailyTask[],
  starts: Record<string, number>,
  movingTaskId?: string,
  bounds: TimelineStartBounds = {}
): null | number {
  const duration = taskDurationMinutes(task)
  const earliestStart = Math.max(DAY_START_MINUTES, bounds.minStart ?? DAY_START_MINUTES)
  const latestStart = Math.min(
    Math.max(DAY_START_MINUTES, DAY_END_MINUTES - duration),
    bounds.maxStart ?? DAY_END_MINUTES
  )

  if (earliestStart > latestStart) {
    return null
  }

  const clampedDesired = Math.max(earliestStart, Math.min(latestStart, snapToSlot(desiredStart)))

  if (!hasTimelineCollision(task, clampedDesired, tasks, starts, movingTaskId)) {
    return clampedDesired
  }

  const maxOffset = Math.max(clampedDesired - DAY_START_MINUTES, latestStart - clampedDesired)

  for (let offset = TIMELINE_SLOT_MINUTES; offset <= maxOffset + TIMELINE_SLOT_MINUTES; offset += TIMELINE_SLOT_MINUTES) {
    const candidates = [clampedDesired - offset, clampedDesired + offset]

    for (const candidate of candidates) {
      const snapped = clampStartForTask(candidate, task)

      if (snapped < earliestStart || snapped > latestStart) {
        continue
      }

      if (!hasTimelineCollision(task, snapped, tasks, starts, movingTaskId)) {
        return snapped
      }
    }
  }

  return null
}

function timelineSequenceBounds(
  task: DailyTask,
  tasks: DailyTask[],
  starts: Record<string, number>
): TimelineStartBounds {
  const index = tasks.findIndex(item => item.id === task.id)
  const previousTask = index > 0 ? tasks[index - 1] : null
  const nextTask = index >= 0 && index < tasks.length - 1 ? tasks[index + 1] : null
  const bounds: TimelineStartBounds = {}

  if (previousTask) {
    const previousStart = starts[previousTask.id]

    if (typeof previousStart === 'number' && Number.isFinite(previousStart) && previousStart < DAY_END_MINUTES) {
      bounds.minStart = previousStart + taskDurationMinutes(previousTask)
    }
  }

  if (nextTask) {
    const nextStart = starts[nextTask.id]

    if (typeof nextStart === 'number' && Number.isFinite(nextStart) && nextStart < DAY_END_MINUTES) {
      bounds.maxStart = nextStart - taskDurationMinutes(task)
    }
  }

  return bounds
}

function todayStorageKey(profile: null | string, date: string): string {
  return `${TIMELINE_STORAGE_PREFIX}:${profile || 'default'}:${date}`
}

function readStoredTimelineStarts(key: string): Record<string, number> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '{}')

    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      return {}
    }

    return Object.fromEntries(
      Object.entries(parsed).filter((entry): entry is [string, number] => typeof entry[0] === 'string' && typeof entry[1] === 'number' && Number.isFinite(entry[1]))
    )
  } catch {
    return {}
  }
}

function serializeStarts(starts: Record<string, number>): string {
  return JSON.stringify(Object.fromEntries(Object.entries(starts).sort(([a], [b]) => a.localeCompare(b))))
}

function writeStoredTimelineStarts(key: string, starts: Record<string, number>): void {
  try {
    window.localStorage.setItem(key, serializeStarts(starts))
  } catch {
    // Storage can be unavailable in restricted shells; the in-memory layout still works.
  }
}

function normalizeTimelineStarts(
  tasks: DailyTask[],
  rawStarts: Record<string, number>
): Record<string, number> {
  let defaultCursor = DAY_START_MINUTES
  const starts: Record<string, number> = {}

  for (const task of tasks) {
    const rawStart = rawStarts[task.id]
    const hasRawStart = typeof rawStart === 'number' && Number.isFinite(rawStart)
    const recommendedStart = parseClock(task.recommended_start_time)

    const desiredStart = hasRawStart
      ? clampStartForTask(rawStart, task)
      : recommendedStart == null
        ? defaultCursor
        : clampStartForTask(recommendedStart, task)
    const startMinutes = nearestAvailableTimelineStart(
      task,
      Math.max(desiredStart, defaultCursor),
      tasks,
      starts,
      task.id,
      { minStart: defaultCursor }
    ) ?? DAY_END_MINUTES

    starts[task.id] = startMinutes

    defaultCursor = Math.min(
      DAY_END_MINUTES,
      startMinutes + taskDurationMinutes(task) + TIMELINE_BREAK_MINUTES
    )
  }

  return starts
}

function buildTimelineEntries(tasks: DailyTask[], starts: Record<string, number>): TimelineEntry[] {
  return tasks
    .map(task => {
      const durationMinutes = taskDurationMinutes(task)
      const startMinutes = starts[task.id] ?? DAY_START_MINUTES
      const endMinutes = startMinutes + durationMinutes

      return {
        durationMinutes,
        endMinutes,
        overflow: startMinutes >= DAY_END_MINUTES || endMinutes > DAY_END_MINUTES,
        startMinutes,
        task
      }
    })
    .sort((left, right) => left.startMinutes - right.startMinutes || (left.task.priority ?? 3) - (right.task.priority ?? 3))
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
    <section className={cn(LEARNING_CARD_CLASS, 'p-3')}>
      <div className="flex items-center gap-3">
        <div className={cn('flex size-11 shrink-0 items-center justify-center rounded-lg', iconClass)}>
          <Icon className="size-5" />
        </div>
        <div className="min-w-0">
          <div className={cn('truncate text-sm', LEARNING_MUTED_TEXT_CLASS)}>{label}</div>
          <div className="mt-1 text-2xl font-bold text-foreground">{value}</div>
        </div>
      </div>
    </section>
  )
}

export function DashboardView(): React.ReactElement {
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const navigate = useNavigate()
  const [timelineStarts, setTimelineStarts] = useState<Record<string, number>>({})

  const dashboardQuery = useQuery({
    queryFn: async (): Promise<DashboardData> => {
      const [stats, today, profile] = await Promise.all([
        getLearningStats(),
        getTodayTasks(),
        getLearningProfile().catch(() => null)
      ])

      return { profile, stats, today }
    },
    queryKey: ['learning-dashboard', activeGatewayProfile],
    retry: 1,
    staleTime: 10_000
  })

  useMidnightRefresh(() => {
    void dashboardQuery.refetch()
  })

  const todayDate = dashboardQuery.data?.today.date ?? localDateKey()
  const todayTasks = useMemo(() => dashboardQuery.data?.today.tasks ?? [], [dashboardQuery.data?.today.tasks])
  const timetableStatus = dashboardQuery.data?.today.timetable_status
  const isTimetableGenerating = timetableStatus === 'model_generating'
  const isTimetableUnavailable = timetableStatus === 'model_unavailable' || isTimetableGenerating
  const timelineTasks = useMemo(() => (isTimetableUnavailable ? [] : todayTasks), [isTimetableUnavailable, todayTasks])

  const orderStorageKey = useMemo(
    () => todayStorageKey(activeGatewayProfile, todayDate),
    [activeGatewayProfile, todayDate]
  )

  useEffect(() => {
    if (isTimetableUnavailable) {
      setTimelineStarts({})

      return
    }

    const nextStarts = normalizeTimelineStarts(timelineTasks, readStoredTimelineStarts(orderStorageKey))

    setTimelineStarts(current => {
      const currentStarts = normalizeTimelineStarts(timelineTasks, current)

      if (serializeStarts(currentStarts) === serializeStarts(nextStarts)) {
        return currentStarts
      }

      return nextStarts
    })
    writeStoredTimelineStarts(orderStorageKey, nextStarts)
  }, [isTimetableUnavailable, orderStorageKey, timelineTasks])

  useEffect(() => {
    if (!isTimetableGenerating) {
      return undefined
    }

    const refreshTimer = window.setTimeout(() => {
      void dashboardQuery.refetch()
    }, 5000)

    return () => window.clearTimeout(refreshTimer)
  }, [dashboardQuery, isTimetableGenerating])

  const timelineEntries = useMemo(() => buildTimelineEntries(timelineTasks, timelineStarts), [timelineStarts, timelineTasks])

  const orderedTasks = todayTasks

  const taskNumberById = useMemo(
    () => new Map(orderedTasks.map((task, index) => [task.id, index + 1])),
    [orderedTasks]
  )

  const handleTimelineMove = useCallback(
    (taskId: string, startMinutes: number) => {
      if (isTimetableUnavailable) {
        return
      }

      const task = timelineTasks.find(item => item.id === taskId)

      if (!task) {
        return
      }

      const nextStart = nearestAvailableTimelineStart(
        task,
        startMinutes,
        timelineTasks,
        timelineStarts,
        taskId,
        timelineSequenceBounds(task, timelineTasks, timelineStarts)
      )

      if (nextStart == null) {
        return
      }

      const nextStarts = { ...timelineStarts, [taskId]: nextStart }

      setTimelineStarts(nextStarts)
      writeStoredTimelineStarts(orderStorageKey, nextStarts)
    },
    [isTimetableUnavailable, orderStorageKey, timelineStarts, timelineTasks]
  )

  if (dashboardQuery.isLoading) {
    return <PageLoader label="正在读取学习数据" />
  }

  if (dashboardQuery.isError && !dashboardQuery.data) {
    const message = dashboardQuery.error instanceof Error && dashboardQuery.error.message
      ? dashboardQuery.error.message
      : '智能排课生成失败，可以稍后重试。'

    return (
      <div className={LEARNING_PAGE_CLASS}>
        <div className={cn(LEARNING_CONTENT_CLASS, 'grid place-items-center')}>
          <div className="grid justify-items-center gap-4">
            <EmptyState
              description={message}
              title="智能排课生成失败"
            />
            <div>
              <Button onClick={() => void dashboardQuery.refetch()} type="button">
                <RefreshCw />
                重试
              </Button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  const stats = dashboardQuery.data?.stats
  const goalNames = dashboardQuery.data?.today.goal_names ?? {}
  const aheadTasks = dashboardQuery.data?.today.ahead_tasks ?? []
  const totalScheduledMinutes = timelineEntries.reduce((sum, entry) => sum + entry.durationMinutes, 0)
  const canStudyAhead = orderedTasks.length > 0 && orderedTasks.every(task => task.status === 'completed') && aheadTasks.length > 0

  return (
    <div className={cn(LEARNING_PAGE_CLASS, 'overflow-hidden')}>
      <div className={cn(LEARNING_CONTENT_CLASS, 'mr-auto flex h-full w-full max-w-none gap-4 overflow-hidden p-5 pr-6')}>
        <header className="flex shrink-0 flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-normal text-foreground">学习中心</h1>
            <p className={cn('mt-1 text-base', LEARNING_MUTED_TEXT_CLASS)}>今天要做什么，一屏看完。</p>
          </div>
          <Button
            className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
            onClick={() => void dashboardQuery.refetch()}
            type="button"
            variant="outline"
          >
            <RefreshCw />
            刷新
          </Button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_14rem] items-stretch gap-4">
          <main className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)] gap-4">
            <div className="grid shrink-0 gap-3 md:grid-cols-4">
              <Metric icon={Target} label="进行中的目标" tone="primary" value={`${stats?.active_goals ?? 0}`} />
              <Metric icon={ListTodo} label="今日任务" tone="amber" value={`${stats?.today_completed ?? 0}/${stats?.today_tasks ?? 0}`} />
              <Metric icon={Clock} label="累计学习" tone="warm" value={formatHours(stats?.total_hours_learned)} />
              <Metric icon={Flame} label="连续学习" tone="green" value={`${stats?.streak_days ?? 0} 天`} />
            </div>

            <section className={cn(LEARNING_CARD_CLASS, 'flex min-h-0 flex-col overflow-hidden')}>
              <div className="flex shrink-0 items-center justify-between border-b border-[var(--learn-border)] px-5 py-3">
                <div>
                  <h2 className="text-lg font-bold text-foreground">今日任务</h2>
                  <p className={cn('mt-1 text-sm', LEARNING_MUTED_TEXT_CLASS)}>编号由右侧时间线位置自动排序。</p>
                </div>
                <Button onClick={() => navigate('/tasks')} size="sm" type="button" variant="textStrong">
                  查看全部
                </Button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                {orderedTasks.length ? (
                  <>
                    {orderedTasks.map(task => (
                      <TodayTaskRow
                        goalName={goalNames[task.goal_id]}
                        key={task.id}
                        number={taskNumberById.get(task.id) ?? 0}
                        onStart={() => navigate(`/study?taskId=${encodeURIComponent(task.id)}`)}
                        task={task}
                      />
                    ))}
                    {canStudyAhead && (
                      <div className="border-b border-[var(--learn-border)] bg-[var(--learn-soft)] px-5 py-2 text-sm font-bold text-foreground">
                        可提前完成明日任务
                      </div>
                    )}
                    {canStudyAhead && aheadTasks.map((task, index) => (
                      <TodayTaskRow
                        goalName={goalNames[task.goal_id]}
                        key={`ahead-${task.id}`}
                        number={`明${index + 1}`}
                        onStart={() => navigate(`/study?taskId=${encodeURIComponent(task.id)}`)}
                        task={task}
                      />
                    ))}
                  </>
                ) : (
                  <EmptyState className="min-h-full" description="去学习目标页创建目标后，这里会出现今天的任务。" title="今天没有任务" />
                )}
              </div>
            </section>
          </main>

          <DailyTimeline
            date={todayDate}
            entries={timelineEntries}
            isGenerating={isTimetableGenerating}
            isUnavailable={isTimetableUnavailable && todayTasks.length > 0}
            onMove={handleTimelineMove}
            taskNumberById={taskNumberById}
            totalMinutes={totalScheduledMinutes}
          />
        </div>
      </div>
    </div>
  )
}

function TodayTaskRow({
  goalName,
  number,
  onStart,
  task
}: {
  goalName?: string
  number: number | string
  onStart: () => void
  task: DailyTask
}) {
  const done = task.status === 'completed'

  return (
    <article
      className={cn(
        'grid min-h-20 items-center gap-3 border-b border-[var(--learn-border)] px-5 py-3 last:border-b-0 md:grid-cols-[auto_auto_minmax(0,1fr)_auto]',
        done && 'bg-[var(--learn-primary-soft)] opacity-70'
      )}
    >
      <div className={cn('grid size-8 shrink-0 place-items-center rounded-lg text-sm font-bold', LEARNING_ICON_AMBER_CLASS)}>
        {number}
      </div>
      <span
        aria-label={done ? '已完成' : '待完成'}
        className={cn(
          'grid size-6 shrink-0 place-items-center rounded-md border',
          done
            ? 'border-[var(--learn-primary)] bg-[var(--learn-primary-soft)] text-[var(--learn-primary)]'
            : 'border-[var(--learn-border)] text-muted-foreground'
        )}
      >
        {done ? <CheckCircle2 className="size-4" /> : <CircleIcon className="size-3.5" />}
      </span>
      <div className="min-w-0">
        <h3 className={cn('truncate font-bold text-foreground', done && 'line-through')}>{task.title}</h3>
        {goalName && <p className={cn('mt-1 truncate text-sm', LEARNING_MUTED_TEXT_CLASS)}>{goalName}</p>}
      </div>
      <div className={cn('flex flex-wrap items-center gap-3 text-sm md:justify-end', LEARNING_MUTED_TEXT_CLASS)}>
        <span className="inline-flex items-center gap-1.5">
          <Clock className="size-4" />
          {task.estimated_minutes} 分钟
        </span>
        <Button
          className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
          disabled={done}
          onClick={onStart}
          size="sm"
          type="button"
          variant="outline"
        >
          <Play className="size-4" />
          开始任务
        </Button>
      </div>
    </article>
  )
}

function DailyTimeline({
  date,
  entries,
  isGenerating,
  isUnavailable,
  onMove,
  taskNumberById,
  totalMinutes
}: {
  date: string
  entries: TimelineEntry[]
  isGenerating: boolean
  isUnavailable: boolean
  onMove: (taskId: string, startMinutes: number) => void
  taskNumberById: Map<string, number>
  totalMinutes: number
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const visibleEntries = entries.filter(entry => entry.startMinutes < DAY_END_MINUTES)
  const overflowCount = entries.length - visibleEntries.length

  const handleDrag = useCallback(
    (task: DailyTask, clientY: number) => {
      const rect = trackRef.current?.getBoundingClientRect()

      if (!rect || rect.height <= 0) {
        return
      }

      const ratio = (clientY - rect.top) / rect.height
      const minutes = DAY_START_MINUTES + ratio * DAY_TOTAL_MINUTES

      onMove(task.id, clampStartForTask(minutes, task))
    },
    [onMove]
  )

  return (
    <aside className={cn(LEARNING_CARD_CLASS, 'grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] overflow-hidden')}>
      <div className="grid gap-1 border-b border-[var(--learn-border)] px-2 py-3 text-center">
        <div className="text-xs font-bold text-foreground">时间线</div>
        <div className={cn('rounded-md px-1.5 py-1 text-xs font-semibold', LEARNING_ICON_GREEN_CLASS)}>{date.slice(5)}</div>
        <div className={cn('text-[0.68rem] leading-4', LEARNING_MUTED_TEXT_CLASS)}>
          <div>{isUnavailable ? isGenerating ? '生成中' : '待排课' : `${entries.length}项`}</div>
          <div>{isUnavailable ? '智能排课' : `${Math.round(totalMinutes / 60 * 10) / 10}h`}</div>
        </div>
        {overflowCount > 0 && <div className="text-[0.68rem] font-semibold text-[var(--learn-red)]">+{overflowCount}</div>}
      </div>

      <div className="min-h-0 p-2">
        {isUnavailable ? (
          <div className="grid h-full place-items-center rounded-lg border border-dashed border-[var(--learn-border)] bg-[var(--learn-soft)] px-2 text-center text-[0.68rem] leading-4 text-[var(--learn-muted)]">
            <div>
              <div className="font-bold text-foreground">{isGenerating ? '智能排课中' : '待智能排课'}</div>
              <div className="mt-1">{isGenerating ? '完成后自动刷新' : '刷新重试'}</div>
            </div>
          </div>
        ) : entries.length ? (
          <div className="relative h-full min-h-0 overflow-hidden rounded-lg border border-[var(--learn-border)] bg-[var(--learn-soft)]">
            <div className="absolute bottom-4 left-2 right-2 top-4" ref={trackRef}>
              <div className="absolute bottom-0 left-[3.9rem] top-0 w-px bg-[var(--learn-border)]" />
              {HOUR_MARKS.map(mark => (
                <div
                  className="absolute left-0 right-0"
                  key={mark}
                  style={{ top: `${minuteToPercent(mark)}%` }}
                >
                  <span className="absolute left-0 top-0 w-[3.25rem] -translate-y-1/2 text-right text-[0.68rem] font-semibold leading-4 text-[var(--learn-muted)]">
                    {formatClock(mark)}
                  </span>
                  <span className="absolute left-[4.4rem] right-0 border-t border-dashed border-[var(--learn-border)]" />
                </div>
              ))}
              {visibleEntries.map(entry => (
                <TimelineTaskCard
                  entry={entry}
                  key={entry.task.id}
                  number={taskNumberById.get(entry.task.id) ?? 0}
                  onDrag={handleDrag}
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="grid h-full place-items-center text-center text-xs text-[var(--learn-muted)]">暂无</div>
        )}
      </div>
    </aside>
  )
}

function TimelineTaskCard({
  entry,
  number,
  onDrag
}: {
  entry: TimelineEntry
  number: number
  onDrag: (task: DailyTask, clientY: number) => void
}) {
  const [isDragging, setIsDragging] = useState(false)
  const heightPercent = Math.min(100, entry.durationMinutes / DAY_TOTAL_MINUTES * 100)
  const topPercent = minuteToPercent(entry.startMinutes)

  const handlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      if (event.button !== 0) {
        return
      }

      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      setIsDragging(true)
      onDrag(entry.task, event.clientY)
    },
    [entry.task, onDrag]
  )

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      if (!isDragging) {
        return
      }

      event.preventDefault()
      onDrag(entry.task, event.clientY)
    },
    [entry.task, isDragging, onDrag]
  )

  const handlePointerUp = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    setIsDragging(false)

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }, [])

  return (
    <button
      aria-label={`${number}. ${entry.task.title}，${formatClock(entry.startMinutes)}-${formatClock(Math.min(entry.endMinutes, DAY_END_MINUTES))}`}
      className={cn(
        'absolute grid cursor-ns-resize place-items-center rounded-lg border border-[var(--learn-border)] bg-[var(--learn-card)] text-sm font-bold shadow-sm transition-shadow',
        isDragging ? 'z-20 shadow-md' : 'z-10 hover:shadow-md'
      )}
      onPointerCancel={handlePointerUp}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      style={{
        height: `max(1.9rem, ${heightPercent}%)`,
        left: '4.8rem',
        right: '0.25rem',
        top: `${topPercent}%`
      }}
      title={`${number}. ${entry.task.title} ${formatClock(entry.startMinutes)}-${formatClock(Math.min(entry.endMinutes, DAY_END_MINUTES))}`}
      type="button"
    >
      {number}
    </button>
  )
}
