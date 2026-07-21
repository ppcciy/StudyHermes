import type * as React from 'react'
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Trash2, Edit2, Target, BookOpen, Calendar, Clock, Check } from '@/lib/icons'
import { getLearningGoals, generateLearningPlan, type LearningGoal, type GeneratePlanResponse } from '@/hermes'

export function LearningView(): React.ReactElement {
  const navigate = useNavigate()
  const [goals, setGoals] = useState<LearningGoal[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [topic, setTopic] = useState('')
  const [isGenerating, setIsGenerating] = useState(false)

  useEffect(() => {
    loadGoals()
  }, [])

  const loadGoals = async () => {
    setIsLoading(true)
    try {
      const data = await getLearningGoals()
      setGoals(data)
    } catch (error) {
      console.error('Failed to load goals:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleGeneratePlan = async () => {
    if (!topic.trim()) return
    setIsGenerating(true)
    try {
      await generateLearningPlan(topic)
      setTopic('')
      await loadGoals()
    } catch (error) {
      console.error('Failed to generate plan:', error)
    } finally {
      setIsGenerating(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin" />
        <p className="mt-4 text-muted-foreground">加载中...</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">学习目标</h1>
          <p className="text-muted-foreground mt-1">管理你的学习目标和进度</p>
        </div>
      </div>

      <div className="bg-gradient-to-br from-indigo-500 to-purple-600 rounded-2xl p-6 text-white">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-white/80 text-sm">AI 生成学习计划</p>
            <h2 className="text-xl font-bold mt-1">输入你想学习的内容</h2>
          </div>
          <Target className="w-12 h-12 text-white/50" />
        </div>
        <div className="mt-4 flex gap-3">
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="例如：Python、React、机器学习..."
            className="flex-1 px-4 py-3 rounded-xl bg-white/20 border border-white/30 text-white placeholder-white/60 focus:outline-none focus:bg-white/30 transition-colors"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleGeneratePlan()
              }
            }}
          />
          <button
            onClick={handleGeneratePlan}
            disabled={isGenerating || !topic.trim()}
            className="px-6 py-3 bg-white text-indigo-600 font-semibold rounded-xl hover:bg-white/90 transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isGenerating ? (
              <>
                <div className="w-4 h-4 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin" />
                生成中
              </>
            ) : (
              <>
                <Plus className="w-4 h-4" />
                生成计划
              </>
            )}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <Target className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-muted-foreground text-xs">总目标</p>
              <p className="text-xl font-bold">{goals.length}</p>
            </div>
          </div>
        </div>
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-green-100 rounded-lg">
              <Check className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-muted-foreground text-xs">已完成</p>
              <p className="text-xl font-bold">{goals.filter(g => g.status === 'completed').length}</p>
            </div>
          </div>
        </div>
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-amber-100 rounded-lg">
              <Clock className="w-5 h-5 text-amber-600" />
            </div>
            <div>
              <p className="text-muted-foreground text-xs">进行中</p>
              <p className="text-xl font-bold">{goals.filter(g => g.status === 'active').length}</p>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-card rounded-xl border">
        <div className="p-4 border-b">
          <h3 className="font-semibold">我的学习目标</h3>
        </div>
        <div className="divide-y">
          {goals.length > 0 ? (
            goals.map((goal) => (
              <div key={goal.id} className="p-4 hover:bg-muted/50 transition-colors">
                <div className="flex items-start gap-4">
                  <div className={`p-3 rounded-xl ${goal.status === 'completed' ? 'bg-green-100' : 'bg-blue-100'}`}>
                    {goal.status === 'completed' ? (
                      <Check className="w-6 h-6 text-green-600" />
                    ) : (
                      <Target className="w-6 h-6 text-blue-600" />
                    )}
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="font-medium">{goal.name}</h4>
                      <span className={`px-2 py-0.5 text-xs rounded-full ${
                        goal.status === 'completed' ? 'bg-green-100 text-green-700' : 
                        goal.status === 'active' ? 'bg-blue-100 text-blue-700' :
                        'bg-gray-100 text-gray-700'
                      }`}>
                        {goal.status === 'completed' ? '已完成' : goal.status === 'active' ? '进行中' : '暂停'}
                      </span>
                    </div>
                    <p className="text-sm text-muted-foreground mt-1">{goal.description}</p>
                    <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {goal.estimated_hours} 小时
                      </span>
                      <span className="flex items-center gap-1">
                        <Calendar className="w-3 h-3" />
                        {goal.created_at ? new Date(goal.created_at).toLocaleDateString() : ''}
                      </span>
                      <span className="flex items-center gap-1">
                        <BookOpen className="w-3 h-3" />
                        {goal.goal_type}
                      </span>
                    </div>
                    <div className="mt-3">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs text-muted-foreground">进度</span>
                        <span className="text-xs font-medium">{goal.progress}%</span>
                      </div>
                      <div className="h-2 bg-muted rounded-full overflow-hidden">
                        <div
                          className={`h-full transition-all duration-300 ${
                            goal.progress >= 100 ? 'bg-green-500' : 'bg-blue-500'
                          }`}
                          style={{ width: `${goal.progress}%` }}
                        />
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button className="p-2 hover:bg-muted rounded-lg transition-colors">
                      <Edit2 className="w-4 h-4 text-muted-foreground" />
                    </button>
                    <button className="p-2 hover:bg-red-50 rounded-lg transition-colors">
                      <Trash2 className="w-4 h-4 text-red-500" />
                    </button>
                  </div>
                </div>
              </div>
            ))
          ) : (
            <div className="p-8 text-center text-muted-foreground">
              <Target className="w-16 h-16 mx-auto mb-4 opacity-50" />
              <p className="text-lg">暂无学习目标</p>
              <p className="text-sm mt-2">输入上方的学习主题，AI 将为你生成学习计划</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
