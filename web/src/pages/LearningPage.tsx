import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Brain,
  CalendarDays,
  CheckCircle2,
  Clock3,
  GraduationCap,
  RefreshCw,
  RotateCw,
  Send,
  Sparkles,
  Target,
  UserRound,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type {
  LearningGoal,
  LearningProfile,
  LearningRequirementAnalysis,
  LearningRequirementDraft,
  LearningRequirementFinalizeResponse,
  LearningRequirementQuestion,
  LearningStats,
  LearningTask,
  LearningTodayResponse,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";

type BadgeTone = "success" | "warning" | "destructive" | "secondary" | "outline";
type DraftField = keyof LearningRequirementDraft;

interface DraftFieldSpec {
  field: DraftField;
  label: string;
  type: "text" | "number" | "select";
  options?: string[];
  placeholder?: string;
}

const CONTROL_CLASS =
  "h-9 w-full rounded-md border border-border bg-background/60 px-3 text-sm outline-none transition focus:border-primary/70";

const EMPTY_PROFILE: LearningProfile = {
  nickname: "",
  school: "",
  major: "",
  grade: "",
  semester: "",
  career_target: "",
  baseline_level: "",
  weekday_minutes: null,
  weekend_minutes: null,
  preferred_study_time: "",
  learning_preferences: [],
  current_courses: [],
  strengths: "",
  weaknesses: "",
  constraints: "",
  updated_at: null,
  complete: false,
  missing_fields: [],
  completeness: 0,
};

const REQUIRED_PROFILE_LABELS: Record<string, string> = {
  major: "专业",
  grade: "年级",
  career_target: "去向",
  baseline_level: "当前基础",
  weekday_minutes: "工作日时间",
  weekend_minutes: "周末时间",
  learning_preferences: "学习偏好",
};

const EXAMPLES = [
  "半年学会 Java 找工作",
  "三个月准备英语六级，每天 90 分钟，偏好刷题",
  "零基础学 Python 数据分析，想做作品集",
];

const DRAFT_FIELDS: DraftFieldSpec[] = [
  { field: "goal_name", label: "目标", type: "text", placeholder: "Java 后端开发" },
  {
    field: "purpose",
    label: "用途",
    type: "select",
    options: ["找工作", "转行", "考试/考证", "完成项目", "提升工作能力", "兴趣探索"],
  },
  {
    field: "current_level",
    label: "基础",
    type: "select",
    options: ["零基础", "入门", "初级", "中级", "高级"],
  },
  {
    field: "target_level",
    label: "目标水平",
    type: "select",
    options: ["能独立完成项目", "达到面试/就业水平", "通过考试", "系统掌握", "快速了解"],
  },
  { field: "daily_minutes", label: "每日分钟", type: "number", placeholder: "90" },
  {
    field: "timeframe",
    label: "周期",
    type: "select",
    options: ["1个月", "3个月", "半年", "1年"],
  },
  {
    field: "preference",
    label: "偏好",
    type: "select",
    options: ["项目实战", "视频课程", "文档阅读", "刷题训练", "AI 陪练"],
  },
  {
    field: "goal_type",
    label: "类型",
    type: "select",
    options: ["skill", "knowledge", "certification", "project"],
  },
];

function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString();
}

function statusTone(status: string): BadgeTone {
  if (status === "completed") return "success";
  if (status === "active" || status === "in_progress") return "warning";
  if (status === "paused" || status === "skipped") return "outline";
  if (status === "abandoned") return "destructive";
  return "secondary";
}

function fitTone(fit: string): BadgeTone {
  if (fit === "comfortable") return "success";
  if (fit === "tight") return "warning";
  if (fit === "at_risk") return "destructive";
  return "outline";
}

function fitLabel(fit: string): string {
  if (fit === "comfortable") return "节奏充足";
  if (fit === "tight") return "节奏偏紧";
  if (fit === "at_risk") return "风险较高";
  return "待补充";
}

function draftValue(draft: LearningRequirementDraft | undefined, field: DraftField): string {
  const value = draft?.[field];
  if (value === undefined || value === null) return "";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function questionToSpec(question: LearningRequirementQuestion): DraftFieldSpec {
  return {
    field: question.field as DraftField,
    label: question.label,
    type: question.type === "number" ? "number" : question.options?.length ? "select" : "text",
    options: question.options,
    placeholder: question.placeholder,
  };
}

function FieldControl({
  spec,
  value,
  onChange,
}: {
  spec: DraftFieldSpec;
  value: string;
  onChange: (value: string) => void;
}) {
  if (spec.type === "select" && spec.options?.length) {
    return (
      <select
        className={CONTROL_CLASS}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">-</option>
        {spec.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  return (
    <Input
      type={spec.type === "number" ? "number" : "text"}
      min={spec.type === "number" ? 1 : undefined}
      value={value}
      placeholder={spec.placeholder}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background/50 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-lg font-semibold text-midground">{value}</div>
    </div>
  );
}

export default function LearningPage() {
  const { toast, showToast } = useToast();
  const { setAfterTitle, setEnd, setTitle } = usePageHeader();
  const navigate = useNavigate();
  const [message, setMessage] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [analysis, setAnalysis] = useState<LearningRequirementAnalysis | null>(null);
  const [finalized, setFinalized] = useState<LearningRequirementFinalizeResponse | null>(null);
  const [profile, setProfile] = useState<LearningProfile>(EMPTY_PROFILE);
  const [goals, setGoals] = useState<LearningGoal[]>([]);
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [today, setToday] = useState<LearningTodayResponse | null>(null);
  const [loadingOverview, setLoadingOverview] = useState(true);
  const [loading, setLoading] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const loadOverview = useCallback(async () => {
    setRefreshing(true);
    try {
      const [nextProfile, nextGoals, nextStats, nextToday] = await Promise.all([
        api.getLearningProfile(),
        api.getLearningGoals(),
        api.getLearningStats(),
        api.getLearningToday(),
      ]);
      setProfile({ ...EMPTY_PROFILE, ...nextProfile });
      setGoals(nextGoals);
      setStats(nextStats);
      setToday(nextToday);
    } catch (error) {
      showToast(`学习数据加载失败：${error}`, "error");
    } finally {
      setLoadingOverview(false);
      setRefreshing(false);
    }
  }, [showToast]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    setTitle("HermesLearn");
    setAfterTitle(
      <Badge tone={profile.complete ? "success" : "warning"}>
        {profile.complete ? "可使用" : "需要个人信息"}
      </Badge>,
    );
    setEnd(
      <div className="flex items-center gap-2">
        <Button
          ghost
          size="sm"
          type="button"
          onClick={() => navigate("/student-profile")}
          prefix={<UserRound />}
        >
          修改个人信息
        </Button>
        <Button
          ghost
          size="icon"
          type="button"
          onClick={() => void loadOverview()}
          disabled={refreshing}
          aria-label="Refresh learning data"
          title="Refresh"
        >
          {refreshing ? <Spinner /> : <RefreshCw />}
        </Button>
      </div>,
    );
    return () => {
      setTitle(null);
      setAfterTitle(null);
      setEnd(null);
    };
  }, [loadOverview, navigate, profile.complete, refreshing, setAfterTitle, setEnd, setTitle]);

  const activeGoals = useMemo(
    () => goals.filter((goal) => goal.status === "active").slice(0, 5),
    [goals],
  );
  const profileMissingFields = profile.missing_fields ?? [];
  const answeredDraft = analysis?.draft;
  const requirementSessionId = analysis?.session_id;
  const missingSet = useMemo(
    () => new Set(analysis?.missing_fields ?? []),
    [analysis?.missing_fields],
  );

  const setDraftAnswer = useCallback((field: DraftField, value: string) => {
    setAnswers((prev) => ({ ...prev, [field]: value }));
    setAnalysis((prev) => {
      if (!prev) return prev;
      const nextValue = field === "daily_minutes" ? Number(value) || undefined : value;
      return { ...prev, draft: { ...prev.draft, [field]: nextValue } };
    });
  }, []);

  const runAnalyze = useCallback(async () => {
    if (!profile.complete) {
      navigate("/student-profile");
      return null;
    }
    const trimmed = message.trim();
    if (!trimmed && !requirementSessionId) {
      showToast("请先输入学习需求", "error");
      return null;
    }
    setLoading(true);
    setFinalized(null);
    try {
      const result = await api.analyzeLearningRequirement({
        message: trimmed,
        session_id: requirementSessionId,
        answers,
      });
      setAnalysis(result);
      return result;
    } catch (error) {
      showToast(`需求分析失败：${error}`, "error");
      return null;
    } finally {
      setLoading(false);
    }
  }, [answers, message, navigate, profile.complete, requirementSessionId, showToast]);

  const finalizeRequirement = useCallback(async () => {
    const latest = await runAnalyze();
    if (!latest) return;
    if (!latest.ready) {
      showToast("还有信息需要补充", "error");
      return;
    }
    setFinalizing(true);
    try {
      const result = await api.finalizeLearningRequirement({
        session_id: latest.session_id,
        draft: latest.draft,
      });
      setFinalized(result);
      setAnswers({});
      showToast(result.message, "success");
      await loadOverview();
    } catch (error) {
      showToast(`生成失败：${error}`, "error");
    } finally {
      setFinalizing(false);
    }
  }, [loadOverview, runAnalyze, showToast]);

  const resetRequirement = useCallback(() => {
    setMessage("");
    setAnswers({});
    setAnalysis(null);
    setFinalized(null);
  }, []);

  const completeTask = useCallback(
    async (task: LearningTask) => {
      try {
        await api.updateLearningTaskStatus(task.id, "completed");
        showToast("任务已完成", "success");
        await loadOverview();
      } catch (error) {
        showToast(`更新失败：${error}`, "error");
      }
    },
    [loadOverview, showToast],
  );

  if (loadingOverview) {
    return (
      <div className="flex min-h-80 w-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (!profile.complete) {
    return (
      <div className="w-full min-w-0 space-y-5 p-4 sm:p-6">
        <Toast toast={toast} />
        <PluginSlot name="learning:top" />
        <div className="mx-auto max-w-3xl rounded-md border border-border bg-background/40 p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
              <UserRound className="h-4 w-4" />
              个人信息未完成
            </H2>
            <Badge tone="warning">
              {Math.round((profile.completeness ?? 0) * 100)}%
            </Badge>
          </div>
          <div className="grid gap-4">
            <div className="rounded-md border border-border bg-background/50 p-3">
              <div className="text-sm font-medium text-midground">
                填写后才能使用智能体
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {profileMissingFields.map((field) => (
                  <Badge key={field} tone="warning">
                    {REQUIRED_PROFILE_LABELS[field] ?? field}
                  </Badge>
                ))}
              </div>
            </div>
            <Button
              type="button"
              onClick={() => navigate("/student-profile")}
              prefix={<GraduationCap />}
            >
              去填写个人信息
            </Button>
          </div>
        </div>
        <PluginSlot name="learning:bottom" />
      </div>
    );
  }

  return (
    <div className="w-full min-w-0 space-y-5 p-4 sm:p-6">
      <Toast toast={toast} />
      <PluginSlot name="learning:top" />

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <div className="min-w-0 rounded-md border border-border bg-background/40 p-4">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
              <Brain className="h-4 w-4" />
              需求获取
            </H2>
            <div className="flex items-center gap-2">
              <Badge tone={analysis?.ready ? "success" : "outline"}>
                {analysis?.ready ? "可生成" : "待分析"}
              </Badge>
              {analysis?.classification_label ? (
                <Badge tone="secondary">{analysis.classification_label}</Badge>
              ) : null}
            </div>
          </div>

          <div className="grid gap-3">
            <Label htmlFor="learning-requirement">自然语言需求</Label>
            <textarea
              id="learning-requirement"
              className="min-h-28 w-full resize-y rounded-md border border-border bg-background/60 px-3 py-2 text-sm outline-none transition focus:border-primary/70"
              value={message}
              placeholder="半年学会 Java 找工作，每天 2 小时，偏好项目实战"
              onChange={(event) => setMessage(event.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((example) => (
                <Button
                  key={example}
                  ghost
                  size="sm"
                  type="button"
                  onClick={() => {
                    setMessage(example);
                    setAnswers({});
                    setAnalysis(null);
                    setFinalized(null);
                  }}
                >
                  {example}
                </Button>
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                onClick={() => void runAnalyze()}
                disabled={loading}
                prefix={loading ? <Spinner /> : <Sparkles />}
              >
                分析需求
              </Button>
              <Button
                ghost
                type="button"
                onClick={resetRequirement}
                prefix={<RotateCw />}
              >
                重置
              </Button>
            </div>
          </div>

          {analysis ? (
            <div className="mt-5 grid gap-4">
              <div className="rounded-md border border-border bg-background/30 p-3">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
                    <Target className="h-4 w-4" />
                    目标草稿
                  </H2>
                  <Badge tone={analysis.ready ? "success" : "warning"}>
                    缺 {analysis.missing_fields.length} 项
                  </Badge>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {DRAFT_FIELDS.map((spec) => (
                    <div key={spec.field} className="grid gap-1.5">
                      <Label className="flex items-center justify-between gap-2 text-xs">
                        <span>{spec.label}</span>
                        {missingSet.has(spec.field) ? (
                          <span className="text-destructive">必填</span>
                        ) : null}
                      </Label>
                      <FieldControl
                        spec={spec}
                        value={answers[spec.field] ?? draftValue(answeredDraft, spec.field)}
                        onChange={(value) => setDraftAnswer(spec.field, value)}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                <div className="rounded-md border border-border bg-background/30 p-3">
                  <H2 variant="sm" className="mb-3 flex items-center gap-2 text-muted-foreground">
                    <Send className="h-4 w-4" />
                    追问
                  </H2>
                  <div className="grid gap-3">
                    {analysis.questions.length === 0 ? (
                      <div className="rounded-md border border-success/40 bg-success/10 p-3 text-sm text-success">
                        信息完整，可以生成学习目标与计划。
                      </div>
                    ) : (
                      analysis.questions.map((question) => {
                        const spec = questionToSpec(question);
                        return (
                          <div key={question.field} className="grid gap-1.5">
                            <Label className="text-xs">{question.question}</Label>
                            <FieldControl
                              spec={spec}
                              value={answers[spec.field] ?? draftValue(answeredDraft, spec.field)}
                              onChange={(value) => setDraftAnswer(spec.field, value)}
                            />
                          </div>
                        );
                      })
                    )}
                    <Button
                      type="button"
                      onClick={() => void finalizeRequirement()}
                      disabled={loading || finalizing}
                      prefix={finalizing ? <Spinner /> : <GraduationCap />}
                    >
                      生成目标与计划
                    </Button>
                  </div>
                </div>

                <div className="rounded-md border border-border bg-background/30 p-3">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
                      <Clock3 className="h-4 w-4" />
                      预估
                    </H2>
                    <Badge tone={fitTone(analysis.estimate.fit)}>
                      {fitLabel(analysis.estimate.fit)}
                    </Badge>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-4">
                    <Metric label="预计小时" value={`${analysis.estimate.estimated_hours}`} />
                    <Metric
                      label="预计天数"
                      value={analysis.estimate.estimated_days ? `${analysis.estimate.estimated_days}` : "-"}
                    />
                    <Metric
                      label="可用小时"
                      value={analysis.estimate.available_hours ? `${analysis.estimate.available_hours}` : "-"}
                    />
                    <Metric
                      label="把握"
                      value={`${Math.round(analysis.estimate.confidence * 100)}%`}
                    />
                  </div>
                  <div className="mt-3 grid gap-2">
                    {analysis.estimate.phases.map((phase) => (
                      <div
                        key={phase.name}
                        className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 rounded-md border border-border/70 bg-background/40 px-3 py-2 text-sm"
                      >
                        <span className="truncate">{phase.name}</span>
                        <span className="text-muted-foreground">{phase.hours}h</span>
                        <span className="text-muted-foreground">
                          {phase.days ? `${phase.days}天` : "-"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {analysis.related_goals.length > 0 ? (
                <div className="rounded-md border border-border bg-background/30 p-3">
                  <H2 variant="sm" className="mb-3 text-muted-foreground">
                    关联目标
                  </H2>
                  <div className="grid gap-2 md:grid-cols-2">
                    {analysis.related_goals.map((goal) => (
                      <button
                        key={goal.id}
                        type="button"
                        className="rounded-md border border-border bg-background/50 p-3 text-left text-sm transition hover:border-primary/60"
                        onClick={() => setDraftAnswer("existing_goal_id", goal.id)}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="min-w-0 truncate font-medium">{goal.name}</span>
                          <Badge tone={statusTone(goal.status)}>{goal.status}</Badge>
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {goal.progress}% complete
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        <aside className="min-w-0 space-y-4">
          <div className="rounded-md border border-border bg-background/40 p-4">
            <H2 variant="sm" className="mb-3 flex items-center gap-2 text-muted-foreground">
              <UserRound className="h-4 w-4" />
              当前学生
            </H2>
            <div className="grid gap-2">
              <Metric label="专业" value={profile.major || "-"} />
              <Metric label="年级" value={profile.grade || "-"} />
              <Metric label="去向" value={profile.career_target || "-"} />
              <Metric label="基础" value={profile.baseline_level || "-"} />
            </div>
          </div>

          <div className="rounded-md border border-border bg-background/40 p-4">
            <H2 variant="sm" className="mb-3 flex items-center gap-2 text-muted-foreground">
              <Sparkles className="h-4 w-4" />
              学习概览
            </H2>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
              <Metric label="活跃目标" value={`${stats?.active_goals ?? 0}`} />
              <Metric label="今日任务" value={`${stats?.today_completed ?? 0}/${stats?.today_tasks ?? 0}`} />
              <Metric label="已学小时" value={`${stats?.total_hours_learned ?? 0}`} />
              <Metric label="已完成任务" value={`${stats?.total_tasks_completed ?? 0}`} />
            </div>
          </div>

          <div className="rounded-md border border-border bg-background/40 p-4">
            <H2 variant="sm" className="mb-3 flex items-center gap-2 text-muted-foreground">
              <Target className="h-4 w-4" />
              活跃目标
            </H2>
            <div className="grid gap-2">
              {activeGoals.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无活跃目标。</p>
              ) : (
                activeGoals.map((goal) => (
                  <Card key={goal.id}>
                    <CardContent className="p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="min-w-0 truncate text-sm font-medium">
                          {goal.name}
                        </span>
                        <Badge tone={statusTone(goal.status)}>{goal.status}</Badge>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary"
                          style={{ width: `${Math.max(0, Math.min(100, goal.progress || 0))}%` }}
                        />
                      </div>
                      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
                        <span>{goal.estimated_hours}h</span>
                        <span>{formatDate(goal.target_date)}</span>
                      </div>
                    </CardContent>
                  </Card>
                ))
              )}
            </div>
          </div>

          <div className="rounded-md border border-border bg-background/40 p-4">
            <H2 variant="sm" className="mb-3 flex items-center gap-2 text-muted-foreground">
              <CalendarDays className="h-4 w-4" />
              今日任务
            </H2>
            <div className="grid gap-2">
              {(today?.tasks ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">今天暂无学习任务。</p>
              ) : (
                today?.tasks.map((task) => (
                  <div
                    key={task.id}
                    className={cn(
                      "rounded-md border border-border bg-background/50 p-3",
                      task.status === "completed" && "opacity-60",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{task.title}</div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {today.goal_names[task.goal_id] ?? "学习目标"} · {formatMinutes(task.estimated_minutes)}
                        </div>
                      </div>
                      <Button
                        ghost
                        size="icon"
                        type="button"
                        title="Complete"
                        aria-label="Complete task"
                        disabled={task.status === "completed"}
                        onClick={() => void completeTask(task)}
                      >
                        <CheckCircle2 />
                      </Button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </aside>
      </section>

      {finalized ? (
        <div className="rounded-md border border-success/40 bg-success/10 p-4 text-sm text-success">
          {finalized.goal_created ? "已创建新目标" : "已补充到已有目标"}：
          {finalized.goal.name}，生成 {finalized.plans.length} 个阶段、
          {finalized.tasks.length} 个任务。
        </div>
      ) : null}

      <PluginSlot name="learning:bottom" />
    </div>
  );
}
