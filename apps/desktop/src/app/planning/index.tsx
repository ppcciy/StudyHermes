import { useStore } from '@nanostores/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type * as React from 'react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { deleteLearningGoal, getLearningGoals, getLearningPlans, type LearningGoal, type LearningPlan } from '@/hermes'
import { ArrowRight, CheckCircle2, Clock, Layers, RefreshCw, Target, Trash2 } from '@/lib/icons'
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
  LEARNING_PAGE_CLASS,
  LEARNING_PANEL_CLASS,
  LEARNING_PROGRESS_CLASS
} from '../learning-theme'

interface PlanningData {
  goals: LearningGoal[]
  plans: LearningPlan[]
}

function completion(completed: number, total: number): number {
  if (total <= 0) {
    return 0
  }

  return Math.round((completed / total) * 100)
}

function formatHours(value: null | number | undefined): string {
  return value == null ? '-' : `${value} 小时`
}

function groupPlansByGoal(plans: LearningPlan[]): Map<string, LearningPlan[]> {
  const grouped = new Map<string, LearningPlan[]>()

  plans.forEach(plan => {
    const current = grouped.get(plan.goal_id) ?? []

    current.push(plan)
    grouped.set(plan.goal_id, current)
  })

  grouped.forEach(items => {
    items.sort((a, b) => a.created_at.localeCompare(b.created_at))
  })

  return grouped
}

export function PlanningView(): React.ReactElement {
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [pendingDeleteGoal, setPendingDeleteGoal] = useState<LearningGoal | null>(null)

  const planningQuery = useQuery({
    queryFn: async (): Promise<PlanningData> => {
      const [goals, plans] = await Promise.all([getLearningGoals(), getLearningPlans()])

      return { goals, plans }
    },
    queryKey: ['learning-planning', activeGatewayProfile],
    retry: 1,
    staleTime: 10_000
  })

  const deleteGoalMutation = useMutation({
    mutationFn: deleteLearningGoal,
    onError: error => notifyError(error, '学习计划删除失败'),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['learning-planning'] })
      void queryClient.invalidateQueries({ queryKey: ['learning-dashboard'] })
      void queryClient.invalidateQueries({ queryKey: ['learning-overview'] })
      void queryClient.invalidateQueries({ queryKey: ['today-tasks-page'] })
      notify({ kind: 'success', message: '学习计划已删除' })
    }
  })

  const activeGoals = useMemo(
    () => planningQuery.data?.goals.filter(goal => goal.status !== 'completed') ?? [],
    [planningQuery.data?.goals]
  )

  const plansByGoal = useMemo(() => groupPlansByGoal(planningQuery.data?.plans ?? []), [planningQuery.data?.plans])
  const totalPlans = planningQuery.data?.plans.length ?? 0

  const completedPlans = useMemo(
    () => planningQuery.data?.plans.filter(plan => plan.total_tasks > 0 && plan.completed_tasks >= plan.total_tasks).length ?? 0,
    [planningQuery.data?.plans]
  )

  if (planningQuery.isLoading) {
    return <PageLoader label="正在读取学习计划" />
  }

  return (
    <div className={LEARNING_PAGE_CLASS}>
      <div className={LEARNING_CONTENT_CLASS}>
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-4xl font-bold tracking-normal text-foreground">学习计划</h1>
              <Badge className="bg-[var(--learn-primary-soft)] text-[var(--learn-primary)] hover:bg-[var(--learn-primary-soft)]">
                阶段视图
              </Badge>
            </div>
            <p className={cn('mt-2 max-w-3xl text-base leading-7', LEARNING_MUTED_TEXT_CLASS)}>
              这里集中查看已生成的阶段计划；新建和澄清目标统一放在学习目标页。
            </p>
          </div>
          <Button
            className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
            onClick={() => void planningQuery.refetch()}
            variant="outline"
          >
            <RefreshCw />
            刷新
          </Button>
        </header>

        <div className="grid gap-5 md:grid-cols-3">
          <SummaryCard icon={Target} label="进行中目标" tone={LEARNING_ICON_PRIMARY_CLASS} value={`${activeGoals.length}`} />
          <SummaryCard icon={Layers} label="阶段计划" tone={LEARNING_ICON_AMBER_CLASS} value={`${totalPlans}`} />
          <SummaryCard icon={CheckCircle2} label="已完成阶段" tone={LEARNING_ICON_GREEN_CLASS} value={`${completedPlans}`} />
        </div>

        <main className="grid gap-6">
            {activeGoals.length ? (
              activeGoals.map(goal => (
                <GoalPlanBlock
                  deleting={deleteGoalMutation.isPending && pendingDeleteGoal?.id === goal.id}
                  goal={goal}
                  key={goal.id}
                  onDelete={() => setPendingDeleteGoal(goal)}
                  plans={plansByGoal.get(goal.id) ?? []}
                />
              ))
            ) : (
              <section className={cn(LEARNING_CARD_CLASS, 'p-6')}>
                <EmptyState description="先去学习目标页完成一次需求获取，计划会自动出现在这里。" title="暂无学习计划" />
                <div className="mt-4 flex justify-center">
                  <Button onClick={() => navigate('/learning')}>
                    <ArrowRight />
                    去创建学习目标
                  </Button>
                </div>
              </section>
            )}
        </main>

        <ConfirmDialog
          busyLabel="删除中"
          confirmLabel="删除"
          description={
            pendingDeleteGoal
              ? `会删除「${pendingDeleteGoal.name}」以及它下面的所有阶段和任务。此操作不可撤销。`
              : undefined
          }
          destructive
          onClose={() => setPendingDeleteGoal(null)}
          onConfirm={async () => {
            if (!pendingDeleteGoal) {
              return
            }

            await deleteGoalMutation.mutateAsync(pendingDeleteGoal.id)
          }}
          open={Boolean(pendingDeleteGoal)}
          title="删除学习计划"
        />
      </div>
    </div>
  )
}

function SummaryCard({
  icon: Icon,
  label,
  tone,
  value
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  tone: string
  value: string
}) {
  return (
    <section className={cn(LEARNING_CARD_CLASS, 'p-5')}>
      <div className="flex items-center gap-5">
        <div className={cn('flex size-14 shrink-0 items-center justify-center rounded-lg', tone)}>
          <Icon className="size-7" />
        </div>
        <div>
          <div className={cn('text-base', LEARNING_MUTED_TEXT_CLASS)}>{label}</div>
          <div className="mt-2 text-3xl font-bold text-foreground">{value}</div>
        </div>
      </div>
    </section>
  )
}

function GoalPlanBlock({
  deleting,
  goal,
  onDelete,
  plans
}: {
  deleting: boolean
  goal: LearningGoal
  onDelete: () => void
  plans: LearningPlan[]
}) {
  return (
    <section className={LEARNING_CARD_CLASS}>
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--learn-border)] px-5 py-4">
        <div className="min-w-0">
          <h2 className="line-clamp-2 text-lg font-bold text-foreground">{goal.name}</h2>
          <div className={cn('mt-2 flex flex-wrap items-center gap-4 text-sm', LEARNING_MUTED_TEXT_CLASS)}>
            <span className="inline-flex items-center gap-1.5">
              <Clock className="size-4" />
              {formatHours(goal.estimated_hours)}
            </span>
            <span>{goal.goal_type}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <Badge className="bg-[var(--learn-primary-soft)] text-[var(--learn-primary)] hover:bg-[var(--learn-primary-soft)]">
            {goal.progress}%
          </Badge>
          <Button
            className="border-[var(--learn-red-soft)] bg-[var(--learn-card)] text-[var(--learn-red)] hover:bg-[var(--learn-red-soft)]"
            disabled={deleting}
            onClick={onDelete}
            size="sm"
            variant="outline"
          >
            <Trash2 />
            删除整套计划
          </Button>
        </div>
      </div>

      <div className="grid gap-4 p-5">
        <div className="h-2 overflow-hidden rounded-full bg-[#ebe5db]">
          <div className={cn('h-full', LEARNING_PROGRESS_CLASS)} style={{ width: `${Math.max(0, Math.min(100, goal.progress))}%` }} />
        </div>

        {plans.length ? (
          <div className="grid gap-3">
            {plans.map((plan, index) => {
              const percent = completion(plan.completed_tasks, plan.total_tasks)

              return (
                <div className={cn(LEARNING_PANEL_CLASS, 'grid gap-4 p-4 md:grid-cols-[auto_minmax(0,1fr)_auto] md:items-center')} key={plan.id}>
                  <div className={cn('grid size-10 place-items-center rounded-lg text-sm font-bold', LEARNING_ICON_AMBER_CLASS)}>
                    {index + 1}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate font-semibold text-foreground">{plan.name}</div>
                    <p className={cn('mt-1 line-clamp-2 text-sm leading-6', LEARNING_MUTED_TEXT_CLASS)}>{plan.description}</p>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#ebe5db]">
                      <div className={cn('h-full', LEARNING_PROGRESS_CLASS)} style={{ width: `${percent}%` }} />
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-lg font-bold text-foreground">{percent}%</div>
                    <div className={cn('text-xs', LEARNING_MUTED_TEXT_CLASS)}>
                      {plan.completed_tasks}/{plan.total_tasks} 任务
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <EmptyState className="min-h-32" description="目标生成后，阶段计划会展示在这里。" title="暂无阶段计划" />
        )}
      </div>
    </section>
  )
}
