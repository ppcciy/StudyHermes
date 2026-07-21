import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Checkbox } from '@/components/ui/checkbox'
import {
  BookOpen,
  Plus,
  Search,
  Trash2,
  Edit2,
  Eye,
  RefreshCw,
  X,
  Target,
  Clock,
  AlertCircle,
  CheckCircle2,
  BookMarked,
  Brain,
} from '@/lib/icons'
import { getWrongQuestions, getWrongQuestionStats, getQuestionsNeedReview, createWrongQuestion, updateWrongQuestion, deleteWrongQuestion, reviewWrongQuestion } from '@/hermes'

interface WrongQuestion {
  id: string
  subject: string
  knowledge_point: string
  question_content: string
  user_answer: string
  correct_answer: string
  error_type: string
  difficulty: string
  mastery_status: string
  error_analysis: string
  solution: string
  tags: string[]
  created_at: string
  updated_at: string
  review_count: number
  correct_count: number
  last_review_at: string | null
  next_review_at: string | null
}

const ERROR_TYPE_MAP: Record<string, { label: string; color: string }> = {
  concept_misunderstanding: { label: '概念误解', color: 'bg-red-100 text-red-700' },
  calculation_error: { label: '计算错误', color: 'bg-orange-100 text-orange-700' },
  misreading: { label: '审题错误', color: 'bg-yellow-100 text-yellow-700' },
  careless: { label: '粗心大意', color: 'bg-purple-100 text-purple-700' },
  strategy_error: { label: '策略错误', color: 'bg-blue-100 text-blue-700' },
  other: { label: '其他', color: 'bg-gray-100 text-gray-700' },
}

const DIFFICULTY_MAP: Record<string, { label: string; color: string }> = {
  easy: { label: '简单', color: 'bg-green-100 text-green-700' },
  medium: { label: '中等', color: 'bg-yellow-100 text-yellow-700' },
  hard: { label: '困难', color: 'bg-red-100 text-red-700' },
}

const MASTERY_MAP: Record<string, { label: string; color: string }> = {
  unknown: { label: '未知', color: 'bg-gray-100 text-gray-700' },
  weak: { label: '薄弱', color: 'bg-red-100 text-red-700' },
  basic: { label: '基础', color: 'bg-orange-100 text-orange-700' },
  proficient: { label: '熟练', color: 'bg-blue-100 text-blue-700' },
  mastered: { label: '掌握', color: 'bg-green-100 text-green-700' },
}

const SUBJECT_OPTIONS = ['数学', '物理', '化学', '英语', '语文', '编程', '其他']
const KNOWLEDGE_POINTS: Record<string, string[]> = {
  '数学': ['函数', '代数', '几何', '概率统计', '微积分'],
  '物理': ['力学', '电学', '光学', '热学', '量子力学'],
  '化学': ['有机化学', '无机化学', '分析化学', '物理化学'],
  '英语': ['语法', '词汇', '阅读理解', '写作', '听力'],
  '语文': ['阅读理解', '写作', '文言文', '诗词鉴赏'],
  '编程': ['算法', '数据结构', '面向对象', '数据库', '网络'],
  '其他': [],
}

export function MistakesView() {
  const queryClient = useQueryClient()
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedSubject, setSelectedSubject] = useState<string>('')
  const [selectedKnowledge, setSelectedKnowledge] = useState<string>('')
  const [selectedErrorType, setSelectedErrorType] = useState<string>('')
  const [selectedDifficulty, setSelectedDifficulty] = useState<string>('')
  const [selectedMastery, setSelectedMastery] = useState<string>('')
  const [showAddDialog, setShowAddDialog] = useState(false)
  const [showDetailDialog, setShowDetailDialog] = useState(false)
  const [editingQuestion, setEditingQuestion] = useState<WrongQuestion | null>(null)
  const [selectedQuestion, setSelectedQuestion] = useState<WrongQuestion | null>(null)
  const [reviewAnswer, setReviewAnswer] = useState('')
  const [isReviewCorrect, setIsReviewCorrect] = useState(false)
  const [activeTab, setActiveTab] = useState<'all' | 'need-review'>('all')

  const { data: questions, isLoading } = useQuery({
    queryKey: ['wrong-questions', activeTab],
    queryFn: async () => {
      if (activeTab === 'need-review') {
        return await getQuestionsNeedReview()
      }
      return await getWrongQuestions({
        subject: selectedSubject || undefined,
        knowledge_point: selectedKnowledge || undefined,
        error_type: selectedErrorType || undefined,
        difficulty: selectedDifficulty || undefined,
        mastery_status: selectedMastery || undefined,
      })
    },
  })

  const { data: stats } = useQuery({
    queryKey: ['wrong-questions-stats'],
    queryFn: getWrongQuestionStats,
  })

  const filteredQuestions = questions?.filter(q =>
    q.question_content.toLowerCase().includes(searchTerm.toLowerCase()) ||
    q.knowledge_point.toLowerCase().includes(searchTerm.toLowerCase())
  ) || []

  const addMutation = useMutation({
    mutationFn: createWrongQuestion,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['wrong-questions'] })
      queryClient.invalidateQueries({ queryKey: ['wrong-questions-stats'] })
      setShowAddDialog(false)
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<WrongQuestion> }) => updateWrongQuestion(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['wrong-questions'] })
      queryClient.invalidateQueries({ queryKey: ['wrong-questions-stats'] })
      setEditingQuestion(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteWrongQuestion,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['wrong-questions'] })
      queryClient.invalidateQueries({ queryKey: ['wrong-questions-stats'] })
    },
  })

  const reviewMutation = useMutation({
    mutationFn: ({ id, answer, isCorrect }: { id: string; answer: string; isCorrect: boolean }) =>
      reviewWrongQuestion(id, { user_answer: answer, is_correct: isCorrect }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['wrong-questions'] })
      queryClient.invalidateQueries({ queryKey: ['wrong-questions-stats'] })
      setReviewAnswer('')
      setSelectedQuestion(null)
      setShowDetailDialog(false)
    },
  })

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const formData = new FormData(e.currentTarget)
    const questionData = {
      subject: formData.get('subject') as string,
      knowledge_point: formData.get('knowledge_point') as string,
      question_content: formData.get('question_content') as string,
      user_answer: formData.get('user_answer') as string,
      correct_answer: formData.get('correct_answer') as string,
      error_type: formData.get('error_type') as string,
      difficulty: formData.get('difficulty') as string,
      error_analysis: formData.get('error_analysis') as string,
      solution: formData.get('solution') as string,
      tags: (formData.get('tags') as string).split(',').map(t => t.trim()).filter(Boolean),
    }
    addMutation.mutate(questionData)
  }

  const handleUpdate = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!editingQuestion) return
    const formData = new FormData(e.currentTarget)
    const questionData = {
      subject: formData.get('subject') as string || undefined,
      knowledge_point: formData.get('knowledge_point') as string || undefined,
      question_content: formData.get('question_content') as string || undefined,
      user_answer: formData.get('user_answer') as string || undefined,
      correct_answer: formData.get('correct_answer') as string || undefined,
      error_type: formData.get('error_type') as string || undefined,
      difficulty: formData.get('difficulty') as string || undefined,
      error_analysis: formData.get('error_analysis') as string || undefined,
      solution: formData.get('solution') as string || undefined,
      tags: (formData.get('tags') as string).split(',').map(t => t.trim()).filter(Boolean),
    }
    updateMutation.mutate({ id: editingQuestion.id, data: questionData })
  }

  const handleReview = () => {
    if (!selectedQuestion || !reviewAnswer.trim()) return
    reviewMutation.mutate({
      id: selectedQuestion.id,
      answer: reviewAnswer,
      isCorrect: isReviewCorrect,
    })
  }

  const getAccuracy = (q: WrongQuestion) => {
    if (q.review_count === 0) return 0
    return Math.round((q.correct_count / q.review_count) * 100)
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-'
    return new Date(dateStr).toLocaleDateString('zh-CN')
  }

  return (
    <div className="flex min-h-0 flex-col overflow-y-auto gap-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <BookMarked className="w-7 h-7 text-blue-600" />
            错题本
          </h1>
          <p className="text-sm text-gray-500 mt-1">集中管理学习过程中出现的错误题目</p>
        </div>
        <Button onClick={() => setShowAddDialog(true)} className="gap-2">
          <Plus className="w-4 h-4" />
          添加错题
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center">
              <BookMarked className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{stats?.total || 0}</p>
              <p className="text-sm text-gray-500">错题总数</p>
            </div>
          </div>
        </div>
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center">
              <AlertCircle className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{stats?.need_review || 0}</p>
              <p className="text-sm text-gray-500">待复习</p>
            </div>
          </div>
        </div>
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-orange-100 flex items-center justify-center">
              <Target className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{stats?.by_mastery?.weak || 0}</p>
              <p className="text-sm text-gray-500">薄弱知识点</p>
            </div>
          </div>
        </div>
        <div className="bg-card rounded-xl p-4 border">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-green-100 flex items-center justify-center">
              <CheckCircle2 className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{stats?.by_mastery?.mastered || 0}</p>
              <p className="text-sm text-gray-500">已掌握</p>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <div className="flex bg-gray-100 rounded-lg p-1">
          <button
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'all' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
            }`}
            onClick={() => setActiveTab('all')}
          >
            全部错题
          </button>
          <button
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'need-review' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
            }`}
            onClick={() => setActiveTab('need-review')}
          >
            待复习
          </button>
        </div>
      </div>

      <div className="bg-card rounded-xl border">
        <div className="flex flex-col md:flex-row gap-4 p-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <Input
              placeholder="搜索题目内容或知识点..."
              value={searchTerm}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchTerm(e.target.value)}
              className="pl-10"
            />
          </div>
          <div className="flex gap-2 flex-wrap">
            <Select value={selectedSubject} onValueChange={(value) => { setSelectedSubject(value); setSelectedKnowledge('') }}>
              <SelectTrigger className="w-32">
                <SelectValue placeholder="学科" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">全部</SelectItem>
                {SUBJECT_OPTIONS.map(s => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={selectedKnowledge} onValueChange={setSelectedKnowledge} disabled={!selectedSubject}>
              <SelectTrigger className="w-32">
                <SelectValue placeholder="知识点" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">全部</SelectItem>
                {(KNOWLEDGE_POINTS[selectedSubject] || []).map(k => (
                  <SelectItem key={k} value={k}>{k}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={selectedErrorType} onValueChange={setSelectedErrorType}>
              <SelectTrigger className="w-32">
                <SelectValue placeholder="错误类型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">全部</SelectItem>
                {Object.entries(ERROR_TYPE_MAP).map(([key, { label }]) => (
                  <SelectItem key={key} value={key}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={selectedDifficulty} onValueChange={setSelectedDifficulty}>
              <SelectTrigger className="w-24">
                <SelectValue placeholder="难度" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">全部</SelectItem>
                {Object.entries(DIFFICULTY_MAP).map(([key, { label }]) => (
                  <SelectItem key={key} value={key}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              onClick={() => {
                setSelectedSubject('')
                setSelectedKnowledge('')
                setSelectedErrorType('')
                setSelectedDifficulty('')
                setSelectedMastery('')
                setSearchTerm('')
              }}
            >
              重置筛选
            </Button>
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <RefreshCw className="w-6 h-6 text-blue-600 animate-spin" />
        </div>
      ) : filteredQuestions.length === 0 ? (
        <div className="bg-card rounded-xl p-12 text-center border">
          <BookMarked className="w-12 h-12 text-gray-300 mx-auto mb-4" />
          <p className="text-gray-500">暂无错题记录</p>
          <Button onClick={() => setShowAddDialog(true)} className="mt-4">
            添加第一道错题
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          {filteredQuestions.map((question) => (
            <div key={question.id} className="bg-card rounded-xl border hover:shadow-md transition-shadow">
              <div className="p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <Badge className={ERROR_TYPE_MAP[question.error_type]?.color || 'bg-gray-100'}>
                        {ERROR_TYPE_MAP[question.error_type]?.label || question.error_type}
                      </Badge>
                      <Badge className={DIFFICULTY_MAP[question.difficulty]?.color || 'bg-gray-100'}>
                        {DIFFICULTY_MAP[question.difficulty]?.label || question.difficulty}
                      </Badge>
                      <Badge className={MASTERY_MAP[question.mastery_status]?.color || 'bg-gray-100'}>
                        {MASTERY_MAP[question.mastery_status]?.label || question.mastery_status}
                      </Badge>
                      {question.tags.map(tag => (
                        <span key={tag} className="text-xs border border-gray-300 rounded px-2 py-0.5 text-gray-600">{tag}</span>
                      ))}
                    </div>
                    <p className="text-sm text-gray-500 mb-1">
                      <span className="font-medium">{question.subject}</span> - {question.knowledge_point}
                    </p>
                    <p className="text-gray-900 line-clamp-2 mb-3">{question.question_content}</p>
                    <div className="flex items-center gap-4 text-sm text-gray-500">
                      <span className="flex items-center gap-1">
                        <RefreshCw className="w-3 h-3" />
                        复习 {question.review_count} 次
                      </span>
                      <span className="flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" />
                        正确率 {getAccuracy(question)}%
                      </span>
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        下次复习: {formatDate(question.next_review_at)}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-4">
                    <Button variant="ghost" size="sm" onClick={() => { setSelectedQuestion(question); setShowDetailDialog(true) }}>
                      <Eye className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setEditingQuestion(question)}>
                      <Edit2 className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => deleteMutation.mutate(question.id)}>
                      <Trash2 className="w-4 h-4 text-red-500" />
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
        <DialogHeader>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Plus className="w-5 h-5" />
            添加错题
          </h2>
        </DialogHeader>
        <DialogContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">学科 *</label>
                <select name="subject" required className="w-full px-3 py-2 border rounded-lg bg-background">
                  {SUBJECT_OPTIONS.map(s => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">知识点 *</label>
                <Input name="knowledge_point" required placeholder="输入知识点" />
              </div>
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 mb-1 block">题目内容 *</label>
              <Textarea name="question_content" required rows={4} placeholder="输入题目内容" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">我的答案 *</label>
                <Textarea name="user_answer" required rows={2} placeholder="输入你的答案" />
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">正确答案 *</label>
                <Textarea name="correct_answer" required rows={2} placeholder="输入正确答案" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">错误类型 *</label>
                <select name="error_type" required className="w-full px-3 py-2 border rounded-lg bg-background">
                  {Object.entries(ERROR_TYPE_MAP).map(([key, { label }]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">难度</label>
                <select name="difficulty" defaultValue="medium" className="w-full px-3 py-2 border rounded-lg bg-background">
                  {Object.entries(DIFFICULTY_MAP).map(([key, { label }]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 mb-1 block">错误分析</label>
              <Textarea name="error_analysis" rows={2} placeholder="分析错误原因" />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 mb-1 block">解题思路</label>
              <Textarea name="solution" rows={2} placeholder="输入解题思路" />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 mb-1 block">标签（逗号分隔）</label>
              <Input name="tags" placeholder="例如：易错,重点" />
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setShowAddDialog(false)}>取消</Button>
              <Button type="submit">添加</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {editingQuestion && (
        <Dialog open={!!editingQuestion} onOpenChange={(open: boolean) => !open && setEditingQuestion(null)}>
          <DialogHeader>
            <h2 className="text-xl font-bold flex items-center gap-2">
              <Edit2 className="w-5 h-5" />
              编辑错题
            </h2>
          </DialogHeader>
          <DialogContent>
            <form onSubmit={handleUpdate} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">学科</label>
                  <select name="subject" defaultValue={editingQuestion.subject} className="w-full px-3 py-2 border rounded-lg bg-background">
                    {SUBJECT_OPTIONS.map(s => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">知识点</label>
                  <Input name="knowledge_point" defaultValue={editingQuestion.knowledge_point} />
                </div>
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">题目内容</label>
                <Textarea name="question_content" rows={4} defaultValue={editingQuestion.question_content} />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">我的答案</label>
                  <Textarea name="user_answer" rows={2} defaultValue={editingQuestion.user_answer} />
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">正确答案</label>
                  <Textarea name="correct_answer" rows={2} defaultValue={editingQuestion.correct_answer} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">错误类型</label>
                  <select name="error_type" defaultValue={editingQuestion.error_type} className="w-full px-3 py-2 border rounded-lg bg-background">
                    {Object.entries(ERROR_TYPE_MAP).map(([key, { label }]) => (
                      <option key={key} value={key}>{label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">难度</label>
                  <select name="difficulty" defaultValue={editingQuestion.difficulty} className="w-full px-3 py-2 border rounded-lg bg-background">
                    {Object.entries(DIFFICULTY_MAP).map(([key, { label }]) => (
                      <option key={key} value={key}>{label}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">错误分析</label>
                <Textarea name="error_analysis" rows={2} defaultValue={editingQuestion.error_analysis} />
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">解题思路</label>
                <Textarea name="solution" rows={2} defaultValue={editingQuestion.solution} />
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">标签（逗号分隔）</label>
                <Input name="tags" defaultValue={editingQuestion.tags.join(', ')} />
              </div>
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => setEditingQuestion(null)}>取消</Button>
                <Button type="submit">保存</Button>
              </div>
            </form>
          </DialogContent>
        </Dialog>
      )}

      {selectedQuestion && (
        <Dialog open={showDetailDialog} onOpenChange={setShowDetailDialog}>
          <DialogHeader>
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-bold flex items-center gap-2">
                <BookOpen className="w-5 h-5" />
                错题详情
              </h2>
              <Badge className={MASTERY_MAP[selectedQuestion.mastery_status]?.color || 'bg-gray-100'}>
                {MASTERY_MAP[selectedQuestion.mastery_status]?.label}
              </Badge>
            </div>
          </DialogHeader>
          <DialogContent>
            <div className="space-y-4">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge className={ERROR_TYPE_MAP[selectedQuestion.error_type]?.color || 'bg-gray-100'}>
                  {ERROR_TYPE_MAP[selectedQuestion.error_type]?.label}
                </Badge>
                <Badge className={DIFFICULTY_MAP[selectedQuestion.difficulty]?.color || 'bg-gray-100'}>
                  {DIFFICULTY_MAP[selectedQuestion.difficulty]?.label}
                </Badge>
                <span className="text-sm text-gray-500">
                  {selectedQuestion.subject} - {selectedQuestion.knowledge_point}
                </span>
              </div>

              <div className="bg-gray-50 rounded-lg p-4">
                <h3 className="text-sm font-medium text-gray-700 mb-2">题目内容</h3>
                <p className="text-gray-900">{selectedQuestion.question_content}</p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="bg-red-50 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-red-700 mb-2">我的答案</h3>
                  <p className="text-gray-900">{selectedQuestion.user_answer}</p>
                </div>
                <div className="bg-green-50 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-green-700 mb-2">正确答案</h3>
                  <p className="text-gray-900">{selectedQuestion.correct_answer}</p>
                </div>
              </div>

              {selectedQuestion.error_analysis && (
                <div className="bg-yellow-50 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-yellow-700 mb-2 flex items-center gap-2">
                    <AlertCircle className="w-4 h-4" />
                    错误分析
                  </h3>
                  <p className="text-gray-900">{selectedQuestion.error_analysis}</p>
                </div>
              )}

              {selectedQuestion.solution && (
                <div className="bg-blue-50 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-blue-700 mb-2 flex items-center gap-2">
                    <Brain className="w-4 h-4" />
                    解题思路
                  </h3>
                  <p className="text-gray-900">{selectedQuestion.solution}</p>
                </div>
              )}

              <div className="flex items-center gap-4 text-sm text-gray-500 pt-2 border-t">
                <span>复习次数: {selectedQuestion.review_count}</span>
                <span>正确次数: {selectedQuestion.correct_count}</span>
                <span>正确率: {getAccuracy(selectedQuestion)}%</span>
                <span>下次复习: {formatDate(selectedQuestion.next_review_at)}</span>
              </div>

              <div className="border-t pt-4">
                <h3 className="text-sm font-medium text-gray-700 mb-3">复习此题</h3>
                <Textarea
                  value={reviewAnswer}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setReviewAnswer(e.target.value)}
                  placeholder="输入你的答案..."
                  rows={3}
                  className="mb-3"
                />
                <div className="flex items-center gap-4 mb-3">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <Checkbox checked={isReviewCorrect} onCheckedChange={(checked) => setIsReviewCorrect(checked === true)} />
                    <span className="text-sm">回答正确</span>
                  </label>
                </div>
                <Button onClick={handleReview} disabled={!reviewAnswer.trim()} className="w-full">
                  提交复习
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}