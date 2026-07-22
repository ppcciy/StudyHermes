import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  BookOpen,
  CalendarClock,
  GraduationCap,
  Save,
  Target,
  UserRound,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type { LearningProfile } from "@/lib/api";
import { PluginSlot } from "@/plugins";

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

const GRADE_OPTIONS = ["大一", "大二", "大三", "大四", "研一", "研二", "研三"];
const CAREER_TARGET_OPTIONS = ["就业", "实习", "考研", "保研", "考公", "留学", "竞赛", "转专业"];
const LEVEL_OPTIONS = ["零基础", "入门", "初级", "中级", "高级"];
const PREFERENCE_OPTIONS = ["项目实战", "视频课程", "文档阅读", "刷题训练", "AI 陪练"];

const REQUIRED_LABELS: Record<string, string> = {
  major: "专业",
  grade: "年级",
  career_target: "去向",
  baseline_level: "当前基础",
  weekday_minutes: "工作日时间",
  weekend_minutes: "周末时间",
  learning_preferences: "学习偏好",
};

function splitListInput(value: string): string[] {
  return value
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function localMissingFields(profile: LearningProfile): string[] {
  const missing: string[] = [];
  if (!profile.major.trim()) missing.push("major");
  if (!profile.grade.trim()) missing.push("grade");
  if (!profile.career_target.trim()) missing.push("career_target");
  if (!profile.baseline_level.trim()) missing.push("baseline_level");
  if (profile.weekday_minutes === null) missing.push("weekday_minutes");
  if (profile.weekend_minutes === null) missing.push("weekend_minutes");
  if (profile.learning_preferences.length === 0) missing.push("learning_preferences");
  return missing;
}

function NumberInput({
  label,
  value,
  onChange,
  placeholder,
  required,
}: {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  placeholder: string;
  required?: boolean;
}) {
  return (
    <div className="grid gap-1.5">
      <Label className="flex items-center justify-between gap-2 text-xs">
        <span>{label}</span>
        {required ? <span className="text-destructive">必填</span> : null}
      </Label>
      <Input
        type="number"
        min={0}
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(event) =>
          onChange(event.target.value ? Number(event.target.value) : null)
        }
      />
    </div>
  );
}

function SelectField({
  label,
  value,
  options,
  onChange,
  required,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  required?: boolean;
}) {
  return (
    <div className="grid gap-1.5">
      <Label className="flex items-center justify-between gap-2 text-xs">
        <span>{label}</span>
        {required ? <span className="text-destructive">必填</span> : null}
      </Label>
      <select
        className={CONTROL_CLASS}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">-</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </div>
  );
}

export default function StudentProfilePage() {
  const { toast, showToast } = useToast();
  const { setAfterTitle, setEnd, setTitle } = usePageHeader();
  const navigate = useNavigate();
  const [profile, setProfile] = useState<LearningProfile>(EMPTY_PROFILE);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const missingFields = useMemo(() => localMissingFields(profile), [profile]);
  const completeness = Math.round(Math.max(0, 1 - missingFields.length / 7) * 100);
  const complete = missingFields.length === 0;

  const updateProfileField = useCallback(
    <K extends keyof LearningProfile>(field: K, value: LearningProfile[K]) => {
      setProfile((prev) => ({ ...prev, [field]: value }));
    },
    [],
  );

  const loadProfile = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.getLearningProfile();
      setProfile({ ...EMPTY_PROFILE, ...result });
    } catch (error) {
      showToast(`个人信息加载失败：${error}`, "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadProfile();
  }, [loadProfile]);

  useEffect(() => {
    setTitle("个人信息");
    setAfterTitle(
      <Badge tone={complete ? "success" : "warning"}>{complete ? "已完成" : "未完成"}</Badge>,
    );
    setEnd(null);
    return () => {
      setTitle(null);
      setAfterTitle(null);
      setEnd(null);
    };
  }, [complete, setAfterTitle, setEnd, setTitle]);

  const saveProfile = useCallback(
    async (goNext = false) => {
      setSaving(true);
      try {
        const saved = await api.saveLearningProfile({
          ...profile,
          learning_preferences: profile.learning_preferences.filter(Boolean),
          current_courses: profile.current_courses.filter(Boolean),
        });
        setProfile({ ...EMPTY_PROFILE, ...saved });
        const savedMissing = saved.missing_fields ?? localMissingFields(saved);
        if (savedMissing.length > 0) {
          showToast(
            `还缺：${savedMissing.map((field) => REQUIRED_LABELS[field] ?? field).join("、")}`,
            "error",
          );
          return;
        }
        showToast("个人信息已保存", "success");
        if (goNext) navigate("/learning");
      } catch (error) {
        showToast(`保存失败：${error}`, "error");
      } finally {
        setSaving(false);
      }
    },
    [navigate, profile, showToast],
  );

  return (
    <div className="w-full min-w-0 space-y-5 p-4 sm:p-6">
      <Toast toast={toast} />
      <PluginSlot name="student-profile:top" />

      <section className="grid gap-4 xl:grid-cols-[minmax(0,0.72fr)_minmax(0,1.28fr)]">
        <div className="rounded-md border border-border bg-background/40 p-4">
          <div className="mb-4 flex items-center justify-between gap-3">
            <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
              <UserRound className="h-4 w-4" />
              入学档案
            </H2>
            <Badge tone={complete ? "success" : "warning"}>{completeness}%</Badge>
          </div>

          <div className="grid gap-3">
            <div className="rounded-md border border-border bg-background/50 p-3">
              <div className="text-xs text-muted-foreground">智能体状态</div>
              <div className="mt-1 text-lg font-semibold text-midground">
                {complete ? "可使用" : "待填写"}
              </div>
            </div>
            <div className="rounded-md border border-border bg-background/50 p-3">
              <div className="text-xs text-muted-foreground">缺失字段</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {missingFields.length === 0 ? (
                  <Badge tone="success">无</Badge>
                ) : (
                  missingFields.map((field) => (
                    <Badge key={field} tone="warning">
                      {REQUIRED_LABELS[field] ?? field}
                    </Badge>
                  ))
                )}
              </div>
            </div>
            <Button
              type="button"
              onClick={() => void saveProfile(true)}
              disabled={saving || loading}
              prefix={saving ? <Spinner /> : <GraduationCap />}
            >
              保存并进入智能体
            </Button>
            <Button
              ghost
              type="button"
              onClick={() => void saveProfile(false)}
              disabled={saving || loading}
              prefix={saving ? <Spinner /> : <Save />}
            >
              保存
            </Button>
          </div>
        </div>

        <div className="min-w-0 rounded-md border border-border bg-background/40 p-4">
          {loading ? (
            <div className="flex min-h-80 items-center justify-center">
              <Spinner />
            </div>
          ) : (
            <div className="grid gap-5">
              <section className="grid gap-3">
                <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
                  <GraduationCap className="h-4 w-4" />
                  基本信息
                </H2>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="grid gap-1.5">
                    <Label className="text-xs">昵称</Label>
                    <Input
                      value={profile.nickname}
                      placeholder="张同学"
                      onChange={(event) =>
                        updateProfileField("nickname", event.target.value)
                      }
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label className="text-xs">学校</Label>
                    <Input
                      value={profile.school}
                      placeholder="某某大学"
                      onChange={(event) =>
                        updateProfileField("school", event.target.value)
                      }
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label className="flex items-center justify-between gap-2 text-xs">
                      <span>专业</span>
                      <span className="text-destructive">必填</span>
                    </Label>
                    <Input
                      value={profile.major}
                      placeholder="计算机科学与技术"
                      onChange={(event) =>
                        updateProfileField("major", event.target.value)
                      }
                    />
                  </div>
                  <SelectField
                    label="年级"
                    value={profile.grade}
                    options={GRADE_OPTIONS}
                    required
                    onChange={(value) => updateProfileField("grade", value)}
                  />
                  <div className="grid gap-1.5">
                    <Label className="text-xs">学期</Label>
                    <Input
                      value={profile.semester}
                      placeholder="2026 春季"
                      onChange={(event) =>
                        updateProfileField("semester", event.target.value)
                      }
                    />
                  </div>
                  <SelectField
                    label="去向"
                    value={profile.career_target}
                    options={CAREER_TARGET_OPTIONS}
                    required
                    onChange={(value) => updateProfileField("career_target", value)}
                  />
                </div>
              </section>

              <section className="grid gap-3">
                <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
                  <CalendarClock className="h-4 w-4" />
                  时间与基础
                </H2>
                <div className="grid gap-3 md:grid-cols-3">
                  <SelectField
                    label="当前基础"
                    value={profile.baseline_level}
                    options={LEVEL_OPTIONS}
                    required
                    onChange={(value) => updateProfileField("baseline_level", value)}
                  />
                  <NumberInput
                    label="工作日分钟/天"
                    value={profile.weekday_minutes}
                    placeholder="60"
                    required
                    onChange={(value) => updateProfileField("weekday_minutes", value)}
                  />
                  <NumberInput
                    label="周末分钟/天"
                    value={profile.weekend_minutes}
                    placeholder="180"
                    required
                    onChange={(value) => updateProfileField("weekend_minutes", value)}
                  />
                  <div className="grid gap-1.5">
                    <Label className="text-xs">偏好时段</Label>
                    <Input
                      value={profile.preferred_study_time}
                      placeholder="晚饭后，周末上午"
                      onChange={(event) =>
                        updateProfileField("preferred_study_time", event.target.value)
                      }
                    />
                  </div>
                  <div className="grid gap-1.5 md:col-span-2">
                    <Label className="flex items-center justify-between gap-2 text-xs">
                      <span>学习偏好</span>
                      <span className="text-destructive">必填</span>
                    </Label>
                    <Input
                      value={profile.learning_preferences.join("，")}
                      placeholder={PREFERENCE_OPTIONS.join("，")}
                      onChange={(event) =>
                        updateProfileField(
                          "learning_preferences",
                          splitListInput(event.target.value),
                        )
                      }
                    />
                  </div>
                </div>
              </section>

              <section className="grid gap-3">
                <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
                  <BookOpen className="h-4 w-4" />
                  学习背景
                </H2>
                <div className="grid gap-3">
                  <div className="grid gap-1.5">
                    <Label className="text-xs">当前课程</Label>
                    <Input
                      value={profile.current_courses.join("，")}
                      placeholder="数据结构，概率论，大学英语"
                      onChange={(event) =>
                        updateProfileField(
                          "current_courses",
                          splitListInput(event.target.value),
                        )
                      }
                    />
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="grid gap-1.5">
                      <Label className="text-xs">优势</Label>
                      <textarea
                        className="min-h-24 w-full resize-y rounded-md border border-border bg-background/60 px-3 py-2 text-sm outline-none transition focus:border-primary/70"
                        value={profile.strengths}
                        placeholder="自学能力强，数学基础还可以"
                        onChange={(event) =>
                          updateProfileField("strengths", event.target.value)
                        }
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label className="text-xs">薄弱项</Label>
                      <textarea
                        className="min-h-24 w-full resize-y rounded-md border border-border bg-background/60 px-3 py-2 text-sm outline-none transition focus:border-primary/70"
                        value={profile.weaknesses}
                        placeholder="算法弱，英语阅读慢"
                        onChange={(event) =>
                          updateProfileField("weaknesses", event.target.value)
                        }
                      />
                    </div>
                  </div>
                  <div className="grid gap-1.5">
                    <Label className="text-xs">近期限制</Label>
                    <textarea
                      className="min-h-24 w-full resize-y rounded-md border border-border bg-background/60 px-3 py-2 text-sm outline-none transition focus:border-primary/70"
                      value={profile.constraints}
                      placeholder="考试周、社团、实习、课程作业"
                      onChange={(event) =>
                        updateProfileField("constraints", event.target.value)
                      }
                    />
                  </div>
                </div>
              </section>

              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  onClick={() => void saveProfile(true)}
                  disabled={saving}
                  prefix={saving ? <Spinner /> : <Target />}
                >
                  保存并进入智能体
                </Button>
                <Button
                  ghost
                  type="button"
                  onClick={() => void saveProfile(false)}
                  disabled={saving}
                  prefix={saving ? <Spinner /> : <Save />}
                >
                  保存
                </Button>
              </div>
            </div>
          )}
        </div>
      </section>

      <PluginSlot name="student-profile:bottom" />
    </div>
  );
}
