import type * as React from 'react'
import { useState, useEffect } from 'react'
import { Calendar, Target, TrendingUp, Clock, Sparkles, ChevronRight, Check, Plus, ArrowRight } from '@/lib/icons'
import { getLearningGoals, generateLearningPlan, type LearningGoal, type GeneratePlanResponse } from '@/hermes'

export function PlanningView(): React.ReactElement {
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

  const recommendedTopics = [
    { title: 'Python 编程', icon: 'Code2', color: 'blue' },
    { title: 'React 开发', icon: 'Layers', color: 'cyan' },
    { title: '数据分析', icon: 'BarChart3', color: 'purple' },
    { title: '机器学习', icon: 'Brain', color: 'green' },
    { title: 'JavaScript', icon: 'Code', color: 'yellow' },
    { title: '深度学习', icon: 'NeuralNetwork', color: 'pink' },
  ]

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
          <h1 className="text-3xl font-bold">规划助手</h1>
          <p className="text-muted-foreground mt-1">AI 帮你制定学习计划</p>
        </div>
      </div>

      <div className="bg-gradient-to-br from-violet-500 to-indigo-600 rounded-2xl p-8 text-white">
        <div className="flex items-center gap-4 mb-6">
          <div className="p-3 bg-white/20 rounded-xl">
            <Sparkles className="w-8 h-8" />
          </div>
          <div>
            <h2 className="text-2xl font-bold">智能学习规划</h2>
            <p className="text-white/80 mt-1">输入你想学习的内容，AI 将为你生成个性化学习计划</p>
          </div>
        </div>
        <div className="flex gap-3">
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
            className="px-6 py-3 bg-white text-violet-600 font-semibold rounded-xl hover:bg-white/90 transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isGenerating ? (
              <>
                <div className="w-4 h-4 border-2 border-violet-600 border-t-transparent rounded-full animate-spin" />
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

      <div>
        <h3 className="font-semibold mb-4">热门学习主题</h3>
        <div className="grid grid-cols-6 gap-3">
          {recommendedTopics.map((topicItem) => (
            <button
              key={topicItem.title}
              onClick={() => setTopic(topicItem.title)}
              className="p-4 bg-card rounded-xl border hover:border-primary hover:shadow-lg transition-all text-left"
            >
              <div className={`p-2 bg-${topicItem.color}-100 rounded-lg w-fit mb-2`}>
                <Target className={`w-5 h-5 text-${topicItem.color}-600`} />
              </div>
              <p className="text-sm font-medium">{topicItem.title}</p>
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div className="bg-card rounded-xl border">
          <div className="p-4 border-b">
            <h3 className="font-semibold">学习规划流程</h3>
          </div>
          <div className="p-4 space-y-4">
            {[
              { step: '1', title: '设定目标', desc: '选择你想学习的内容' },
              { step: '2', title: '生成计划', desc: 'AI 自动生成学习路径' },
              { step: '3', title: '每日学习', desc: '按照计划完成每日任务' },
              { step: '4', title: '追踪进度', desc: '查看学习统计和成果' },
            ].map((item, index) => (
              <div key={item.step} className="flex items-center gap-4">
                <div className="w-8 h-8 rounded-full bg-primary text-white flex items-center justify-center font-bold">
                  {item.step}
                </div>
                <div className="flex-1">
                  <h4 className="font-medium">{item.title}</h4>
                  <p className="text-sm text-muted-foreground">{item.desc}</p>
                </div>
                {index < 3 && <ChevronRight className="w-4 h-4 text-muted-foreground" />}
              </div>
            ))}
          </div>
        </div>

        <div className="bg-card rounded-xl border">
          <div className="p-4 border-b">
            <h3 className="font-semibold">我的学习目标</h3>
          </div>
          <div className="p-4 max-h-60 overflow-y-auto">
            {goals.length > 0 ? (
              goals.map((goal) => (
                <div key={goal.id} className="flex items-center gap-3 py-2 border-b last:border-0">
                  <div className={`p-2 rounded-lg ${goal.status === 'completed' ? 'bg-green-100' : 'bg-blue-100'}`}>
                    {goal.status === 'completed' ? (
                      <Check className="w-4 h-4 text-green-600" />
                    ) : (
                      <Target className="w-4 h-4 text-blue-600" />
                    )}
                  </div>
                  <div className="flex-1">
                    <h4 className="text-sm font-medium">{goal.name}</h4>
                    <p className="text-xs text-muted-foreground">{goal.progress}% 完成</p>
                  </div>
                  <ArrowRight className="w-4 h-4 text-muted-foreground" />
                </div>
              ))
            ) : (
              <div className="text-center text-muted-foreground py-4">
                <Target className="w-12 h-12 mx-auto mb-2 opacity-50" />
                <p className="text-sm">暂无学习目标</p>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="bg-card rounded-xl border">
        <div className="p-4 border-b">
          <h3 className="font-semibold">规划功能介绍</h3>
        </div>
        <div className="p-4">
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center">
              <div className="p-3 bg-blue-100 rounded-xl w-fit mx-auto mb-2">
                <Target className="w-6 h-6 text-blue-600" />
              </div>
              <h4 className="font-medium">智能推荐</h4>
              <p className="text-sm text-muted-foreground mt-1">根据你的水平推荐合适的学习路径</p>
            </div>
            <div className="text-center">
              <div className="p-3 bg-green-100 rounded-xl w-fit mx-auto mb-2">
                <TrendingUp className="w-6 h-6 text-green-600" />
              </div>
              <h4 className="font-medium">进度追踪</h4>
              <p className="text-sm text-muted-foreground mt-1">实时查看学习进度和成果</p>
            </div>
            <div className="text-center">
              <div className="p-3 bg-amber-100 rounded-xl w-fit mx-auto mb-2">
                <Calendar className="w-6 h-6 text-amber-600" />
              </div>
              <h4 className="font-medium">灵活调整</h4>
              <p className="text-sm text-muted-foreground mt-1">根据实际情况调整学习计划</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
