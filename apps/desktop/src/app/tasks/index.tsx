import { useStore } from '@nanostores/react'
import { useQuery } from '@tanstack/react-query'
import type * as React from 'react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { type DailyTask, getTodayTasks } from '@/hermes'
import { CheckCircle2, CircleIcon, Clock, Filter, ListTodo, Play, RefreshCw } from '@/lib/icons'
import { cn } from '@/lib/utils'
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

type TaskFilter = 'all' | 'completed' | 'pending'

const FILTERS: { key: TaskFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待完成' },
  { key: 'completed', label: '已完成' }
]

function Metric({
  icon: Icon,
  label,
  tone,
  value
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  tone: 'blue' | 'green' | 'amber'
  value: string
}) {
  const iconClass =
    tone === 'green' ? LEARNING_ICON_GREEN_CLASS : tone === 'amber' ? LEARNING_ICON_AMBER_CLASS : LEARNING_ICON_PRIMARY_CLASS

  return (
    <section className={cn(LEARNING_CARD_CLASS, 'p-5')}>
      <div className="flex items-center gap-5">
        <div className={cn('flex size-16 shrink-0 items-center justify-center rounded-lg', iconClass)}>
          <Icon className="size-8" />
        </div>
        <div>
          <div className="text-base text-muted-foreground">{label}</div>
          <div className="mt-2 text-3xl font-bold text-foreground">{value}</div>
        </div>
      </div>
    </section>
  )
}

export function TasksView(): React.ReactElement {
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const navigate = useNavigate()
  const [filter, setFilter] = useState<TaskFilter>('all')

  const tasksQuery = useQuery({
    queryFn: getTodayTasks,
    queryKey: ['today-tasks-page', activeGatewayProfile],
    retry: 1,
    staleTime: 10_000
  })

  useMidnightRefresh(() => {
    void tasksQuery.refetch()
  })

  const tasks = useMemo(() => tasksQuery.data?.tasks ?? [], [tasksQuery.data?.tasks])
  const goalNames = useMemo(() => tasksQuery.data?.goal_names ?? {}, [tasksQuery.data?.goal_names])
  const pendingCount = useMemo(() => tasks.filter(task => task.status === 'pending').length, [tasks])
  const completedCount = useMemo(() => tasks.filter(task => task.status === 'completed').length, [tasks])
  const filteredTasks = useMemo(() => tasks.filter(task => filter === 'all' || task.status === filter), [filter, tasks])

  if (tasksQuery.isLoading) {
    return <PageLoader label="正在读取任务" />
  }

  return (
    <div className={LEARNING_PAGE_CLASS}>
      <div className={cn(LEARNING_CONTENT_CLASS, 'gap-8')}>
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-4xl font-bold tracking-normal text-foreground">任务管理</h1>
            <p className={cn('mt-2 text-base', LEARNING_MUTED_TEXT_CLASS)}>管理和追踪你的学习任务</p>
          </div>
          <Button
            className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
            onClick={() => void tasksQuery.refetch()}
            variant="outline"
          >
            <RefreshCw />
            刷新
          </Button>
        </header>

        <div className="grid gap-5 md:grid-cols-3">
          <Metric icon={ListTodo} label="总任务" tone="blue" value={`${tasks.length}`} />
          <Metric icon={Clock} label="待完成" tone="amber" value={`${pendingCount}`} />
          <Metric icon={CheckCircle2} label="已完成" tone="green" value={`${completedCount}`} />
        </div>

        <section className="grid gap-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <h2 className="text-xl font-bold text-foreground">任务列表</h2>
            <div className="flex items-center gap-3">
              <Filter className={cn('size-5', LEARNING_MUTED_TEXT_CLASS)} />
              <div className="flex items-center gap-1 rounded-lg bg-[var(--learn-primary-soft)] p-1">
                {FILTERS.map(item => (
                  <button
                    className={cn(
                      'min-h-10 rounded-md px-5 text-base font-medium transition-colors',
                      filter === item.key ? 'bg-[var(--learn-card)] text-foreground shadow-sm' : cn(LEARNING_MUTED_TEXT_CLASS, 'hover:text-foreground')
                    )}
                    key={item.key}
                    onClick={() => setFilter(item.key)}
                    type="button"
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className={cn(LEARNING_CARD_CLASS, 'overflow-hidden')}>
            {filteredTasks.length ? (
              filteredTasks.map(task => (
                <TaskRow
                  goalName={goalNames[task.goal_id]}
                  key={task.id}
                  onStart={() => navigate(`/study?taskId=${encodeURIComponent(task.id)}`)}
                  task={task}
                />
              ))
            ) : (
              <EmptyState className="min-h-72" description="创建学习目标后会自动生成任务。" title="暂无任务" />
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

function TaskRow({ goalName, onStart, task }: { goalName?: string; onStart: () => void; task: DailyTask }) {
  const done = task.status === 'completed'

  return (
    <article
      className={cn(
        'grid min-h-24 items-center gap-4 border-b border-[var(--learn-border)] px-7 py-5 last:border-b-0 md:grid-cols-[auto_minmax(0,1fr)_auto]',
        done && 'bg-[var(--learn-primary-soft)] opacity-70'
      )}
    >
      <span
        aria-label={done ? '已完成' : '待完成'}
        className={cn(
          'grid size-7 shrink-0 place-items-center rounded-md border',
          done
            ? 'border-[var(--learn-primary)] bg-[var(--learn-primary-soft)] text-[var(--learn-primary)]'
            : 'border-[var(--learn-border)] text-muted-foreground'
        )}
      >
        {done ? <CheckCircle2 className="size-5" /> : <CircleIcon className="size-4" />}
      </span>
      <div className="min-w-0">
        <h3 className={cn('text-lg font-bold text-foreground', done && 'line-through')}>{task.title}</h3>
        {task.description && <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted-foreground">{task.description}</p>}
      </div>
      <div className={cn('flex flex-wrap items-center gap-4 text-base md:justify-end', LEARNING_MUTED_TEXT_CLASS)}>
        {goalName && (
          <Badge className={cn('rounded-full bg-[var(--learn-primary-soft)] px-3 py-1 text-base font-normal hover:bg-[var(--learn-primary-soft)]', LEARNING_MUTED_TEXT_CLASS)}>
            {goalName}
          </Badge>
        )}
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
