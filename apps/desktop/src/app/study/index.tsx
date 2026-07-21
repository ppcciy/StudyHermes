import type * as React from 'react'
import { useState, useRef } from 'react'
import { BookOpen, Play, Pause, Clock, Flame, Target, ArrowRight, ChevronRight } from '@/lib/icons'

export function StudyView(): React.ReactElement {
  const [isStudying, setIsStudying] = useState(false)
  const [timeElapsed, setTimeElapsed] = useState(0)
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const studyTopics = [
    {
      id: 'python',
      title: 'Python 入门',
      description: '学习 Python 基础语法和编程思想',
      progress: 45,
      duration: '2小时',
      icon: 'Code2'
    },
    {
      id: 'react',
      title: 'React 开发',
      description: '掌握 React 组件化开发和状态管理',
      progress: 30,
      duration: '3小时',
      icon: 'Layers'
    },
    {
      id: 'ml',
      title: '机器学习',
      description: '理解机器学习基本概念和算法',
      progress: 20,
      duration: '4小时',
      icon: 'Brain'
    },
    {
      id: 'data',
      title: '数据分析',
      description: '使用 Python 进行数据处理和可视化',
      progress: 55,
      duration: '2.5小时',
      icon: 'BarChart3'
    }
  ]

  const startStudy = () => {
    setIsStudying(true)
    timerRef.current = setInterval(() => {
      setTimeElapsed(prev => prev + 1)
    }, 1000)
  }

  const stopStudy = () => {
    setIsStudying(false)
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  const formatTime = (seconds: number) => {
    const hrs = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    const secs = seconds % 60
    return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }

  return (
    <div className="flex min-h-0 flex-col overflow-y-auto gap-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">学习中心</h1>
          <p className="text-muted-foreground mt-1">开始你的学习之旅</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div className="bg-gradient-to-br from-orange-500 to-red-600 rounded-2xl p-6 text-white">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="p-3 bg-white/20 rounded-xl">
                <Clock className="w-8 h-8" />
              </div>
              <div>
                <p className="text-white/80 text-sm">今日学习时长</p>
                <p className="text-4xl font-bold mt-1">{formatTime(timeElapsed)}</p>
              </div>
            </div>
            <div className={`p-4 rounded-full ${isStudying ? 'bg-white/30' : 'bg-white/20'}`}>
              {isStudying ? (
                <Pause className="w-8 h-8 cursor-pointer" onClick={stopStudy} />
              ) : (
                <Play className="w-8 h-8 cursor-pointer" onClick={startStudy} />
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 mt-4">
            <Flame className="w-5 h-5" />
            <span className="text-sm">
              {isStudying ? '正在学习中...' : '点击播放开始学习'}
            </span>
          </div>
        </div>

        <div className="bg-card rounded-xl p-6 border">
          <h3 className="font-semibold mb-4">学习统计</h3>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">本周学习天数</span>
              <span className="font-semibold">5 天</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">累计学习时长</span>
              <span className="font-semibold">12.5 小时</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">完成任务数</span>
              <span className="font-semibold">18 个</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">学习目标完成率</span>
              <span className="font-semibold">65%</span>
            </div>
          </div>
        </div>
      </div>

      <div>
        <h3 className="font-semibold mb-4">继续学习</h3>
        <div className="grid grid-cols-4 gap-4">
          {studyTopics.map((topic) => (
            <div
              key={topic.id}
              className={`bg-card rounded-xl p-4 border cursor-pointer hover:border-primary hover:shadow-lg transition-all ${
                selectedTopic === topic.id ? 'border-primary shadow-lg' : ''
              }`}
              onClick={() => setSelectedTopic(topic.id)}
            >
              <div className="flex items-center gap-3 mb-3">
                <div className="p-2 bg-blue-100 rounded-lg">
                  <BookOpen className="w-5 h-5 text-blue-600" />
                </div>
                <h4 className="font-medium">{topic.title}</h4>
              </div>
              <p className="text-sm text-muted-foreground mb-3">{topic.description}</p>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">{topic.duration}</span>
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              </div>
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="text-muted-foreground">进度</span>
                  <span>{topic.progress}%</span>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-300"
                    style={{ width: `${topic.progress}%` }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-card rounded-xl border">
        <div className="p-4 border-b">
          <h3 className="font-semibold">学习建议</h3>
        </div>
        <div className="p-4">
          <div className="flex items-start gap-4">
            <div className="p-2 bg-amber-100 rounded-lg">
              <Target className="w-5 h-5 text-amber-600" />
            </div>
            <div>
              <h4 className="font-medium">专注学习建议</h4>
              <p className="text-sm text-muted-foreground mt-1">
                根据你的学习习惯，建议每天学习 30-60 分钟，保持学习的连续性比长时间学习更有效。
                建议在学习前制定明确的学习目标，并在学习后进行总结和复习。
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
