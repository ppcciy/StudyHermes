import type * as React from 'react'
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Calendar, Flame, ListTodo, Sparkles, Code2, Layers, MessageSquare, BookOpen, Target, TrendingUp, Clock, Check, Loader2, BookMarked } from '@/lib/icons'
import { getLearningStats, getTodayTasks, getLearningGoals, generateLearningPlan, updateTaskStatus, type LearningStats, type DailyTask, type LearningGoal, type GeneratePlanResponse } from '@/hermes'

export function DashboardView(): React.ReactElement {
  const navigate = useNavigate()
  const [stats, setStats] = useState<LearningStats | null>(null)
  const [todayTasks, setTodayTasks] = useState<DailyTask[]>([])
  const [goalNames, setGoalNames] = useState<Record<string, string>>({})
  const [activeGoals, setActiveGoals] = useState<LearningGoal[]>([])
  const [topic, setTopic] = useState('')
  const [isGenerating, setIsGenerating] = useState(false)
  const [generateResult, setGenerateResult] = useState<GeneratePlanResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [statsData, todayData, goalsData] = await Promise.all([
        getLearningStats(),
        getTodayTasks(),
        getLearningGoals()
      ])
      setStats(statsData)
      setTodayTasks(todayData.tasks)
      setGoalNames(todayData.goal_names)
      setActiveGoals(goalsData.filter(g => g.status === 'active'))
    } catch (error) {
      console.error('Failed to load learning data:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleGeneratePlan = async () => {
    if (!topic.trim()) return
    setIsGenerating(true)
    setGenerateResult(null)
    try {
      const result = await generateLearningPlan(topic)
      setGenerateResult(result)
      setTopic('')
      await loadData()
    } catch (error) {
      console.error('Failed to generate learning plan:', error)
    } finally {
      setIsGenerating(false)
    }
  }

  const handleTaskToggle = async (taskId: string, currentStatus: string) => {
    const newStatus = currentStatus === 'completed' ? 'pending' : 'completed'
    try {
      await updateTaskStatus(taskId, newStatus)
      await loadData()
    } catch (error) {
      console.error('Failed to update task status:', error)
    }
  }

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
        <p className="mt-4 text-muted-foreground">加载学习数据中...</p>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-col overflow-y-auto gap-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">学习助手</h1>
          <p className="text-muted-foreground mt-1">让 AI 帮你规划学习路径</p>
        </div>
        <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-amber-500/20 to-orange-500/20 rounded-full">
          <Flame className="w-5 h-5 text-amber-500" />
          <span className="text-sm font-medium">今日已学习 {stats?.total_hours_learned || 0} 小时</span>
        </div>
      </div>

      <div className="flex gap-4">
        <div className="flex-1 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-2xl p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-white/80 text-sm">学习规划</p>
              <h2 className="text-2xl font-bold mt-1">AI 驱动</h2>
              <p className="text-white/70 text-sm mt-2">输入你想学习的内容，AI 为你自动生成学习计划</p>
            </div>
            <Sparkles className="w-12 h-12 text-white/50" />
          </div>
          <div className="mt-4">
            <input
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="例如：学习 Python 编程、深度学习、React..."
              className="w-full px-4 py-3 rounded-xl bg-white/20 border border-white/30 text-white placeholder-white/60 focus:outline-none focus:bg-white/30 transition-colors"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleGeneratePlan()
                }
              }}
            />
            <button
              onClick={handleGeneratePlan}
              disabled={isGenerating || !topic.trim()}
              className="mt-3 w-full px-4 py-3 bg-white text-indigo-600 font-semibold rounded-xl hover:bg-white/90 transition-colors flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isGenerating ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  生成中...
                </>
              ) : (
                <>
                  开始规划 <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
            {generateResult && (
              <div className="mt-3 p-3 bg-green-500/30 rounded-xl border border-green-400/50">
                <div className="flex items-center gap-2">
                  <Check className="w-5 h-5 text-green-300" />
                  <span className="text-green-100">{generateResult.message}</span>
                </div>
                <p className="text-white/80 text-sm mt-1">
                  已创建 {generateResult.total_plans} 个学习计划，共 {generateResult.total_tasks} 个任务
                </p>
              </div>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 w-80">
          <div className="bg-card rounded-xl p-4 border">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-blue-100 rounded-lg">
                <Target className="w-5 h-5 text-blue-600" />
              </div>
              <div>
                <p className="text-muted-foreground text-xs">进行中的目标</p>
                <p className="text-xl font-bold">{stats?.active_goals || 0}</p>
              </div>
            </div>
          </div>

          <div className="bg-card rounded-xl p-4 border">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-green-100 rounded-lg">
                <ListTodo className="w-5 h-5 text-green-600" />
              </div>
              <div>
                <p className="text-muted-foreground text-xs">今日任务</p>
                <p className="text-xl font-bold">{stats?.today_completed || 0}/{stats?.today_tasks || 0}</p>
              </div>
            </div>
          </div>

          <div className="bg-card rounded-xl p-4 border">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-amber-100 rounded-lg">
                <TrendingUp className="w-5 h-5 text-amber-600" />
              </div>
              <div>
                <p className="text-muted-foreground text-xs">连续学习</p>
                <p className="text-xl font-bold">{stats?.streak_days || 0} 天</p>
              </div>
            </div>
          </div>

          <div className="bg-card rounded-xl p-4 border">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-purple-100 rounded-lg">
                <BookOpen className="w-5 h-5 text-purple-600" />
              </div>
              <div>
                <p className="text-muted-foreground text-xs">已完成课程</p>
                <p className="text-xl font-bold">{stats?.total_tasks_completed || 0}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-2 bg-card rounded-xl p-5 border">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold">今日任务</h3>
            <button onClick={() => navigate('/tasks')} className="text-sm text-primary hover:underline">
              查看全部
            </button>
          </div>
          <div className="space-y-3">
            {todayTasks.length > 0 ? (
              todayTasks.map((task) => (
                <div
                  key={task.id}
                  className={`flex items-center gap-3 p-3 rounded-lg ${task.status === 'completed' ? 'bg-green-50' : 'bg-muted/50'}`}
                >
                  <input
                    type="checkbox"
                    checked={task.status === 'completed'}
                    onChange={() => handleTaskToggle(task.id, task.status)}
                    className="w-4 h-4 rounded border-primary text-primary"
                  />
                  <div className="flex-1">
                    <span className={task.status === 'completed' ? 'line-through text-muted-foreground' : ''}>
                      {task.title}
                    </span>
                    {goalNames[task.goal_id] && (
                      <span className="ml-2 text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5">
                        {goalNames[task.goal_id]}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">{task.estimated_minutes}分钟</span>
                    <div className="flex items-center gap-1 text-sm text-muted-foreground">
                      <Clock className="w-3 h-3" />
                      {new Date(task.scheduled_date).toLocaleDateString()}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="text-center py-8 text-muted-foreground">
                <ListTodo className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>今日暂无任务</p>
                <button onClick={() => navigate('/learning')} className="mt-2 text-primary hover:underline">
                  创建学习目标
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="bg-card rounded-xl p-5 border">
          <h3 className="font-semibold mb-4">进行中的目标</h3>
          <div className="space-y-4">
            {activeGoals.length > 0 ? (
              activeGoals.map((goal) => (
                <div key={goal.id}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-medium">{goal.name}</span>
                    <span className="text-sm text-muted-foreground">{goal.progress}%</span>
                  </div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-blue-500 transition-all duration-300"
                      style={{ width: `${goal.progress}%` }}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    预计 {goal.estimated_hours} 小时 · {goal.goal_type}
                  </p>
                </div>
              ))
            ) : (
              <div className="text-center py-8 text-muted-foreground">
                <Target className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>暂无进行中的目标</p>
                <button onClick={() => navigate('/learning')} className="mt-2 text-primary hover:underline">
                  创建目标
                </button>
              </div>
            )}
          </div>
          <button
            onClick={() => navigate('/learning')}
            className="mt-4 w-full px-4 py-2 border rounded-lg text-sm font-medium hover:bg-muted/50 transition-colors"
          >
            管理目标
          </button>
        </div>
      </div>

      <div className="bg-card rounded-xl p-5 border">
        <h3 className="font-semibold mb-4">快捷入口</h3>
        <div className="grid grid-cols-6 gap-4">
          {[
            { icon: <Code2 className="w-6 h-6" />, label: 'AI 问答', color: 'bg-blue-100 text-blue-600', route: '/chat' },
            { icon: <Target className="w-6 h-6" />, label: '学习目标', color: 'bg-green-100 text-green-600', route: '/learning' },
            { icon: <ListTodo className="w-6 h-6" />, label: '任务管理', color: 'bg-amber-100 text-amber-600', route: '/tasks' },
            { icon: <Clock className="w-6 h-6" />, label: '专注学习', color: 'bg-purple-100 text-purple-600', route: '/study' },
            { icon: <Layers className="w-6 h-6" />, label: '学习计划', color: 'bg-pink-100 text-pink-600', route: '/planning' },
            { icon: <BookMarked className="w-6 h-6" />, label: '错题本', color: 'bg-red-100 text-red-600', route: '/mistakes' },
          ].map((item, index) => (
            <button
              key={index}
              onClick={() => navigate(item.route)}
              className={`flex flex-col items-center gap-2 p-4 rounded-xl ${item.color} hover:opacity-80 transition-opacity`}
            >
              {item.icon}
              <span className="text-sm font-medium">{item.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="bg-gradient-to-r from-amber-50 to-orange-50 rounded-xl p-5 border border-amber-200">
        <div className="flex items-start gap-4">
          <div className="p-2 bg-amber-100 rounded-lg">
            <Sparkles className="w-5 h-5 text-amber-600" />
          </div>
          <div>
            <h4 className="font-semibold text-amber-800">学习小贴士</h4>
            <p className="text-sm text-amber-700 mt-1">
              科学研究表明，每天保持专注学习 45-60 分钟，比长时间连续学习效果更好。记得每学习一段时间就休息一下！
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
