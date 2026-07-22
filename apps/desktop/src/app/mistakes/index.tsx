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
  Sparkles,
  FileText,
  Zap,
  ChevronDown,
  ChevronRight,
  Upload,
} from '@/lib/icons'
import {
  getWrongQuestions,
  getWrongQuestionStats,
  getQuestionsNeedReview,
  createWrongQuestion,
  updateWrongQuestion,
  deleteWrongQuestion,
  reviewWrongQuestion,
  analyzeWrongQuestion,
  generateSimilarQuestion,
  getSimilarQuestions,
  getWrongQuestionReport,
  uploadQuestionFile,
  getWrongQuestionFileDataUrl,
  type QuestionAnalysis,
  type SimilarQuestion,
  type WrongQuestionReport,
  type ExtractedQuestion,
} from '@/hermes'

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
  question_file?: string | null
  correct_answer_file?: string | null
  user_answer_file?: string | null
}

const ERROR_TYPE_MAP: Record<string, { label: string; color: string }> = {
  concept_misunderstanding: { label: '概念误解', color: 'border-transparent bg-[#eadbd8] text-[#8f5f57]' },
  calculation_error: { label: '计算错误', color: 'border-transparent bg-[#efe5cf] text-[#8b7041]' },
  misreading: { label: '审题错误', color: 'border-transparent bg-[#eee8d5] text-[#847445]' },
  careless: { label: '粗心大意', color: 'border-transparent bg-[#e5ddeb] text-[#756583]' },
  strategy_error: { label: '策略错误', color: 'border-transparent bg-[#dce4e8] text-[#5d7380]' },
  other: { label: '其他', color: 'border-transparent bg-[#e7e4dd] text-[#6f706a]' },
}

const DIFFICULTY_MAP: Record<string, { label: string; color: string }> = {
  easy: { label: '简单', color: 'border-transparent bg-[#d8e3d8] text-[#55745f]' },
  medium: { label: '中等', color: 'border-transparent bg-[#efe5cf] text-[#8b7041]' },
  hard: { label: '困难', color: 'border-transparent bg-[#eadbd8] text-[#8f5f57]' },
}

const MASTERY_MAP: Record<string, { label: string; color: string }> = {
  unknown: { label: '未知', color: 'border-transparent bg-[#e7e4dd] text-[#6f706a]' },
  weak: { label: '薄弱', color: 'border-transparent bg-[#eadbd8] text-[#8f5f57]' },
  basic: { label: '基础', color: 'border-transparent bg-[#efe5cf] text-[#8b7041]' },
  proficient: { label: '熟练', color: 'border-transparent bg-[#dce4e8] text-[#5d7380]' },
  mastered: { label: '掌握', color: 'border-transparent bg-[#d8e3d8] text-[#55745f]' },
}

const ALL_SELECT_VALUE = '__all__'

function wrongQuestionFileName(filePath: string): string {
  return filePath.split(/[\\/]/).filter(Boolean).pop() || filePath
}

function isImageFile(filePath: string): boolean {
  return /\.(png|jpg|jpeg|gif|bmp|webp)$/i.test(wrongQuestionFileName(filePath))
}

function WrongQuestionFilePreview({
  alt,
  filePath,
  maxHeightClass,
}: {
  alt: string
  filePath: string
  maxHeightClass: string
}) {
  const fileQuery = useQuery({
    queryKey: ['wrong-question-file', filePath],
    queryFn: () => getWrongQuestionFileDataUrl(filePath),
  })

  if (fileQuery.isLoading) {
    return <p className="mt-2 text-xs text-[#7b817b]">正在加载原图...</p>
  }

  if (fileQuery.isError || !fileQuery.data?.data_url) {
    return <p className="mt-2 text-xs text-[#b04e45]">原始文件加载失败</p>
  }

  const fileName = wrongQuestionFileName(filePath)

  return (
    <div className="mt-2">
      {isImageFile(filePath) ? (
        <img src={fileQuery.data.data_url} alt={alt} className={`max-w-full rounded-lg border border-[#d8d2c4] ${maxHeightClass}`} />
      ) : (
        <a
          href={fileQuery.data.data_url}
          download={fileName}
          className="flex items-center gap-1 text-xs text-[#5d7380] hover:text-[#405661]"
        >
          <FileText className="w-3 h-3" /> 查看原始文件
        </a>
      )}
    </div>
  )
}

function cleanAiDisplayText(value?: string | null): string {
  let text = (value || '')
    .replace(/\uFFFD+/g, '')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
  text = trimRepetitiveAiNoise(text)
  text = text
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  if (!text) {
    return ''
  }

  const leakedFieldIndex = text.search(
    /\n?\s*["']?(?:detail_solution|detailed_solution|relatedrelated_concepts|related_concepts|knowledge_points|error_reason|solution|correct_answer|explanation|options)["']?\s*[:：]/i,
  )
  if (leakedFieldIndex > 0) {
    text = text.slice(0, leakedFieldIndex).trim()
  }
  text = text.replace(/["'“”]\s*,?\s*$/g, '').replace(/[,，]\s*$/g, '').trim()

  if (text.includes('AI 返回内容无法解析') || text.includes('AI returned content')) {
    return ''
  }

  if (text.startsWith('{') && (text.includes('"knowledge_points"') || text.includes('"error_reason"'))) {
    return ''
  }

  return text
}

function trimRepetitiveAiNoise(value: string): string {
  let text = value
  const repeatedMatch = /([^\sA-Za-z0-9])(?:\s*\1){15,}/u.exec(text)
  if (repeatedMatch?.index !== undefined) {
    const prefix = text.slice(0, repeatedMatch.index).replace(/[\s,.;:'"()[\]{}<>]+$/g, '')
    const suffix = text.slice(repeatedMatch.index + repeatedMatch[0].length)
    text = prefix.length >= 20 ? prefix : `${prefix}\n${suffix}`.trim()
  }

  return text
    .split(/\r?\n/)
    .filter(line => {
      const compact = line.replace(/\s+/g, '')
      if (compact.length < 80) return true
      const counts = new Map<string, number>()
      for (const char of compact) {
        counts.set(char, (counts.get(char) || 0) + 1)
      }
      const dominant = Math.max(...counts.values())
      return dominant / compact.length < 0.45
    })
    .join('\n')
    .trim()
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
  const [aiAnalysis, setAiAnalysis] = useState<QuestionAnalysis | null>(null)
  const [showSimilar, setShowSimilar] = useState(false)
  const [showReport, setShowReport] = useState(false)
  const [reportSubject, setReportSubject] = useState<string>('')
  const [formData, setFormData] = useState<ExtractedQuestion & {
    question_file?: string
    correct_answer_file?: string
    user_answer_file?: string
  }>({
    subject: '',
    knowledge_point: '',
    question_content: '',
    user_answer: '',
    correct_answer: '',
    error_type: 'other',
    difficulty: 'medium',
    error_analysis: '',
    solution: '',
  })

  const { data: questions, isLoading } = useQuery({
    queryKey: [
      'wrong-questions',
      activeTab,
      selectedSubject,
      selectedKnowledge,
      selectedErrorType,
      selectedDifficulty,
      selectedMastery,
    ],
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
  const subjectOptions = Array.from(new Set([
    ...Object.keys((stats?.by_subject || {}) as Record<string, unknown>),
    ...(questions || []).map(q => q.subject),
  ].map(subject => subject.trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b, 'zh-CN'))
  const knowledgeOptions = Array.from(new Set((questions || [])
    .filter(q => !selectedSubject || q.subject === selectedSubject)
    .map(q => q.knowledge_point.trim())
    .filter(Boolean))).sort((a, b) => a.localeCompare(b, 'zh-CN'))

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

  const analyzeMutation = useMutation({
    mutationFn: (id: string) => analyzeWrongQuestion(id),
    onSuccess: (data) => {
      setAiAnalysis(data.analysis)
      setSelectedQuestion(prev => {
        if (!prev || prev.id !== data.question_id) {
          return prev
        }

        return {
          ...prev,
          knowledge_point: data.analysis.knowledge_points?.[0] || prev.knowledge_point,
          error_analysis: data.analysis.error_reason || prev.error_analysis,
          solution: data.analysis.solution || prev.solution,
        }
      })
      queryClient.invalidateQueries({ queryKey: ['wrong-questions'] })
    },
  })

  const generateSimilarMutation = useMutation({
    mutationFn: (id: string) => generateSimilarQuestion(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['similar-questions', selectedQuestion?.id] })
    },
  })

  const reportMutation = useMutation({
    mutationFn: (subject?: string) => getWrongQuestionReport(subject),
  })

  const uploadMutation = useMutation({
    mutationFn: ({ file, fieldType }: { file: File; fieldType: 'question' | 'correct_answer' | 'user_answer' }) => uploadQuestionFile(file, fieldType),
    onSuccess: (data) => {
      const ft = data.field_type
      setFormData(prev => {
        const next = { ...prev }
        // 保存文件路径
        if (ft === 'question') {
          next.question_file = data.file_path
        } else if (ft === 'correct_answer') {
          next.correct_answer_file = data.file_path
        } else if (ft === 'user_answer') {
          next.user_answer_file = data.file_path
        }
        // 题目上传：AI 自动填充所有结构化字段
        if (ft === 'question') {
          if (data.extracted.subject) next.subject = data.extracted.subject
          if (data.extracted.knowledge_point) next.knowledge_point = data.extracted.knowledge_point
          if (data.extracted.question_content) next.question_content = data.extracted.question_content
          if (data.extracted.error_type) next.error_type = data.extracted.error_type
          if (data.extracted.difficulty) next.difficulty = data.extracted.difficulty
          if (data.extracted.error_analysis) next.error_analysis = data.extracted.error_analysis
          if (data.extracted.solution) next.solution = data.extracted.solution
          if (data.extracted.user_answer) next.user_answer = data.extracted.user_answer
          if (data.extracted.correct_answer) next.correct_answer = data.extracted.correct_answer
        }
        // 我的答案上传：填充答案文本
        if (ft === 'user_answer') {
          next.user_answer = data.extracted.user_answer || data.raw_text
        }
        // 正确答案上传：填充正确答案文本
        if (ft === 'correct_answer') {
          next.correct_answer = data.extracted.correct_answer || data.raw_text
        }
        return next
      })
    },
  })

  const { data: similarQuestions } = useQuery({
    queryKey: ['similar-questions', selectedQuestion?.id],
    queryFn: () => selectedQuestion ? getSimilarQuestions(selectedQuestion.id) : Promise.resolve([]),
    enabled: !!selectedQuestion && showSimilar,
  })

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const questionData = {
      subject: formData.subject,
      knowledge_point: formData.knowledge_point,
      question_content: formData.question_content,
      user_answer: formData.user_answer,
      correct_answer: formData.correct_answer,
      error_type: formData.error_type,
      difficulty: formData.difficulty,
      error_analysis: formData.error_analysis,
      solution: formData.solution,
      tags: [] as string[],
      question_file: formData.question_file || null,
      correct_answer_file: formData.correct_answer_file || null,
      user_answer_file: formData.user_answer_file || null,
    }
    addMutation.mutate(questionData)
  }

  const resetFormData = () => {
    setFormData({
      subject: '',
      knowledge_point: '',
      question_content: '',
      user_answer: '',
      correct_answer: '',
      error_type: 'other',
      difficulty: 'medium',
      error_analysis: '',
      solution: '',
      question_file: undefined,
      correct_answer_file: undefined,
      user_answer_file: undefined,
    })
    uploadMutation.reset()
  }

  const openAddDialog = () => {
    resetFormData()
    setShowAddDialog(true)
  }

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>, fieldType: 'question' | 'correct_answer' | 'user_answer') => {
    const file = e.target.files?.[0]
    if (file) {
      uploadMutation.mutate({ file, fieldType })
    }
  }

  const updateFormField = (field: keyof ExtractedQuestion, value: string) => {
    setFormData(prev => ({ ...prev, [field]: value }))
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

  const selectedErrorAnalysis = cleanAiDisplayText(selectedQuestion?.error_analysis)
  const selectedSolution = cleanAiDisplayText(selectedQuestion?.solution)
  const aiErrorReason = cleanAiDisplayText(aiAnalysis?.error_reason)
  const aiSolution = cleanAiDisplayText(aiAnalysis?.solution)
  const visibleSimilarQuestions = similarQuestions || []

  if (selectedQuestion) {
    return (
      <div className="flex h-full min-h-0 flex-col gap-6 overflow-hidden bg-[#f4f1ea] p-6 text-[#2f3437]">
        <div className="flex shrink-0 items-start justify-between gap-4">
          <div>
            <Button
              variant="ghost"
              className="mb-3 px-0"
              onClick={() => {
                setSelectedQuestion(null)
                setShowDetailDialog(false)
                setAiAnalysis(null)
                setShowSimilar(false)
              }}
            >
              返回错题列表
            </Button>
            <h1 className="flex items-center gap-2 text-2xl font-bold text-[#2f3437]">
              <BookOpen className="h-7 w-7 text-[#667c6d]" />
              错题详情
            </h1>
            <p className="mt-1 text-sm text-[#7b817b]">
              {selectedQuestion.subject} - {selectedQuestion.knowledge_point}
            </p>
          </div>
          <Badge className={MASTERY_MAP[selectedQuestion.mastery_status]?.color || 'bg-gray-100'}>
            {MASTERY_MAP[selectedQuestion.mastery_status]?.label || selectedQuestion.mastery_status}
          </Badge>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-6 overflow-y-auto xl:grid-cols-[minmax(0,1fr)_360px] xl:overflow-hidden">
          <div className="space-y-4 xl:min-h-0 xl:overflow-y-auto xl:pr-2">
            <div className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-5 shadow-sm">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <Badge className={ERROR_TYPE_MAP[selectedQuestion.error_type]?.color || 'bg-gray-100'}>
                  {ERROR_TYPE_MAP[selectedQuestion.error_type]?.label || selectedQuestion.error_type}
                </Badge>
                <Badge className={DIFFICULTY_MAP[selectedQuestion.difficulty]?.color || 'bg-gray-100'}>
                  {DIFFICULTY_MAP[selectedQuestion.difficulty]?.label || selectedQuestion.difficulty}
                </Badge>
              </div>
              <h2 className="mb-2 text-sm font-medium text-[#6f706a]">题目内容</h2>
              <p className="whitespace-pre-wrap break-words text-[#2f3437]">{selectedQuestion.question_content}</p>
              {selectedQuestion.question_file && (
                <WrongQuestionFilePreview alt="题目原图" filePath={selectedQuestion.question_file} maxHeightClass="max-h-[520px]" />
              )}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="rounded-lg border border-[#e2cbc6] bg-[#fbf4f2] p-5">
                <h2 className="mb-2 text-sm font-medium text-[#8f5f57]">我的答案</h2>
                <p className="whitespace-pre-wrap break-words text-[#2f3437]">{selectedQuestion.user_answer}</p>
                {selectedQuestion.user_answer_file && (
                  <WrongQuestionFilePreview alt="我的答案原图" filePath={selectedQuestion.user_answer_file} maxHeightClass="max-h-80" />
                )}
              </div>
              <div className="rounded-lg border border-[#cbdccf] bg-[#f2f7f2] p-5">
                <h2 className="mb-2 text-sm font-medium text-[#55745f]">正确答案</h2>
                <p className="whitespace-pre-wrap break-words text-[#2f3437]">{selectedQuestion.correct_answer}</p>
                {selectedQuestion.correct_answer_file && (
                  <WrongQuestionFilePreview alt="正确答案原图" filePath={selectedQuestion.correct_answer_file} maxHeightClass="max-h-80" />
                )}
              </div>
            </div>

            {selectedErrorAnalysis && (
              <div className="rounded-lg border border-[#e6dcc2] bg-[#fbf7eb] p-5">
                <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#8b7041]">
                  <AlertCircle className="w-4 h-4" />
                  错误分析
                </h2>
                <p className="whitespace-pre-wrap break-words text-[#2f3437]">{selectedErrorAnalysis}</p>
              </div>
            )}

            {selectedSolution && (
              <div className="rounded-lg border border-[#cfd9df] bg-[#f0f5f6] p-5">
                <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#5d7380]">
                  <Brain className="w-4 h-4" />
                  解题思路
                </h2>
                <p className="whitespace-pre-wrap break-words text-[#2f3437]">{selectedSolution}</p>
              </div>
            )}

            <div className="rounded-lg border border-[#d8cfe0] bg-[#f7f2f7] p-5 shadow-sm">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="flex items-center gap-2 text-sm font-medium text-[#756583]">
                  <Sparkles className="w-4 h-4" />
                  AI 智能分析
                </h2>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => analyzeMutation.mutate(selectedQuestion.id)}
                  disabled={analyzeMutation.isPending}
                  className="gap-1"
                >
                  {analyzeMutation.isPending ? (
                    <><RefreshCw className="w-3 h-3 animate-spin" /> 分析中...</>
                  ) : (
                    <><Sparkles className="w-3 h-3" /> {aiAnalysis ? '重新分析' : '开始分析'}</>
                  )}
                </Button>
              </div>
              {analyzeMutation.isError && (
                <p className="text-sm text-[#b04e45]">
                  分析失败：{(analyzeMutation.error as Error)?.message || '请检查 LLM 配置后重试'}
                </p>
              )}
              {aiAnalysis ? (
                <div className="space-y-3">
                  {aiAnalysis.knowledge_points.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-medium text-[#6f706a]">识别知识点</p>
                      <div className="flex flex-wrap gap-1">
                        {aiAnalysis.knowledge_points.map((kp, i) => (
                          <Badge key={i} className="border-transparent bg-[#e8deee] text-[#756583]">{kp}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {aiErrorReason && (
                    <div>
                      <p className="mb-1 text-xs font-medium text-[#6f706a]">错误原因</p>
                      <p className="whitespace-pre-wrap break-words text-sm text-[#2f3437]">{aiErrorReason}</p>
                    </div>
                  )}
                  {aiSolution && (
                    <div>
                      <p className="mb-1 text-xs font-medium text-[#6f706a]">详细解析</p>
                      <p className="whitespace-pre-wrap break-words text-sm text-[#2f3437]">{aiSolution}</p>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-sm text-[#7b817b]">点击“开始分析”让 AI 识别知识点、分析错因并生成解析。</p>
              )}
            </div>

            <div className="rounded-lg border border-[#cbd7df] bg-[#f1f5f7] p-5 shadow-sm">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="flex items-center gap-2 text-sm font-medium text-[#5d7380]">
                  <BookOpen className="w-4 h-4" />
                  相似练习题
                </h2>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setShowSimilar(prev => !prev)
                      if (!showSimilar) {
                        queryClient.invalidateQueries({ queryKey: ['similar-questions', selectedQuestion.id] })
                      }
                    }}
                    className="gap-1"
                  >
                    {showSimilar ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                    {showSimilar ? '收起' : '展开'}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setShowSimilar(true)
                      generateSimilarMutation.mutate(selectedQuestion.id)
                    }}
                    disabled={generateSimilarMutation.isPending}
                    className="gap-1"
                  >
                    {generateSimilarMutation.isPending ? (
                      <><RefreshCw className="w-3 h-3 animate-spin" /> 生成中...</>
                    ) : (
                      <><Plus className="w-3 h-3" /> 生成新题</>
                    )}
                  </Button>
                </div>
              </div>
              {generateSimilarMutation.isError && (
                <p className="text-sm text-[#b04e45]">
                  生成失败：{(generateSimilarMutation.error as Error)?.message || '请重试'}
                </p>
              )}
              {showSimilar && (
                <div className="space-y-3">
                  {visibleSimilarQuestions.length > 0 ? (
                    visibleSimilarQuestions.map((sq: SimilarQuestion) => (
                      <div key={sq.id} className="rounded-lg border border-[#d8d2c4] bg-[#fffdf9] p-3">
                        <p className="mb-2 whitespace-pre-wrap break-words text-sm text-[#2f3437]">{sq.question_content}</p>
                        {sq.options && (
                          <div className="mb-2 space-y-1">
                            {sq.options.map((opt, i) => (
                              <p key={i} className="text-xs text-[#6f706a]">{String.fromCharCode(65 + i)}. {opt}</p>
                            ))}
                          </div>
                        )}
                        <div className="mb-2 rounded-md bg-[#f2f7f2] p-2">
                          <p className="text-xs font-medium text-[#55745f]">正确答案</p>
                          <p className="whitespace-pre-wrap break-words text-sm text-[#2f3437]">{sq.correct_answer}</p>
                        </div>
                        {sq.explanation && (
                          <div className="rounded-md bg-[#f0f5f6] p-2">
                            <p className="text-xs font-medium text-[#5d7380]">解析</p>
                            <p className="whitespace-pre-wrap break-words text-sm text-[#2f3437]">{sq.explanation}</p>
                          </div>
                        )}
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-[#7b817b]">暂无相似题，点击“生成新题”让 AI 创建一道相似练习题。</p>
                  )}
                </div>
              )}
            </div>
          </div>

          <aside className="space-y-4 xl:min-h-0 xl:overflow-y-auto xl:pr-1">
            <div className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-5 shadow-sm">
              <h2 className="mb-3 text-sm font-medium text-[#575d57]">复习数据</h2>
              <div className="space-y-2 text-sm text-[#6f706a]">
                <div className="flex justify-between"><span>复习次数</span><span>{selectedQuestion.review_count}</span></div>
                <div className="flex justify-between"><span>正确次数</span><span>{selectedQuestion.correct_count}</span></div>
                <div className="flex justify-between"><span>正确率</span><span>{getAccuracy(selectedQuestion)}%</span></div>
                <div className="flex justify-between"><span>下次复习</span><span>{formatDate(selectedQuestion.next_review_at)}</span></div>
              </div>
            </div>

            <div className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-5 shadow-sm">
              <h2 className="mb-3 text-sm font-medium text-[#575d57]">复习此题</h2>
              <Textarea
                value={reviewAnswer}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setReviewAnswer(e.target.value)}
                placeholder="输入你的答案..."
                rows={5}
                className="mb-3 border-[#d8d2c4] bg-[#fffdf9] focus-visible:ring-[#9aa898]"
              />
              <label className="mb-3 flex cursor-pointer items-center gap-2">
                <Checkbox checked={isReviewCorrect} onCheckedChange={(checked) => setIsReviewCorrect(checked === true)} />
                <span className="text-sm">回答正确</span>
              </label>
              <Button onClick={handleReview} disabled={!reviewAnswer.trim()} className="w-full bg-[#667c6d] text-white hover:bg-[#586d5f]">
                提交复习
              </Button>
            </div>
          </aside>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-col gap-6 overflow-y-auto bg-[#f4f1ea] p-6 text-[#2f3437]">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-[#2f3437]">
            <BookMarked className="h-7 w-7 text-[#667c6d]" />
            错题本
          </h1>
          <p className="mt-1 text-sm text-[#7b817b]">集中管理学习过程中出现的错误题目</p>
        </div>
        <Button variant="outline" onClick={() => { setShowReport(true); reportMutation.mutate(reportSubject || undefined) }} className="gap-2 border-[#d8d2c4] bg-[#fbfaf7] text-[#575d57] hover:bg-[#f0eee6]">
          <FileText className="w-4 h-4" />
          分析报告
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <button
          type="button"
          className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-4 text-left shadow-sm transition hover:border-[#b9c6bb] hover:bg-[#f7f4ee]"
          onClick={() => {
            setActiveTab('all')
            setSelectedMastery('')
          }}
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#dfe7df]">
              <BookMarked className="h-5 w-5 text-[#667c6d]" />
            </div>
            <div>
              <p className="text-2xl font-bold text-[#2f3437]">{stats?.total || 0}</p>
              <p className="text-sm text-[#7b817b]">错题总数</p>
            </div>
          </div>
        </button>
        <button
          type="button"
          className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-4 text-left shadow-sm transition hover:border-[#d8b8b1] hover:bg-[#f7f4ee]"
          onClick={() => setActiveTab('need-review')}
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#eadbd8]">
              <AlertCircle className="h-5 w-5 text-[#8f5f57]" />
            </div>
            <div>
              <p className="text-2xl font-bold text-[#2f3437]">{stats?.need_review || 0}</p>
              <p className="text-sm text-[#7b817b]">待复习</p>
            </div>
          </div>
        </button>
        <button
          type="button"
          className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-4 text-left shadow-sm transition hover:border-[#d8c7a5] hover:bg-[#f7f4ee]"
          onClick={() => {
            setActiveTab('all')
            setSelectedMastery('weak')
          }}
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#efe5cf]">
              <Target className="h-5 w-5 text-[#8b7041]" />
            </div>
            <div>
              <p className="text-2xl font-bold text-[#2f3437]">{stats?.by_mastery?.weak || 0}</p>
              <p className="text-sm text-[#7b817b]">薄弱知识点</p>
            </div>
          </div>
        </button>
        <button
          type="button"
          className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-4 text-left shadow-sm transition hover:border-[#b9c6bb] hover:bg-[#f7f4ee]"
          onClick={() => {
            setActiveTab('all')
            setSelectedMastery('mastered')
          }}
        >
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#d8e3d8]">
              <CheckCircle2 className="h-5 w-5 text-[#55745f]" />
            </div>
            <div>
              <p className="text-2xl font-bold text-[#2f3437]">{stats?.by_mastery?.mastered || 0}</p>
              <p className="text-sm text-[#7b817b]">已掌握</p>
            </div>
          </div>
        </button>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <div className="flex rounded-lg border border-[#d8d2c4] bg-[#e9e5dc] p-1">
          <button
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'all' ? 'bg-[#fbfaf7] text-[#2f3437] shadow-sm' : 'text-[#6f706a] hover:text-[#2f3437]'
            }`}
            onClick={() => setActiveTab('all')}
          >
            全部错题
          </button>
          <button
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'need-review' ? 'bg-[#fbfaf7] text-[#2f3437] shadow-sm' : 'text-[#6f706a] hover:text-[#2f3437]'
            }`}
            onClick={() => setActiveTab('need-review')}
          >
            待复习
          </button>
        </div>
      </div>

      <div className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] shadow-sm">
        <div className="flex flex-col gap-4 p-4 md:flex-row">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#9a9b91]" />
            <Input
              placeholder="搜索题目内容或知识点..."
              value={searchTerm}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchTerm(e.target.value)}
              className="border-[#d8d2c4] bg-[#fffdf9] pl-10 text-[#2f3437] placeholder:text-[#9a9b91] focus-visible:ring-[#9aa898]"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={selectedSubject || ALL_SELECT_VALUE}
              onValueChange={(value) => {
                setSelectedSubject(value === ALL_SELECT_VALUE ? '' : value)
                setSelectedKnowledge('')
              }}
            >
              <SelectTrigger className="w-32 border-[#d8d2c4] bg-[#fffdf9] text-[#575d57]">
                <span>学科</span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_SELECT_VALUE}>全部</SelectItem>
                {subjectOptions.map(s => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedSubject && (
              <Select
                value={selectedKnowledge || ALL_SELECT_VALUE}
                onValueChange={(value) => setSelectedKnowledge(value === ALL_SELECT_VALUE ? '' : value)}
              >
                <SelectTrigger className="w-32 border-[#d8d2c4] bg-[#fffdf9] text-[#575d57]">
                  <span>知识点</span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_SELECT_VALUE}>全部</SelectItem>
                  {knowledgeOptions.map(k => (
                    <SelectItem key={k} value={k}>{k}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Select
              value={selectedErrorType || ALL_SELECT_VALUE}
              onValueChange={(value) => setSelectedErrorType(value === ALL_SELECT_VALUE ? '' : value)}
            >
              <SelectTrigger className="w-32 border-[#d8d2c4] bg-[#fffdf9] text-[#575d57]">
                <span>错误类型</span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_SELECT_VALUE}>全部</SelectItem>
                {Object.entries(ERROR_TYPE_MAP).map(([key, { label }]) => (
                  <SelectItem key={key} value={key}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={selectedDifficulty || ALL_SELECT_VALUE}
              onValueChange={(value) => setSelectedDifficulty(value === ALL_SELECT_VALUE ? '' : value)}
            >
              <SelectTrigger className="w-24 border-[#d8d2c4] bg-[#fffdf9] text-[#575d57]">
                <span>难度</span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_SELECT_VALUE}>全部</SelectItem>
                {Object.entries(DIFFICULTY_MAP).map(([key, { label }]) => (
                  <SelectItem key={key} value={key}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                className="text-[#6f706a] hover:bg-[#f0eee6] hover:text-[#2f3437]"
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
              <Button onClick={openAddDialog} className="gap-2 whitespace-nowrap bg-[#667c6d] text-white hover:bg-[#586d5f]">
                <Plus className="w-4 h-4" />
                添加错题
              </Button>
            </div>
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <RefreshCw className="h-6 w-6 animate-spin text-[#667c6d]" />
        </div>
      ) : filteredQuestions.length === 0 ? (
        <div className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-12 text-center shadow-sm">
          <BookMarked className="mx-auto mb-4 h-12 w-12 text-[#b8b0a2]" />
          <p className="text-[#7b817b]">暂无错题记录</p>
          <Button onClick={openAddDialog} className="mt-4 bg-[#667c6d] text-white hover:bg-[#586d5f]">
            添加第一道错题
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          {filteredQuestions.map((question) => (
            <div key={question.id} className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] shadow-sm transition hover:border-[#b8b0a2] hover:bg-[#fffdf9]">
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
                        <span key={tag} className="rounded border border-[#d8d2c4] bg-[#f3f0e8] px-2 py-0.5 text-xs text-[#6f706a]">{tag}</span>
                      ))}
                    </div>
                    <p className="mb-1 text-sm text-[#7b817b]">
                      <span className="font-medium">{question.subject}</span> - {question.knowledge_point}
                    </p>
                    <p className="mb-3 line-clamp-2 text-[#2f3437]">{question.question_content}</p>
                    <div className="flex items-center gap-4 text-sm text-[#7b817b]">
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
                    <Button variant="ghost" size="sm" onClick={() => { setSelectedQuestion(question); setAiAnalysis(null); setShowSimilar(false); setShowDetailDialog(true) }} className="text-[#6f706a] hover:bg-[#f0eee6] hover:text-[#2f3437]">
                      <Eye className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setEditingQuestion(question)} className="text-[#6f706a] hover:bg-[#f0eee6] hover:text-[#2f3437]">
                      <Edit2 className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => deleteMutation.mutate(question.id)} className="hover:bg-[#fbf4f2]">
                      <Trash2 className="h-4 w-4 text-[#b04e45]" />
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
        <DialogContent className="border-[#d8d2c4] bg-[#fbfaf7] text-[#2f3437]">
          <DialogHeader>
            <h2 className="flex items-center gap-2 text-xl font-bold text-[#2f3437]">
              <Plus className="h-5 w-5 text-[#667c6d]" />
              添加错题
            </h2>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* 智能导入区域 */}
            <div className="rounded-lg border border-[#d8cfe0] bg-[#f7f2f7] p-4">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-[#756583]">
                <Sparkles className="h-4 w-4 text-[#756583]" />
                智能导入（AI 自动识别题目并填充表单）
              </h3>

              {/* 文件上传 - 题目 */}
              <div className="space-y-2 mb-3">
                <label className="text-xs text-[#6f706a]">上传题目图片 / PDF / Word 文件</label>
                <div className="flex items-center gap-2">
                  <label className="flex-1 cursor-pointer">
                    <div className={`rounded-lg border-2 border-dashed p-3 text-center transition-colors ${formData.question_file ? 'border-[#9fb49f] bg-[#f2f7f2]' : 'border-[#d8d2c4] bg-[#fffdf9] hover:border-[#b9c6bb] hover:bg-[#f7f4ee]'}`}>
                      {uploadMutation.isPending ? (
                        <div className="flex items-center justify-center gap-2 text-sm text-[#5d7380]">
                          <RefreshCw className="w-4 h-4 animate-spin" />
                          AI 解析中...
                        </div>
                      ) : formData.question_file ? (
                        <div className="flex items-center justify-center gap-2 text-sm text-[#55745f]">
                          <CheckCircle2 className="w-4 h-4" />
                          题目文件已上传（AI 已自动填充）
                        </div>
                      ) : (
                        <div className="flex items-center justify-center gap-2 text-sm text-[#7b817b]">
                          <Upload className="w-4 h-4" />
                          点击上传题目文件
                        </div>
                      )}
                    </div>
                    <input
                      type="file"
                      className="hidden"
                      accept="image/*,.pdf,.docx,.txt"
                      onChange={(e) => handleFileUpload(e, 'question')}
                      disabled={uploadMutation.isPending}
                    />
                  </label>
                </div>
              </div>

              {/* 文件上传 - 答案 */}
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div className="space-y-2">
                  <label className="text-xs text-[#6f706a]">上传我的答案文件</label>
                  <label className="flex-1 cursor-pointer">
                    <div className={`rounded-lg border-2 border-dashed p-2 text-center transition-colors ${formData.user_answer_file ? 'border-[#9fb49f] bg-[#f2f7f2]' : 'border-[#d8d2c4] bg-[#fffdf9] hover:border-[#d8b8b1] hover:bg-[#fbf4f2]'}`}>
                      {formData.user_answer_file ? (
                        <div className="flex items-center justify-center gap-1 text-xs text-[#55745f]">
                          <CheckCircle2 className="w-3 h-3" />
                          已上传
                        </div>
                      ) : (
                        <div className="flex items-center justify-center gap-1 text-xs text-[#7b817b]">
                          <Upload className="w-3 h-3" />
                          我的答案
                        </div>
                      )}
                    </div>
                    <input
                      type="file"
                      className="hidden"
                      accept="image/*,.pdf,.docx,.txt"
                      onChange={(e) => handleFileUpload(e, 'user_answer')}
                      disabled={uploadMutation.isPending}
                    />
                  </label>
                </div>
                <div className="space-y-2">
                  <label className="text-xs text-[#6f706a]">上传正确答案文件</label>
                  <label className="flex-1 cursor-pointer">
                    <div className={`rounded-lg border-2 border-dashed p-2 text-center transition-colors ${formData.correct_answer_file ? 'border-[#9fb49f] bg-[#f2f7f2]' : 'border-[#d8d2c4] bg-[#fffdf9] hover:border-[#9fb49f] hover:bg-[#f2f7f2]'}`}>
                      {formData.correct_answer_file ? (
                        <div className="flex items-center justify-center gap-1 text-xs text-[#55745f]">
                          <CheckCircle2 className="w-3 h-3" />
                          已上传
                        </div>
                      ) : (
                        <div className="flex items-center justify-center gap-1 text-xs text-[#7b817b]">
                          <Upload className="w-3 h-3" />
                          正确答案
                        </div>
                      )}
                    </div>
                    <input
                      type="file"
                      className="hidden"
                      accept="image/*,.pdf,.docx,.txt"
                      onChange={(e) => handleFileUpload(e, 'correct_answer')}
                      disabled={uploadMutation.isPending}
                    />
                  </label>
                </div>
              </div>

              {uploadMutation.isError && (
                <p className="mb-2 text-xs text-[#b04e45]">
                  上传失败：{(uploadMutation.error as Error)?.message || '请重试'}
                </p>
              )}

              {uploadMutation.isSuccess && (
                <div className="mt-2 flex items-center gap-1 text-xs text-[#55745f]">
                  <CheckCircle2 className="w-3 h-3" />
                  AI 已自动填充表单，请检查并编辑后提交
                </div>
              )}
            </div>

            {/* 表单字段（受控组件，可被 AI 自动填充） */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">学科 *</label>
                <Input
                  value={formData.subject}
                  onChange={(e) => updateFormField('subject', e.target.value)}
                  required
                  placeholder="例如：数据结构、线性代数、数字电路"
                />
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">知识点 *</label>
                <Input
                  value={formData.knowledge_point}
                  onChange={(e) => updateFormField('knowledge_point', e.target.value)}
                  required
                  placeholder="输入知识点"
                />
              </div>
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 mb-1 block">题目内容 *</label>
              <Textarea
                value={formData.question_content}
                onChange={(e) => updateFormField('question_content', e.target.value)}
                required
                rows={4}
                placeholder="输入题目内容"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">我的答案 *</label>
                <Textarea
                  value={formData.user_answer}
                  onChange={(e) => updateFormField('user_answer', e.target.value)}
                  required
                  rows={2}
                  placeholder="输入你的答案"
                />
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">正确答案 *</label>
                <Textarea
                  value={formData.correct_answer}
                  onChange={(e) => updateFormField('correct_answer', e.target.value)}
                  required
                  rows={2}
                  placeholder="输入正确答案"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">错误类型 *</label>
                <select
                  value={formData.error_type}
                  onChange={(e) => updateFormField('error_type', e.target.value)}
                  required
                  className="w-full rounded-lg border border-[#d8d2c4] bg-[#fffdf9] px-3 py-2 text-[#2f3437] focus:outline-none focus:ring-2 focus:ring-[#9aa898]"
                >
                  {Object.entries(ERROR_TYPE_MAP).map(([key, { label }]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">难度</label>
                <select
                  value={formData.difficulty}
                  onChange={(e) => updateFormField('difficulty', e.target.value)}
                  className="w-full rounded-lg border border-[#d8d2c4] bg-[#fffdf9] px-3 py-2 text-[#2f3437] focus:outline-none focus:ring-2 focus:ring-[#9aa898]"
                >
                  {Object.entries(DIFFICULTY_MAP).map(([key, { label }]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 mb-1 block">错误分析</label>
              <Textarea
                value={formData.error_analysis}
                onChange={(e) => updateFormField('error_analysis', e.target.value)}
                rows={2}
                placeholder="分析错误原因"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700 mb-1 block">解题思路</label>
              <Textarea
                value={formData.solution}
                onChange={(e) => updateFormField('solution', e.target.value)}
                rows={2}
                placeholder="输入解题思路"
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setShowAddDialog(false)} className="text-[#6f706a] hover:bg-[#f0eee6] hover:text-[#2f3437]">取消</Button>
              <Button type="submit" disabled={addMutation.isPending} className="bg-[#667c6d] text-white hover:bg-[#586d5f]">
                {addMutation.isPending ? '添加中...' : '添加'}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {editingQuestion && (
        <Dialog open={!!editingQuestion} onOpenChange={(open: boolean) => !open && setEditingQuestion(null)}>
          <DialogContent className="border-[#d8d2c4] bg-[#fbfaf7] text-[#2f3437]">
            <DialogHeader>
              <h2 className="flex items-center gap-2 text-xl font-bold text-[#2f3437]">
                <Edit2 className="h-5 w-5 text-[#667c6d]" />
                编辑错题
              </h2>
            </DialogHeader>
            <form onSubmit={handleUpdate} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">学科</label>
                  <Input name="subject" defaultValue={editingQuestion.subject} placeholder="输入课程或学科名称" />
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
                  <select name="error_type" defaultValue={editingQuestion.error_type} className="w-full rounded-lg border border-[#d8d2c4] bg-[#fffdf9] px-3 py-2 text-[#2f3437] focus:outline-none focus:ring-2 focus:ring-[#9aa898]">
                    {Object.entries(ERROR_TYPE_MAP).map(([key, { label }]) => (
                      <option key={key} value={key}>{label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">难度</label>
                  <select name="difficulty" defaultValue={editingQuestion.difficulty} className="w-full rounded-lg border border-[#d8d2c4] bg-[#fffdf9] px-3 py-2 text-[#2f3437] focus:outline-none focus:ring-2 focus:ring-[#9aa898]">
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
                <Button type="button" variant="ghost" onClick={() => setEditingQuestion(null)} className="text-[#6f706a] hover:bg-[#f0eee6] hover:text-[#2f3437]">取消</Button>
                <Button type="submit" className="bg-[#667c6d] text-white hover:bg-[#586d5f]">保存</Button>
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
          <DialogContent className="border-[#d8d2c4] bg-[#fbfaf7] text-[#2f3437]">
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

              <div className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-4">
                <h3 className="text-sm font-medium text-gray-700 mb-2">题目内容</h3>
                <p className="text-gray-900 whitespace-pre-wrap">{selectedQuestion.question_content}</p>
                {selectedQuestion.question_file && (
                  <WrongQuestionFilePreview
                    alt="题目原图"
                    filePath={selectedQuestion.question_file}
                    maxHeightClass="max-h-96"
                  />
                )}
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="rounded-lg border border-[#e2cbc6] bg-[#fbf4f2] p-4">
                  <h3 className="mb-2 text-sm font-medium text-[#8f5f57]">我的答案</h3>
                  <p className="text-gray-900 whitespace-pre-wrap">{selectedQuestion.user_answer}</p>
                  {selectedQuestion.user_answer_file && (
                    <WrongQuestionFilePreview
                      alt="我的答案原图"
                      filePath={selectedQuestion.user_answer_file}
                      maxHeightClass="max-h-64"
                    />
                  )}
                </div>
                <div className="rounded-lg border border-[#cbdccf] bg-[#f2f7f2] p-4">
                  <h3 className="mb-2 text-sm font-medium text-[#55745f]">正确答案</h3>
                  <p className="text-gray-900 whitespace-pre-wrap">{selectedQuestion.correct_answer}</p>
                  {selectedQuestion.correct_answer_file && (
                    <WrongQuestionFilePreview
                      alt="正确答案原图"
                      filePath={selectedQuestion.correct_answer_file}
                      maxHeightClass="max-h-64"
                    />
                  )}
                </div>
              </div>

              {selectedErrorAnalysis && (
                <div className="rounded-lg border border-[#e6dcc2] bg-[#fbf7eb] p-4">
                  <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#8b7041]">
                    <AlertCircle className="w-4 h-4" />
                    错误分析
                  </h3>
                  <p className="text-gray-900 whitespace-pre-wrap break-words">{selectedErrorAnalysis}</p>
                </div>
              )}

              {selectedSolution && (
                <div className="rounded-lg border border-[#cfd9df] bg-[#f0f5f6] p-4">
                  <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#5d7380]">
                    <Brain className="w-4 h-4" />
                    解题思路
                  </h3>
                  <p className="text-gray-900 whitespace-pre-wrap break-words">{selectedSolution}</p>
                </div>
              )}

              {/* AI 智能分析区域 */}
              <div className="rounded-lg border border-[#d8cfe0] bg-[#f7f2f7] p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="flex items-center gap-2 text-sm font-medium text-[#756583]">
                    <Sparkles className="w-4 h-4" />
                    AI 智能分析
                  </h3>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => analyzeMutation.mutate(selectedQuestion.id)}
                    disabled={analyzeMutation.isPending}
                    className="gap-1"
                  >
                    {analyzeMutation.isPending ? (
                      <><RefreshCw className="w-3 h-3 animate-spin" /> 分析中...</>
                    ) : (
                      <><Sparkles className="w-3 h-3" /> {aiAnalysis ? '重新分析' : '开始分析'}</>
                    )}
                  </Button>
                </div>

                {analyzeMutation.isError && (
                  <p className="mt-2 text-sm text-[#b04e45]">
                    分析失败：{(analyzeMutation.error as Error)?.message || '请检查 LLM 配置后重试'}
                  </p>
                )}

                {aiAnalysis && (
                  <div className="space-y-3 mt-3">
                    {aiAnalysis.knowledge_points.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-gray-600 mb-1">识别知识点</p>
                        <div className="flex flex-wrap gap-1">
                          {aiAnalysis.knowledge_points.map((kp, i) => (
                            <Badge key={i} className="border-transparent bg-[#e8deee] text-[#756583]">{kp}</Badge>
                          ))}
                        </div>
                      </div>
                    )}
                    {aiErrorReason && (
                      <div>
                        <p className="text-xs font-medium text-gray-600 mb-1">错误原因</p>
                        <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">{aiErrorReason}</p>
                      </div>
                    )}
                    {aiSolution && (
                      <div>
                        <p className="text-xs font-medium text-gray-600 mb-1">详细解析</p>
                        <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">{aiSolution}</p>
                      </div>
                    )}
                    {aiAnalysis.related_concepts.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-gray-600 mb-1">相关概念</p>
                        <div className="flex flex-wrap gap-1">
                          {aiAnalysis.related_concepts.map((c, i) => (
                            <span key={i} className="rounded border border-[#d8d2c4] bg-[#f3f0e8] px-2 py-0.5 text-xs text-[#6f706a]">{c}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    {aiAnalysis.difficulty_suggestion && (
                      <div className="flex items-center gap-2 text-xs">
                        <Zap className="w-3 h-3 text-amber-500" />
                        <span className="text-gray-600">难度建议：</span>
                        <Badge className="bg-amber-100 text-amber-700">{aiAnalysis.difficulty_suggestion}</Badge>
                      </div>
                    )}
                  </div>
                )}
                {!aiAnalysis && !analyzeMutation.isPending && !analyzeMutation.isError && (
                  <p className="text-sm text-gray-500 mt-1">点击"开始分析"让 AI 识别知识点、分析错误原因并生成详细解析</p>
                )}
              </div>

              {/* 相似题推荐区域 */}
              <div className="rounded-lg border border-[#cbd7df] bg-[#f1f5f7] p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="flex items-center gap-2 text-sm font-medium text-[#5d7380]">
                    <BookOpen className="w-4 h-4" />
                    相似练习题
                  </h3>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setShowSimilar(!showSimilar)
                        if (!showSimilar) {
                          queryClient.invalidateQueries({ queryKey: ['similar-questions', selectedQuestion.id] })
                        }
                      }}
                      className="gap-1"
                    >
                      {showSimilar ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                      {showSimilar ? '收起' : '展开'}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => generateSimilarMutation.mutate(selectedQuestion.id)}
                      disabled={generateSimilarMutation.isPending}
                      className="gap-1"
                    >
                      {generateSimilarMutation.isPending ? (
                        <><RefreshCw className="w-3 h-3 animate-spin" /> 生成中...</>
                      ) : (
                        <><Plus className="w-3 h-3" /> 生成新题</>
                      )}
                    </Button>
                  </div>
                </div>

                {generateSimilarMutation.isError && (
                  <p className="mt-2 text-sm text-[#b04e45]">
                    生成失败：{(generateSimilarMutation.error as Error)?.message || '请重试'}
                  </p>
                )}

                {showSimilar && (
                  <div className="space-y-3 mt-3">
                    {visibleSimilarQuestions.length > 0 ? (
                      visibleSimilarQuestions.map((sq: SimilarQuestion) => (
                        <div key={sq.id} className="rounded-lg border border-[#d8d2c4] bg-[#fffdf9] p-3">
                          <p className="text-sm text-gray-900 mb-2">{sq.question_content}</p>
                          {sq.options && (
                            <div className="space-y-1 mb-2">
                              {sq.options.map((opt, i) => (
                                <p key={i} className="text-xs text-gray-600">{String.fromCharCode(65 + i)}. {opt}</p>
                              ))}
                            </div>
                          )}
                          <div className="mb-1 rounded-md bg-[#f2f7f2] p-2">
                            <p className="text-xs font-medium text-[#55745f]">正确答案</p>
                            <p className="text-sm text-gray-900">{sq.correct_answer}</p>
                          </div>
                          {sq.explanation && (
                            <div className="rounded-md bg-[#f0f5f6] p-2">
                              <p className="text-xs font-medium text-[#5d7380]">解析</p>
                              <p className="text-sm text-gray-900">{sq.explanation}</p>
                            </div>
                          )}
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-gray-500">暂无相似题，点击"生成新题"让 AI 创建一道相似练习题</p>
                    )}
                  </div>
                )}
              </div>

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
                <Button onClick={handleReview} disabled={!reviewAnswer.trim()} className="w-full bg-[#667c6d] text-white hover:bg-[#586d5f]">
                  提交复习
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}

      {/* 分析报告对话框 */}
      {showReport && (
        <Dialog open={showReport} onOpenChange={setShowReport}>
          <DialogContent className="border-[#d8d2c4] bg-[#fbfaf7] text-[#2f3437]">
            <DialogHeader>
              <div className="flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-xl font-bold text-[#2f3437]">
                  <FileText className="h-5 w-5 text-[#667c6d]" />
                  错题分析研究报告
                </h2>
                <div className="flex items-center gap-2">
                  <Select
                    value={reportSubject || ALL_SELECT_VALUE}
                    onValueChange={(value) => {
                      const nextSubject = value === ALL_SELECT_VALUE ? '' : value
                      setReportSubject(nextSubject)
                      reportMutation.mutate(nextSubject || undefined)
                    }}
                  >
                    <SelectTrigger className="w-32 border-[#d8d2c4] bg-[#fffdf9] text-[#575d57]">
                      <SelectValue placeholder="全部学科" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={ALL_SELECT_VALUE}>全部学科</SelectItem>
                      {subjectOptions.map(s => (
                        <SelectItem key={s} value={s}>{s}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button size="sm" variant="outline" onClick={() => reportMutation.mutate(reportSubject || undefined)} disabled={reportMutation.isPending} className="gap-1 border-[#d8d2c4] bg-[#fffdf9] text-[#575d57] hover:bg-[#f0eee6]">
                    <RefreshCw className={`w-3 h-3 ${reportMutation.isPending ? 'animate-spin' : ''}`} />
                    刷新
                  </Button>
                </div>
              </div>
            </DialogHeader>
            {reportMutation.isPending ? (
              <div className="flex items-center justify-center py-12">
                <RefreshCw className="h-6 w-6 animate-spin text-[#667c6d]" />
                <span className="ml-2 text-[#7b817b]">AI 正在生成分析报告...</span>
              </div>
            ) : reportMutation.isError ? (
              <div className="py-8 text-center">
                <AlertCircle className="mx-auto mb-4 h-12 w-12 text-[#d8b8b1]" />
                <p className="text-[#b04e45]">报告生成失败</p>
                <p className="text-sm text-gray-500 mt-1">{(reportMutation.error as Error)?.message || '请检查 LLM 配置后重试'}</p>
              </div>
            ) : reportMutation.data ? (
              <div className="space-y-4">
                {/* 统计概览 */}
                <div className="grid grid-cols-4 gap-3">
                  <div className="rounded-lg border border-[#cfd9df] bg-[#f0f5f6] p-3 text-center">
                    <p className="text-2xl font-bold text-[#5d7380]">{reportMutation.data.total_questions}</p>
                    <p className="text-xs text-gray-500">错题总数</p>
                  </div>
                  <div className="rounded-lg border border-[#d8cfe0] bg-[#f7f2f7] p-3 text-center">
                    <p className="text-2xl font-bold text-[#756583]">{reportMutation.data.total_reviews}</p>
                    <p className="text-xs text-gray-500">复习次数</p>
                  </div>
                  <div className="rounded-lg border border-[#cbdccf] bg-[#f2f7f2] p-3 text-center">
                    <p className="text-2xl font-bold text-[#55745f]">{reportMutation.data.accuracy}%</p>
                    <p className="text-xs text-gray-500">复习正确率</p>
                  </div>
                  <div className="rounded-lg border border-[#e6dcc2] bg-[#fbf7eb] p-3 text-center">
                    <p className="text-2xl font-bold text-[#8b7041]">{Object.keys(reportMutation.data.by_knowledge).length}</p>
                    <p className="text-xs text-gray-500">涉及知识点</p>
                  </div>
                </div>

                {/* 总体概况 */}
                {reportMutation.data.report.summary && (
                  <div className="rounded-lg border border-[#d8d2c4] bg-[#fbfaf7] p-4">
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#575d57]">
                      <FileText className="w-4 h-4" />
                      总体概况
                    </h3>
                    <p className="text-sm text-gray-900 whitespace-pre-wrap">{reportMutation.data.report.summary}</p>
                  </div>
                )}

                {/* 薄弱知识点 */}
                {reportMutation.data.report.weak_points.length > 0 && (
                  <div className="rounded-lg border border-[#e2cbc6] bg-[#fbf4f2] p-4">
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#8f5f57]">
                      <AlertCircle className="w-4 h-4" />
                      薄弱知识点
                    </h3>
                    <ul className="space-y-1">
                      {reportMutation.data.report.weak_points.map((point, i) => (
                        <li key={i} className="text-sm text-gray-900 flex items-start gap-2">
                          <span className="mt-0.5 text-[#b04e45]">•</span>
                          {point}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 错误模式 */}
                {reportMutation.data.report.error_patterns.length > 0 && (
                  <div className="rounded-lg border border-[#e6dcc2] bg-[#fbf7eb] p-4">
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#8b7041]">
                      <AlertCircle className="w-4 h-4" />
                      错误模式分析
                    </h3>
                    <ul className="space-y-1">
                      {reportMutation.data.report.error_patterns.map((pattern, i) => (
                        <li key={i} className="text-sm text-gray-900 flex items-start gap-2">
                          <span className="mt-0.5 text-[#8b7041]">•</span>
                          {pattern}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 改进建议 */}
                {reportMutation.data.report.suggestions.length > 0 && (
                  <div className="rounded-lg border border-[#cbdccf] bg-[#f2f7f2] p-4">
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#55745f]">
                      <Zap className="w-4 h-4" />
                      改进建议
                    </h3>
                    <ul className="space-y-1">
                      {reportMutation.data.report.suggestions.map((suggestion, i) => (
                        <li key={i} className="text-sm text-gray-900 flex items-start gap-2">
                          <CheckCircle2 className="mt-1 h-3 w-3 flex-shrink-0 text-[#55745f]" />
                          {suggestion}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 复习计划 */}
                {reportMutation.data.report.review_plan && (
                  <div className="rounded-lg border border-[#cfd9df] bg-[#f0f5f6] p-4">
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-[#5d7380]">
                      <Target className="w-4 h-4" />
                      复习计划建议
                    </h3>
                    <p className="text-sm text-gray-900 whitespace-pre-wrap">{reportMutation.data.report.review_plan}</p>
                  </div>
                )}
              </div>
            ) : null}
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}
