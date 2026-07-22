import { useStore } from '@nanostores/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type * as React from 'react'
import { useCallback, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { getLearningProfile, type LearningProfile, saveLearningProfile } from '@/hermes'
import { ArrowRight, BookOpen, Brain, CheckCircle2, Clock, RefreshCw, Save, Target, Users } from '@/lib/icons'
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
import { DASHBOARD_ROUTE } from '../routes'

export const LEARNING_PROFILE_QUERY_ROOT = 'learning-profile'

export function learningProfileQueryKey(profile: string): readonly [typeof LEARNING_PROFILE_QUERY_ROOT, string] {
  return [LEARNING_PROFILE_QUERY_ROOT, profile || 'default']
}

const EMPTY_SELECT = '__empty__'
const REQUIRED_COUNT = 7

const EMPTY_PROFILE: LearningProfile = {
  nickname: '',
  school: '',
  major: '',
  grade: '',
  semester: '',
  career_target: '',
  baseline_level: '',
  weekday_minutes: null,
  weekend_minutes: null,
  preferred_study_time: '',
  learning_preferences: [],
  current_courses: [],
  strengths: '',
  weaknesses: '',
  constraints: '',
  updated_at: null,
  complete: false,
  missing_fields: [],
  completeness: 0
}

const REQUIRED_LABELS: Record<string, string> = {
  baseline_level: '当前基础',
  career_target: '目标去向',
  grade: '年级',
  learning_preferences: '学习偏好',
  major: '专业',
  weekday_minutes: '工作日学习时长',
  weekend_minutes: '休息日学习时长'
}

const GRADE_OPTIONS = ['大一', '大二', '大三', '大四', '研一', '研二', '研三']
const CAREER_TARGET_OPTIONS = ['就业', '实习', '考研', '保研', '考公', '留学', '竞赛', '转专业']
const LEVEL_OPTIONS = ['零基础', '入门', '初级', '中级', '高级']
const TIME_OPTIONS = ['早晨', '上午', '下午', '晚上', '碎片时间', '周末集中学习']
const PREFERENCE_OPTIONS = ['项目实战', '视频课程', '文档阅读', '刷题训练', 'AI 陪练', '同伴学习']

interface StudentProfileViewProps {
  enabled?: boolean
  mode?: 'edit' | 'gate'
  onContinue?: () => void
  onSaved?: (profile: LearningProfile) => void
}

function normalizeProfile(profile?: Partial<LearningProfile> | null): LearningProfile {
  return {
    ...EMPTY_PROFILE,
    ...(profile ?? {}),
    current_courses: Array.isArray(profile?.current_courses) ? profile.current_courses : [],
    learning_preferences: Array.isArray(profile?.learning_preferences) ? profile.learning_preferences : []
  }
}

function splitListInput(value: string): string[] {
  return value
    .split(/[,，、\n]/)
    .map(item => item.trim())
    .filter(Boolean)
}

function localMissingFields(profile: LearningProfile): string[] {
  const missing: string[] = []

  if (!profile.major.trim()) {
    missing.push('major')
  }

  if (!profile.grade.trim()) {
    missing.push('grade')
  }

  if (!profile.career_target.trim()) {
    missing.push('career_target')
  }

  if (!profile.baseline_level.trim()) {
    missing.push('baseline_level')
  }

  if (profile.weekday_minutes == null || !Number.isFinite(profile.weekday_minutes) || profile.weekday_minutes <= 0) {
    missing.push('weekday_minutes')
  }

  if (profile.weekend_minutes == null || !Number.isFinite(profile.weekend_minutes) || profile.weekend_minutes <= 0) {
    missing.push('weekend_minutes')
  }

  if (!profile.learning_preferences.length) {
    missing.push('learning_preferences')
  }

  return missing
}

function cleanProfile(profile: LearningProfile): LearningProfile {
  return {
    ...profile,
    baseline_level: profile.baseline_level.trim(),
    career_target: profile.career_target.trim(),
    constraints: profile.constraints.trim(),
    current_courses: profile.current_courses.map(item => item.trim()).filter(Boolean),
    grade: profile.grade.trim(),
    learning_preferences: profile.learning_preferences.map(item => item.trim()).filter(Boolean),
    major: profile.major.trim(),
    nickname: profile.nickname.trim(),
    preferred_study_time: profile.preferred_study_time.trim(),
    school: profile.school.trim(),
    semester: profile.semester.trim(),
    strengths: profile.strengths.trim(),
    weaknesses: profile.weaknesses.trim(),
    weekday_minutes:
      profile.weekday_minutes != null && Number.isFinite(profile.weekday_minutes) ? profile.weekday_minutes : null,
    weekend_minutes:
      profile.weekend_minutes != null && Number.isFinite(profile.weekend_minutes) ? profile.weekend_minutes : null
  }
}

function missingLabel(fields: string[]): string {
  return fields.map(field => REQUIRED_LABELS[field] ?? field).join('、')
}

function FieldLabel({ children, required = false }: { children: React.ReactNode; required?: boolean }) {
  return (
    <div className={cn('flex items-center justify-between gap-2 text-sm font-medium', LEARNING_MUTED_TEXT_CLASS)}>
      <span>{children}</span>
      {required && <span className="text-[var(--learn-red)]">必填</span>}
    </div>
  )
}

function TextField({
  label,
  onChange,
  placeholder,
  required,
  value
}: {
  label: string
  onChange: (value: string) => void
  placeholder?: string
  required?: boolean
  value: string
}) {
  return (
    <label className="grid gap-2">
      <FieldLabel required={required}>{label}</FieldLabel>
      <Input className="border-[var(--learn-border)] bg-[var(--learn-soft)]" onChange={event => onChange(event.target.value)} placeholder={placeholder} value={value} />
    </label>
  )
}

function NumberField({
  label,
  onChange,
  placeholder,
  required,
  value
}: {
  label: string
  onChange: (value: null | number) => void
  placeholder?: string
  required?: boolean
  value: null | number
}) {
  return (
    <label className="grid gap-2">
      <FieldLabel required={required}>{label}</FieldLabel>
      <Input
        className="border-[var(--learn-border)] bg-[var(--learn-soft)]"
        min={1}
        onChange={event => {
          const next = event.target.value.trim()

          onChange(next ? Number(next) : null)
        }}
        placeholder={placeholder}
        type="number"
        value={value ?? ''}
      />
    </label>
  )
}

function SelectField({
  label,
  onChange,
  options,
  required,
  value
}: {
  label: string
  onChange: (value: string) => void
  options: readonly string[]
  required?: boolean
  value: string
}) {
  return (
    <div className="grid gap-2">
      <FieldLabel required={required}>{label}</FieldLabel>
      <Select onValueChange={next => onChange(next === EMPTY_SELECT ? '' : next)} value={value || EMPTY_SELECT}>
        <SelectTrigger className="border-[var(--learn-border)] bg-[var(--learn-soft)]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={EMPTY_SELECT}>未选择</SelectItem>
          {options.map(option => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function TextareaField({
  label,
  onChange,
  placeholder,
  value
}: {
  label: string
  onChange: (value: string) => void
  placeholder?: string
  value: string
}) {
  return (
    <label className="grid gap-2">
      <FieldLabel>{label}</FieldLabel>
      <Textarea className="min-h-24 resize-y border-[var(--learn-border)] bg-[var(--learn-soft)]" onChange={event => onChange(event.target.value)} placeholder={placeholder} value={value} />
    </label>
  )
}

function FormSection({
  children,
  icon: Icon,
  title,
  tone = LEARNING_ICON_PRIMARY_CLASS
}: {
  children: React.ReactNode
  icon: React.ComponentType<{ className?: string }>
  title: string
  tone?: string
}) {
  return (
    <section className={LEARNING_CARD_CLASS}>
      <div className="flex items-center gap-3 border-b border-[var(--learn-border)] px-5 py-4">
        <div className={cn('flex size-10 items-center justify-center rounded-lg', tone)}>
          <Icon className="size-5" />
        </div>
        <h2 className="text-lg font-bold text-foreground">{title}</h2>
      </div>
      <div className="grid gap-5 p-5">{children}</div>
    </section>
  )
}

export function StudentProfileView({
  enabled = true,
  mode = 'edit',
  onContinue,
  onSaved
}: StudentProfileViewProps): React.ReactElement {
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const queryKey = useMemo(() => learningProfileQueryKey(activeGatewayProfile), [activeGatewayProfile])

  const profileQuery = useQuery({
    enabled,
    queryFn: getLearningProfile,
    queryKey,
    retry: 1,
    staleTime: 15_000
  })

  const handleSaved = useCallback(
    (profile: LearningProfile) => {
      queryClient.setQueryData(queryKey, profile)
      void queryClient.invalidateQueries({ queryKey: [LEARNING_PROFILE_QUERY_ROOT] })
      void queryClient.invalidateQueries({ queryKey: ['learning-dashboard'] })
      void queryClient.invalidateQueries({ queryKey: ['learning-overview'] })
      void queryClient.invalidateQueries({ queryKey: ['study-today-tasks'] })
      void queryClient.invalidateQueries({ queryKey: ['today-tasks-page'] })
      onSaved?.(profile)
    },
    [onSaved, queryClient, queryKey]
  )

  if (!enabled || profileQuery.isLoading) {
    return <PageLoader label={enabled ? '正在读取个人信息' : '等待桌面服务启动'} />
  }

  if (profileQuery.isError && !profileQuery.data) {
    return (
      <div className={cn(LEARNING_PAGE_CLASS, 'grid place-items-center px-6')}>
        <ErrorState description="请确认桌面后端已经启动，然后重试。" title="个人信息加载失败">
          <Button onClick={() => void profileQuery.refetch()} size="sm" variant="outline">
            <RefreshCw />
            重试
          </Button>
        </ErrorState>
      </div>
    )
  }

  return (
    <StudentProfileForm
      initialProfile={normalizeProfile(profileQuery.data)}
      key={`${activeGatewayProfile}:${profileQuery.data?.updated_at ?? 'new'}`}
      mode={mode}
      onContinue={onContinue ?? (() => navigate(DASHBOARD_ROUTE))}
      onSaved={handleSaved}
    />
  )
}

function StudentProfileForm({
  initialProfile,
  mode,
  onContinue,
  onSaved
}: {
  initialProfile: LearningProfile
  mode: 'edit' | 'gate'
  onContinue: () => void
  onSaved: (profile: LearningProfile) => void
}) {
  const [profile, setProfile] = useState(initialProfile)
  const saveMutation = useMutation({ mutationFn: saveLearningProfile })
  const missingFields = useMemo(() => localMissingFields(profile), [profile])
  const completeness = Math.round(Math.max(0, 1 - missingFields.length / REQUIRED_COUNT) * 100)
  const complete = missingFields.length === 0

  const updateField = useCallback(<K extends keyof LearningProfile,>(field: K, value: LearningProfile[K]) => {
    setProfile(prev => ({ ...prev, [field]: value }))
  }, [])

  const togglePreference = useCallback((option: string, checked: boolean) => {
    setProfile(prev => {
      const current = new Set(prev.learning_preferences)

      if (checked) {
        current.add(option)
      } else {
        current.delete(option)
      }

      return { ...prev, learning_preferences: Array.from(current) }
    })
  }, [])

  const handleSave = useCallback(
    async (goNext: boolean) => {
      try {
        const saved = normalizeProfile(await saveMutation.mutateAsync(cleanProfile(profile)))
        const savedMissing = saved.missing_fields?.length ? saved.missing_fields : localMissingFields(saved)

        setProfile(saved)
        onSaved(saved)

        if (savedMissing.length) {
          notify({
            kind: goNext ? 'warning' : 'info',
            message: `已保存，还差：${missingLabel(savedMissing)}`,
            title: '个人信息未完整'
          })

          return
        }

        notify({ kind: 'success', message: '个人信息已保存' })

        if (goNext) {
          onContinue()
        }
      } catch (error) {
        notifyError(error, '个人信息保存失败')
      }
    },
    [onContinue, onSaved, profile, saveMutation]
  )

  return (
    <div className={LEARNING_PAGE_CLASS}>
      <div className={LEARNING_CONTENT_CLASS}>
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-4xl font-bold tracking-normal text-foreground">个人信息</h1>
              <Badge
                className={cn(
                  complete ? 'bg-[var(--learn-green-soft)] text-[var(--learn-green)]' : 'bg-[var(--learn-amber-soft)] text-[var(--learn-amber)]',
                  'hover:bg-[var(--learn-green-soft)]'
                )}
              >
                {complete ? '已完成' : `${completeness}%`}
              </Badge>
            </div>
            <p className={cn('mt-2 max-w-3xl text-base leading-7', LEARNING_MUTED_TEXT_CLASS)}>
              {mode === 'gate'
                ? '先补齐学习背景，Hermes 才能按你的专业、基础和可用时间制定计划。'
                : '这里的信息会用于学习目标拆解、每日任务安排和智能体回答，之后可以随时修改。'}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-3">
            <Button
              className="border-[var(--learn-border)] bg-[var(--learn-card)] text-foreground hover:bg-[var(--learn-soft)]"
              disabled={saveMutation.isPending}
              onClick={() => void handleSave(false)}
              variant="outline"
            >
              <Save />
              保存
            </Button>
            <Button
              className="bg-[var(--learn-primary)] text-white hover:bg-[#718a8b]"
              disabled={saveMutation.isPending}
              onClick={() => void handleSave(true)}
            >
              {saveMutation.isPending ? <RefreshCw className="animate-spin" /> : <ArrowRight />}
              保存并进入首页
            </Button>
          </div>
        </header>

        <div className={cn(LEARNING_CARD_CLASS, 'px-5 py-4')}>
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="text-lg font-bold text-foreground">
                {complete ? '资料完整，可以使用智能体' : `还差：${missingLabel(missingFields)}`}
              </div>
              <div className={cn('mt-1 text-sm', LEARNING_MUTED_TEXT_CLASS)}>资料之后可以随时修改。</div>
            </div>
            <div className={cn('flex size-12 shrink-0 items-center justify-center rounded-lg', complete ? LEARNING_ICON_GREEN_CLASS : LEARNING_ICON_AMBER_CLASS)}>
              {complete ? <CheckCircle2 className="size-6" /> : <Target className="size-6" />}
            </div>
          </div>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-[#ebe5db]">
            <div className={cn('h-full transition-[width]', LEARNING_PROGRESS_CLASS)} style={{ width: `${completeness}%` }} />
          </div>
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,24rem)]">
          <div className="grid gap-6">
            <FormSection icon={Users} title="学校与专业">
              <div className="grid gap-5 md:grid-cols-2">
                <TextField
                  label="昵称"
                  onChange={value => updateField('nickname', value)}
                  placeholder="你希望 Hermes 怎么称呼你"
                  value={profile.nickname}
                />
                <TextField
                  label="学校"
                  onChange={value => updateField('school', value)}
                  placeholder="例如：某某大学"
                  value={profile.school}
                />
                <TextField
                  label="专业"
                  onChange={value => updateField('major', value)}
                  placeholder="例如：计算机科学与技术"
                  required
                  value={profile.major}
                />
                <SelectField label="年级" onChange={value => updateField('grade', value)} options={GRADE_OPTIONS} required value={profile.grade} />
                <TextField
                  label="学期"
                  onChange={value => updateField('semester', value)}
                  placeholder="例如：大三上"
                  value={profile.semester}
                />
                <SelectField
                  label="目标去向"
                  onChange={value => updateField('career_target', value)}
                  options={CAREER_TARGET_OPTIONS}
                  required
                  value={profile.career_target}
                />
              </div>
              <label className="grid gap-2">
                <FieldLabel>当前课程</FieldLabel>
                <Input
                  className="border-[var(--learn-border)] bg-[var(--learn-soft)]"
                  onChange={event => updateField('current_courses', splitListInput(event.target.value))}
                  placeholder="用逗号分隔，例如：数据结构、操作系统、概率论"
                  value={profile.current_courses.join('、')}
                />
              </label>
            </FormSection>

            <FormSection icon={Brain} title="基础与偏好" tone={LEARNING_ICON_PRIMARY_CLASS}>
              <div className="grid gap-5 md:grid-cols-2">
                <SelectField
                  label="当前基础"
                  onChange={value => updateField('baseline_level', value)}
                  options={LEVEL_OPTIONS}
                  required
                  value={profile.baseline_level}
                />
                <SelectField
                  label="偏好的学习时间"
                  onChange={value => updateField('preferred_study_time', value)}
                  options={TIME_OPTIONS}
                  value={profile.preferred_study_time}
                />
              </div>
              <div className="grid gap-3">
                <FieldLabel required>学习偏好</FieldLabel>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {PREFERENCE_OPTIONS.map(option => {
                    const checked = profile.learning_preferences.includes(option)

                    return (
                      <div
                        className={cn(
                          'flex min-h-11 items-center gap-3 rounded-lg border px-3 text-sm transition-colors',
                          checked
                            ? 'border-[var(--learn-primary)] bg-[var(--learn-primary-soft)] text-foreground'
                            : 'border-[var(--learn-border)] bg-[var(--learn-soft)] text-[var(--learn-muted)] hover:bg-[var(--learn-card)]'
                        )}
                        key={option}
                      >
                        <Checkbox
                          checked={checked}
                          className="border-[var(--learn-border)] data-[state=checked]:border-[var(--learn-primary)] data-[state=checked]:bg-[var(--learn-primary)]"
                          onCheckedChange={next => togglePreference(option, next === true)}
                        />
                        <span>{option}</span>
                      </div>
                    )
                  })}
                </div>
              </div>
              <div className="grid gap-5 md:grid-cols-2">
                <TextareaField
                  label="优势"
                  onChange={value => updateField('strengths', value)}
                  placeholder="例如：数学基础好、动手能力强、英语阅读可以"
                  value={profile.strengths}
                />
                <TextareaField
                  label="薄弱点"
                  onChange={value => updateField('weaknesses', value)}
                  placeholder="例如：算法基础弱、项目经验少、容易拖延"
                  value={profile.weaknesses}
                />
              </div>
            </FormSection>

            <FormSection icon={Clock} title="时间安排" tone={LEARNING_ICON_AMBER_CLASS}>
              <div className="grid gap-5 md:grid-cols-2">
                <NumberField
                  label="工作日学习时长（分钟/天）"
                  onChange={value => updateField('weekday_minutes', value)}
                  placeholder="例如：60"
                  required
                  value={profile.weekday_minutes}
                />
                <NumberField
                  label="休息日学习时长（分钟/天）"
                  onChange={value => updateField('weekend_minutes', value)}
                  placeholder="例如：180"
                  required
                  value={profile.weekend_minutes}
                />
              </div>
              <TextareaField
                label="限制与约束"
                onChange={value => updateField('constraints', value)}
                placeholder="例如：周三晚上有课、考试周减少任务、不能连续学习超过 90 分钟"
                value={profile.constraints}
              />
            </FormSection>
          </div>

          <aside className={cn(LEARNING_CARD_CLASS, 'grid h-fit gap-4 p-5')}>
            <div className="flex items-center gap-3 text-lg font-bold text-foreground">
              <div className={cn('flex size-10 items-center justify-center rounded-lg', LEARNING_ICON_GREEN_CLASS)}>
                <BookOpen className="size-5" />
              </div>
              学习画像
            </div>
            <div className={cn(LEARNING_PANEL_CLASS, 'grid gap-4 p-4')}>
              <ProfileFact label="专业" value={profile.major || '未填写'} />
              <ProfileFact label="年级" value={profile.grade || '未选择'} />
              <ProfileFact label="去向" value={profile.career_target || '未选择'} />
              <ProfileFact label="基础" value={profile.baseline_level || '未选择'} />
              <ProfileFact
                label="可用时间"
                value={
                  profile.weekday_minutes || profile.weekend_minutes
                    ? `工作日 ${profile.weekday_minutes ?? 0} 分钟，休息日 ${profile.weekend_minutes ?? 0} 分钟`
                    : '未填写'
                }
              />
              <ProfileFact label="偏好" value={profile.learning_preferences.length ? profile.learning_preferences.join('、') : '未选择'} />
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}

function ProfileFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 border-t border-[var(--learn-border)] pt-3 first:border-t-0 first:pt-0">
      <span className={cn('text-xs', LEARNING_MUTED_TEXT_CLASS)}>{label}</span>
      <span className="min-w-0 break-words text-sm font-medium text-foreground">{value}</span>
    </div>
  )
}
