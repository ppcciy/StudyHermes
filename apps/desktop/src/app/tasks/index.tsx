import type * as React from 'react'
import { useState, useEffect } from 'react'
import { ListTodo, Check, Clock, Calendar, Target, Filter, ArrowRight } from '@/lib/icons'
import { getTodayTasks, updateTaskStatus, type DailyTask } from '@/hermes'

export function TasksView(): React.ReactElement {
  const [tasks, setTasks] = useState<DailyTask[]>([])
  const [goalNames, setGoalNames] = useState<Record<string, string>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'pending' | 'completed'>('all')

  useEffect(() => {
    loadTasks()
  }, [])

  const loadTasks = async () => {
    setIsLoading(true)
    try {
      const data = await getTodayTasks()
      setTasks(data.tasks)
      setGoalNames(data.goal_names)
    } catch (error) {
      console.error('Failed to load tasks:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleTaskToggle = async (taskId: string, currentStatus: string) => {
    const newStatus = currentStatus === 'completed' ? 'pending' : 'completed'
    try {
      await updateTaskStatus(taskId, newStatus)
      await loadTasks()
    } catch (error) {
      console.error('Failed to update task:', error)
    }
  }

  const filteredTasks = tasks.filter(task => {
    if (filter === 'all') return true
    return task.status === filter
  })

  const pendingCount = tasks.filter(t => t.status === 'pending').length
  const completedCount = tasks.filter(t => t.status === 'completed').length

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin" />
        <p className="mt-4 text-muted-foreground">加载中...</p>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-col overflow-y-auto gap-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">任务管理</h1>
          <p className="text-muted-foreground mt-1">管理和追踪你的学习任务</p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <ListTodo className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-muted-foreground text-xs">总任务</p>
              <p className="text-xl font-bold">{tasks.length}</p>
            </div>
          </div>
        </div>
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-amber-100 rounded-lg">
              <Clock className="w-5 h-5 text-amber-600" />
            </div>
            <div>
              <p className="text-muted-foreground text-xs">待完成</p>
              <p className="text-xl font-bold">{pendingCount}</p>
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
              <p className="text-xl font-bold">{completedCount}</p>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <h3 className="font-semibold">任务列表</h3>
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-muted-foreground" />
          <div className="flex bg-muted rounded-lg p-1">
            {[
              { key: 'all', label: '全部' },
              { key: 'pending', label: '待完成' },
              { key: 'completed', label: '已完成' },
            ].map(item => (
              <button
                key={item.key}
                onClick={() => setFilter(item.key as typeof filter)}
                className={`px-3 py-1 text-sm rounded-md transition-colors ${
                  filter === item.key ? 'bg-white text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-card rounded-xl border">
        <div className="divide-y">
          {filteredTasks.length > 0 ? (
            filteredTasks.map((task) => (
              <div
                key={task.id}
                className={`p-4 hover:bg-muted/50 transition-colors ${
                  task.status === 'completed' ? 'bg-green-50/50' : ''
                }`}
              >
                <div className="flex items-center gap-4">
                  <input
                    type="checkbox"
                    checked={task.status === 'completed'}
                    onChange={() => handleTaskToggle(task.id, task.status)}
                    className="w-5 h-5 rounded border-primary text-primary"
                  />
                  <div className={`flex-1 ${task.status === 'completed' ? 'line-through text-muted-foreground' : ''}`}>
                    <h4 className="font-medium">{task.title}</h4>
                    <p className="text-sm text-muted-foreground mt-0.5">{task.description}</p>
                  </div>
                  {goalNames[task.goal_id] && (
                    <span className="px-2 py-1 text-xs bg-muted rounded-full text-muted-foreground">
                      {goalNames[task.goal_id]}
                    </span>
                  )}
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {task.estimated_minutes} 分钟
                    </span>
                    <span className="flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      {new Date(task.scheduled_date).toLocaleDateString()}
                    </span>
                  </div>
                  <span className={`px-2 py-1 text-xs rounded-full ${
                    task.priority === 1 ? 'bg-red-100 text-red-700' :
                    task.priority === 2 ? 'bg-amber-100 text-amber-700' :
                    'bg-gray-100 text-gray-700'
                  }`}>
                    {task.priority === 1 ? '高优先级' : task.priority === 2 ? '中优先级' : '低优先级'}
                  </span>
                </div>
              </div>
            ))
          ) : (
            <div className="p-8 text-center text-muted-foreground">
              <ListTodo className="w-16 h-16 mx-auto mb-4 opacity-50" />
              <p className="text-lg">暂无任务</p>
              <p className="text-sm mt-2">创建学习目标后会自动生成任务</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
