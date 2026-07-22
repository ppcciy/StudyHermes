"""Learning-related REST API endpoints for HermesLearn."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import mimetypes
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, Field

from hermes_cli.config import get_hermes_home
from agent.learning_models import (
    DailyTask,
    ErrorType,
    LearningGoal,
    LearningGoalStatus,
    LearningGoalType,
    LearningPlan,
    LearningStats,
    MasteryStatus,
    TaskDifficulty,
    TaskStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["learning"])

_LEARNING_DATA_DIR = get_hermes_home() / "learning"
_LEARNING_DATA_DIR.mkdir(parents=True, exist_ok=True)
_LEARNING_CREATE_LOCK = threading.RLock()
_LEARNING_REQUIREMENT_LLM_TIMEOUT_SECONDS: Optional[float] = None
_LEARNING_PLAN_RETRY_AFTER_SECONDS = 8
_LEARNING_LLM_BACKGROUND_TIMEOUT_SECONDS = 30 * 60.0
_LEARNING_PROGRESS_FEEDBACK_LLM_TIMEOUT_SECONDS: Optional[float] = None
_LEARNING_TASK_GUIDANCE_CACHE_TTL_SECONDS = 6 * 60 * 60
_LEARNING_REQUIREMENT_MAX_OUTPUT_TOKENS = 700
_LEARNING_PLAN_MAX_OUTPUT_TOKENS = 1500
_LEARNING_TIMETABLE_MAX_OUTPUT_TOKENS = 1000
_LEARNING_TASK_GUIDANCE_MAX_OUTPUT_TOKENS = 600
_LEARNING_PROGRESS_FEEDBACK_MAX_OUTPUT_TOKENS = 700
_LEARNING_COURSE_PLAN_CACHE_TTL_SECONDS = 24 * 60 * 60
_LEARNING_LLM_MAX_ITERATIONS = 3
_TODAY_SCHEDULE_VERSION = 2
_LEARNING_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills" / "learning"
_LEARNING_SKILL_DIRS = {
    "requirement-intake": "hermeslearn-requirement-intake",
    "course-planner": "hermeslearn-course-planner",
    "daily-scheduler": "hermeslearn-daily-scheduler",
    "task-guidance": "hermeslearn-task-guidance",
    "progress-feedback": "hermeslearn-progress-feedback",
}
_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
_log = logging.getLogger(__name__)
_TASK_GUIDANCE_INFLIGHT: Dict[str, asyncio.Task] = {}
_COURSE_PLAN_INFLIGHT: Dict[str, asyncio.Task] = {}
_TODAY_TIMETABLE_INFLIGHT: Dict[str, asyncio.Task] = {}


class LearningSkillError(RuntimeError):
    """Raised when a required HermesLearn skill cannot produce usable JSON."""

    def __init__(self, skill_name: str, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.skill_name = skill_name
        self.status_code = status_code


class LearningSkillTimeoutError(LearningSkillError):
    def __init__(self, skill_name: str, timeout_seconds: float):
        super().__init__(
            skill_name,
            f"{skill_name} timed out after {timeout_seconds:.1f}s",
            status_code=504,
        )


class LearningSkillPendingError(LearningSkillError):
    def __init__(self, skill_name: str):
        super().__init__(
            skill_name,
            f"{skill_name} is still generating",
            status_code=202,
        )

_FILES_DIR = get_hermes_home() / "learning" / "files"
_FILES_DIR.mkdir(exist_ok=True)


def _run_llm(instructions: str, user_input: str, max_tokens: int = 2048) -> str:
    """调用项目主模型执行学习场景的一次性推理。"""
    try:
        from agent.auxiliary_client import (
            auxiliary_max_tokens_param,
            extract_content_or_reasoning,
            get_auxiliary_extra_body,
            resolve_provider_client,
        )
    except Exception as exc:
        logger.error("Learning LLM 模块加载失败: %s", exc)
        raise HTTPException(status_code=503, detail="LLM 服务不可用，请检查模型配置")

    messages = []
    if (instructions or "").strip():
        messages.append({"role": "system", "content": instructions})
    messages.append({"role": "user", "content": user_input or ""})

    try:
        client, model = resolve_provider_client("auto", task="learning")
        if client is None or not model:
            raise RuntimeError("no configured LLM provider/model")

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "timeout": 300.0,
            "temperature": 0.2,
            **auxiliary_max_tokens_param(max_tokens, model=model),
        }
        extra_body = get_auxiliary_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as first_exc:
            message = str(first_exc).lower()
            retry_kwargs = dict(kwargs)
            if "temperature" in message or "unsupported" in message:
                retry_kwargs.pop("temperature", None)
            if "max_completion_tokens" in message and "max_completion_tokens" in retry_kwargs:
                value = retry_kwargs.pop("max_completion_tokens")
                retry_kwargs["max_tokens"] = value
            elif "max_tokens" in message and "max_tokens" in retry_kwargs:
                value = retry_kwargs.pop("max_tokens")
                retry_kwargs["max_completion_tokens"] = value

            if retry_kwargs != kwargs:
                response = client.chat.completions.create(**retry_kwargs)
            else:
                raise

        text = (extract_content_or_reasoning(response) or "").strip()
        return _strip_markdown_code_fence(text)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Learning LLM 调用失败: %s", exc)
        raise HTTPException(status_code=503, detail=f"LLM 分析失败: {exc}")


def _strip_markdown_code_fence(text: str) -> str:
    text = (text or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _run_vision_llm(image_base64: str, mime_type: str, prompt: str, max_tokens: int = 2048) -> str:
    """调用多模态 LLM 识别图片中的题目内容。"""
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning
    except Exception as exc:
        logger.error("LLM 模块加载失败: %s", exc)
        raise HTTPException(status_code=503, detail="LLM 服务不可用，请检查模型配置")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                },
            ],
        }
    ]

    try:
        response = call_llm(
            task="vision",
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
            timeout=300.0,
        )
        return extract_content_or_reasoning(response)
    except Exception as exc:
        logger.error("Vision LLM 调用失败: %s", exc)
        raise HTTPException(status_code=503, detail=f"图片识别失败: {exc}")


def _parse_pdf(file_bytes: bytes) -> str:
    """解析 PDF 文件，提取文本内容。"""
    try:
        import io
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                texts.append(text.strip())
        return "\n\n".join(texts)
    except ImportError:
        raise HTTPException(status_code=503, detail="PDF 解析需要安装 pypdf：pip install pypdf")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF 解析失败: {exc}")


def _parse_docx(file_bytes: bytes) -> str:
    """解析 Word(.docx) 文件，提取文本内容。"""
    try:
        import io
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        texts = []
        for para in doc.paragraphs:
            if para.text.strip():
                texts.append(para.text.strip())
        return "\n\n".join(texts)
    except ImportError:
        raise HTTPException(status_code=503, detail="Word 解析需要安装 python-docx：pip install python-docx")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Word 解析失败: {exc}")


def _save_file(file_data: str, filename: str, question_id: str, file_type: str = "question") -> str:
    """保存文件到本地存储，返回文件路径。

    Args:
        file_data: base64 编码的文件数据
        filename: 原始文件名
        question_id: 错题ID
        file_type: 文件类型 (question, correct_answer, user_answer)

    Returns:
        相对文件路径，用于存储到错题数据中
    """
    import base64
    import hashlib

    file_bytes = base64.b64decode(file_data)
    file_hash = hashlib.md5(file_bytes).hexdigest()
    ext = filename.split(".")[-1].lower() if "." in filename else "bin"
    stored_name = f"{question_id}_{file_type}_{file_hash}.{ext}"
    stored_path = _FILES_DIR / stored_name

    stored_path.write_bytes(file_bytes)
    return f"files/{stored_name}"


def _get_file_path(relative_path: str) -> Path:
    """根据相对路径获取文件的绝对路径。"""
    return _FILES_DIR / relative_path.split("/")[-1]


class CreateLearningGoalRequest(BaseModel):
    name: str
    description: str
    goal_type: str = Field(..., description="skill, knowledge, certification, or project")
    target_date: Optional[str] = None
    estimated_hours: float = 10.0
    tags: List[str] = []


class CreateLearningPlanRequest(BaseModel):
    goal_id: str
    name: str
    description: str = ""


class CreateDailyTaskRequest(BaseModel):
    plan_id: str
    goal_id: str
    title: str
    description: str = ""
    difficulty: str = "medium"
    estimated_minutes: int = 30
    scheduled_date: str = ""
    priority: int = 3


class UpdateTaskStatusRequest(BaseModel):
    status: str = "completed"
    actual_minutes: Optional[int] = None
    feedback: str = ""
    mastery_level: str = ""
    blockers: List[str] = Field(default_factory=list)
    next_focus: str = ""


class LearningProfileRequest(BaseModel):
    nickname: str = ""
    school: str = ""
    major: str = ""
    grade: str = ""
    semester: str = ""
    career_target: str = ""
    baseline_level: str = ""
    weekday_minutes: Optional[int] = None
    weekend_minutes: Optional[int] = None
    preferred_study_time: str = ""
    learning_preferences: List[str] = Field(default_factory=list)
    current_courses: List[str] = Field(default_factory=list)
    strengths: str = ""
    weaknesses: str = ""
    constraints: str = ""


class AnalyzeLearningRequirementRequest(BaseModel):
    message: str = ""
    session_id: Optional[str] = None
    answers: Dict[str, Any] = Field(default_factory=dict)
    existing_goal_id: Optional[str] = None
    profile: Dict[str, Any] = Field(default_factory=dict)


class FinalizeLearningRequirementRequest(BaseModel):
    session_id: Optional[str] = None
    draft: Dict[str, Any] = Field(default_factory=dict)
    force_retry: bool = False


def _generate_id() -> str:
    import secrets

    return secrets.token_urlsafe(16)


def _read_learning_json_dict(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            _log.warning("Could not parse learning data file %s: %s", path, exc)
            return {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def _load_goals() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "goals.json"
    return _read_learning_json_dict(path)


def _save_goals(goals: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "goals.json"
    path.write_text(json.dumps(goals, indent=2), encoding="utf-8")


def _load_plans() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "plans.json"
    return _read_learning_json_dict(path)


def _save_plans(plans: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "plans.json"
    path.write_text(json.dumps(plans, indent=2), encoding="utf-8")


def _load_tasks() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "tasks.json"
    return _read_learning_json_dict(path)


def _save_tasks(tasks: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "tasks.json"
    path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def _default_learning_profile() -> Dict[str, Any]:
    return {
        "nickname": "",
        "school": "",
        "major": "",
        "grade": "",
        "semester": "",
        "career_target": "",
        "baseline_level": "",
        "weekday_minutes": None,
        "weekend_minutes": None,
        "preferred_study_time": "",
        "learning_preferences": [],
        "current_courses": [],
        "strengths": "",
        "weaknesses": "",
        "constraints": "",
        "updated_at": None,
    }


def _normalize_text_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [
            item.strip()
            for item in re.split(r"[,，、\n]", value)
            if item.strip()
        ]
    return []


def _normalize_minutes(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, minutes)


def _load_learning_profile() -> Dict[str, Any]:
    path = _LEARNING_DATA_DIR / "profile.json"
    profile = _default_learning_profile()
    if path.exists():
        loaded = _read_learning_json_dict(path)
        if isinstance(loaded, dict):
            profile.update(loaded)
    profile["learning_preferences"] = _normalize_text_list(
        profile.get("learning_preferences")
    )
    profile["current_courses"] = _normalize_text_list(profile.get("current_courses"))
    profile["weekday_minutes"] = _normalize_minutes(profile.get("weekday_minutes"))
    profile["weekend_minutes"] = _normalize_minutes(profile.get("weekend_minutes"))
    return profile


def _save_learning_profile(profile: Dict[str, Any]) -> None:
    path = _LEARNING_DATA_DIR / "profile.json"
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")


def _load_requirement_sessions() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "requirements.json"
    return _read_learning_json_dict(path)


def _save_requirement_sessions(sessions: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "requirements.json"
    path.write_text(json.dumps(sessions, indent=2), encoding="utf-8")


def _load_schedule_state() -> Dict[str, Any]:
    path = _LEARNING_DATA_DIR / "schedule_state.json"
    return _read_learning_json_dict(path)


def _save_schedule_state(state: Dict[str, Any]) -> None:
    path = _LEARNING_DATA_DIR / "schedule_state.json"
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _local_now() -> datetime:
    return datetime.now(_LOCAL_TIMEZONE)


def _local_today() -> date:
    return _local_now().date()


def _local_date_from(value: datetime) -> date:
    if value.tzinfo:
        return value.astimezone(_LOCAL_TIMEZONE).date()
    return value.replace(tzinfo=_LOCAL_TIMEZONE).date()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


@router.get("/profile", response_model=Dict[str, Any])
async def get_learning_profile(request: Request):
    return _profile_with_status(_load_learning_profile())


@router.put("/profile", response_model=Dict[str, Any])
async def update_learning_profile(request: Request, body: LearningProfileRequest):
    now = datetime.now(timezone.utc)
    profile = _default_learning_profile()
    profile.update(
        {
            "nickname": body.nickname.strip(),
            "school": body.school.strip(),
            "major": body.major.strip(),
            "grade": body.grade.strip(),
            "semester": body.semester.strip(),
            "career_target": body.career_target.strip(),
            "baseline_level": body.baseline_level.strip(),
            "weekday_minutes": _normalize_minutes(body.weekday_minutes),
            "weekend_minutes": _normalize_minutes(body.weekend_minutes),
            "preferred_study_time": body.preferred_study_time.strip(),
            "learning_preferences": _normalize_text_list(body.learning_preferences),
            "current_courses": _normalize_text_list(body.current_courses),
            "strengths": body.strengths.strip(),
            "weaknesses": body.weaknesses.strip(),
            "constraints": body.constraints.strip(),
            "updated_at": now.isoformat(),
        }
    )
    _save_learning_profile(profile)
    schedule_now = _local_now()
    goals = _load_goals()
    tasks = _load_tasks()
    if _refresh_today_task_schedule(tasks, goals, profile, schedule_now, force=True):
        _save_tasks(tasks)
    return _profile_with_status(profile)


@router.get("/goals", response_model=List[Dict[str, Any]])
async def get_learning_goals(request: Request):
    goals = _load_goals()
    return list(goals.values())


@router.post("/goals", response_model=Dict[str, Any])
async def create_learning_goal(request: Request, body: CreateLearningGoalRequest):
    try:
        goal_type = LearningGoalType(body.goal_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid goal type")

    with _LEARNING_CREATE_LOCK:
        goals = _load_goals()
        duplicate_goal = _find_similar_active_goal(
            {
                "description": body.description,
                "goal_name": body.name,
                "goal_type": goal_type.value,
                "name": body.name,
                "tags": body.tags,
            },
            goals,
        )
        if duplicate_goal:
            return {
                **duplicate_goal,
                "duplicate_goal": True,
                "duplicate_of": duplicate_goal.get("id"),
                "goal_created": False,
                "message": "已存在相似学习目标，未重复创建。",
            }

        now = datetime.now(timezone.utc)
        goal_id = _generate_id()

        goal: Dict[str, Any] = {
            "id": goal_id,
            "name": body.name,
            "description": body.description,
            "goal_type": goal_type.value,
            "status": "active",
            "target_date": body.target_date,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "estimated_hours": body.estimated_hours,
            "progress": 0.0,
            "tags": body.tags,
            "prerequisites": [],
        }

        goals[goal_id] = goal
        _save_goals(goals)

        return {**goal, "goal_created": True}


@router.get("/goals/{goal_id}", response_model=Dict[str, Any])
async def get_learning_goal(request: Request, goal_id: str):
    goals = _load_goals()
    if goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goals[goal_id]


@router.put("/goals/{goal_id}", response_model=Dict[str, Any])
async def update_learning_goal(request: Request, goal_id: str, body: CreateLearningGoalRequest):
    goals = _load_goals()
    if goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    goals[goal_id].update(
        {
            "name": body.name,
            "description": body.description,
            "goal_type": body.goal_type,
            "target_date": body.target_date,
            "estimated_hours": body.estimated_hours,
            "tags": body.tags,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_goals(goals)

    return goals[goal_id]


@router.delete("/goals/{goal_id}")
async def delete_learning_goal(request: Request, goal_id: str):
    goals = _load_goals()
    if goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    plans = _load_plans()
    plan_ids_to_delete = [pid for pid, p in plans.items() if p["goal_id"] == goal_id]
    for pid in plan_ids_to_delete:
        del plans[pid]
    _save_plans(plans)

    tasks = _load_tasks()
    remaining_tasks = {tid: t for tid, t in tasks.items() if t["goal_id"] != goal_id}
    _save_tasks(remaining_tasks)

    del goals[goal_id]
    _save_goals(goals)

    return {"message": "Goal deleted successfully"}


@router.post("/goals/{goal_id}/status", response_model=Dict[str, Any])
async def update_goal_status(request: Request, goal_id: str, status: str):
    goals = _load_goals()
    if goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    try:
        LearningGoalStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status")

    goals[goal_id]["status"] = status
    goals[goal_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

    if status == "completed":
        goals[goal_id]["progress"] = 100.0

    _save_goals(goals)
    return goals[goal_id]


@router.get("/plans", response_model=List[Dict[str, Any]])
async def get_learning_plans(request: Request):
    plans = _load_plans()
    return [
        plan
        for _, plan in sorted(
            enumerate(plans.values()),
            key=lambda item: (
                str(item[1].get("goal_id") or ""),
                _plan_sort_key(item[1], item[0]),
            ),
        )
    ]


@router.get("/goals/{goal_id}/plans", response_model=List[Dict[str, Any]])
async def get_goal_plans(request: Request, goal_id: str):
    plans = _load_plans()
    return [
        plan
        for _, plan in sorted(
            enumerate(p for p in plans.values() if p.get("goal_id") == goal_id),
            key=lambda item: _plan_sort_key(item[1], item[0]),
        )
    ]


@router.post("/plans", response_model=Dict[str, Any])
async def create_learning_plan(request: Request, body: CreateLearningPlanRequest):
    goals = _load_goals()
    if body.goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    plans = _load_plans()
    now = datetime.now(timezone.utc)
    plan_id = _generate_id()

    plan: Dict[str, Any] = {
        "id": plan_id,
        "goal_id": body.goal_id,
        "name": body.name,
        "description": body.description,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "total_tasks": 0,
        "completed_tasks": 0,
        "position": _next_plan_position(plans, body.goal_id),
    }

    plans[plan_id] = plan
    _save_plans(plans)

    return plan


@router.get("/plans/{plan_id}", response_model=Dict[str, Any])
async def get_learning_plan(request: Request, plan_id: str):
    plans = _load_plans()
    if plan_id not in plans:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plans[plan_id]


@router.delete("/plans/{plan_id}")
async def delete_learning_plan(request: Request, plan_id: str):
    plans = _load_plans()
    if plan_id not in plans:
        raise HTTPException(status_code=404, detail="Plan not found")

    tasks = _load_tasks()
    remaining_tasks = {tid: t for tid, t in tasks.items() if t["plan_id"] != plan_id}
    _save_tasks(remaining_tasks)

    del plans[plan_id]
    _save_plans(plans)

    return {"message": "Plan deleted successfully"}


@router.get("/tasks", response_model=List[Dict[str, Any]])
async def get_daily_tasks(request: Request, plan_id: Optional[str] = None):
    tasks = _load_tasks()
    if plan_id:
        return [t for t in tasks.values() if t["plan_id"] == plan_id]
    return list(tasks.values())


@router.get("/goals/{goal_id}/tasks", response_model=List[Dict[str, Any]])
async def get_goal_tasks(request: Request, goal_id: str):
    tasks = _load_tasks()
    return [t for t in tasks.values() if t["goal_id"] == goal_id]


@router.post("/tasks", response_model=Dict[str, Any])
async def create_daily_task(request: Request, body: CreateDailyTaskRequest):
    plans = _load_plans()
    if body.plan_id not in plans:
        raise HTTPException(status_code=404, detail="Plan not found")

    goals = _load_goals()
    if body.goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    try:
        TaskDifficulty(body.difficulty)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid difficulty")

    tasks = _load_tasks()
    now = datetime.now(timezone.utc)
    task_id = _generate_id()

    task: Dict[str, Any] = {
        "id": task_id,
        "plan_id": body.plan_id,
        "goal_id": body.goal_id,
        "title": body.title,
        "description": body.description,
        "status": "pending",
        "difficulty": body.difficulty,
        "estimated_minutes": body.estimated_minutes,
        "scheduled_date": body.scheduled_date,
        "completed_at": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "position": len(tasks),
        "priority": body.priority,
    }

    tasks[task_id] = task
    _refresh_today_task_schedule(
        tasks,
        _load_goals(),
        _load_learning_profile(),
        _local_now(),
        force=True,
        plans=plans,
    )
    _save_tasks(tasks)

    plans[body.plan_id]["total_tasks"] += 1
    plans[body.plan_id]["updated_at"] = now.isoformat()
    _save_plans(plans)

    return task


@router.get("/tasks/{task_id}", response_model=Dict[str, Any])
async def get_daily_task(request: Request, task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]


@router.get("/tasks/{task_id}/guidance", response_model=Dict[str, Any])
async def get_task_guidance(request: Request, task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    task = tasks[task_id]
    goals = _load_goals()
    plans = _load_plans()
    profile = _load_learning_profile()

    refresh = (
        request is not None
        and str(request.query_params.get("refresh") or "").strip().lower()
        in {"1", "true", "yes"}
    )
    return await _build_task_guidance_response(
        task=task,
        goal=goals.get(str(task.get("goal_id") or "")),
        plan=plans.get(str(task.get("plan_id") or "")),
        profile=profile,
        recent_feedback=_recent_feedback_for_goal(str(task.get("goal_id") or ""), tasks),
        force_retry=refresh,
    )


@router.post("/tasks/{task_id}/status", response_model=Dict[str, Any])
async def update_task_status(request: Request, task_id: str, body: UpdateTaskStatusRequest):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        TaskStatus(body.status)
    except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")

    now = datetime.now(timezone.utc)
    was_completed = tasks[task_id].get("status") == TaskStatus.COMPLETED.value
    tasks[task_id]["status"] = body.status
    tasks[task_id]["updated_at"] = now.isoformat()

    if body.status == "completed":
        tasks[task_id]["completed_at"] = now.isoformat()

        plans = _load_plans()
        plan_id = tasks[task_id]["plan_id"]
        if plan_id in plans:
            plan_tasks = [
                task
                for task in tasks.values()
                if task.get("plan_id") == plan_id
            ]
            plans[plan_id]["completed_tasks"] = sum(
                1 for task in plan_tasks if task.get("status") == TaskStatus.COMPLETED.value
            )
            plans[plan_id]["updated_at"] = now.isoformat()

        goals = _load_goals()
        goal_id = tasks[task_id]["goal_id"]
        if goal_id in goals:
            goal_tasks = [t for t in tasks.values() if t["goal_id"] == goal_id]
            completed = sum(1 for t in goal_tasks if t["status"] == "completed")
            total = len(goal_tasks)
            if total > 0:
                goals[goal_id]["progress"] = round((completed / total) * 100, 1)
            goals[goal_id]["updated_at"] = now.isoformat()

        feedback_text = _text_or_empty(body.feedback, 1200)
        actual_minutes = _coerce_positive_int(
            body.actual_minutes,
            _coerce_positive_int(
                tasks[task_id].get("estimated_minutes"),
                30,
                minimum=1,
                maximum=8 * 60,
            ),
            minimum=1,
            maximum=8 * 60,
        )
        if body.actual_minutes is not None:
            raw_feedback = _task_learning_feedback(tasks[task_id])
            tasks[task_id]["learning_feedback"] = {
                **raw_feedback,
                "actual_minutes": actual_minutes,
            }

        has_feedback = any(
            [
                feedback_text,
                _text_or_empty(body.mastery_level, 80),
                body.blockers,
                _text_or_empty(body.next_focus, 500),
            ]
        )
        if has_feedback:
            profile = _load_learning_profile()
            feedback_payload = {
                "actual_minutes": actual_minutes,
                "feedback": feedback_text,
                "mastery_level": _text_or_empty(body.mastery_level, 80),
                "blockers": _normalize_text_list(body.blockers),
                "next_focus": _text_or_empty(body.next_focus, 500),
            }
            await _analyze_and_apply_task_feedback(
                tasks=tasks,
                plans=plans,
                goals=goals,
                task_id=task_id,
                profile=profile,
                feedback=feedback_payload,
                now=now,
            )

        if plan_id in plans:
            plan_tasks = [
                task
                for task in tasks.values()
                if task.get("plan_id") == plan_id
            ]
            plans[plan_id]["completed_tasks"] = sum(
                1 for task in plan_tasks if task.get("status") == TaskStatus.COMPLETED.value
            )
            plans[plan_id]["total_tasks"] = len(plan_tasks)
            plans[plan_id]["updated_at"] = now.isoformat()

        if goal_id in goals:
            goal_tasks = [t for t in tasks.values() if t["goal_id"] == goal_id]
            completed = sum(1 for t in goal_tasks if t["status"] == "completed")
            total = len(goal_tasks)
            if total > 0:
                goals[goal_id]["progress"] = round((completed / total) * 100, 1)
            goals[goal_id]["updated_at"] = now.isoformat()

        _save_plans(plans)
        _save_goals(goals)
    elif was_completed:
        tasks[task_id]["completed_at"] = None

    _save_tasks(tasks)
    return tasks[task_id]


@router.delete("/tasks/{task_id}")
async def delete_daily_task(request: Request, task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    plan_id = tasks[task_id]["plan_id"]
    del tasks[task_id]
    _save_tasks(tasks)

    plans = _load_plans()
    if plan_id in plans:
        plans[plan_id]["total_tasks"] -= 1
        if plans[plan_id]["completed_tasks"] > plans[plan_id]["total_tasks"]:
            plans[plan_id]["completed_tasks"] = plans[plan_id]["total_tasks"]
        plans[plan_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_plans(plans)

    return {"message": "Task deleted successfully"}


@router.get("/stats", response_model=Dict[str, Any])
async def get_learning_stats(request: Request):
    goals = _load_goals()
    plans = _load_plans()
    tasks = _load_tasks()
    profile = _load_learning_profile()
    now = _local_now()
    if _refresh_today_task_schedule(tasks, goals, profile, now, plans=plans):
        _save_tasks(tasks)

    total_goals = len(goals)
    completed_goals = sum(1 for g in goals.values() if g["status"] == "completed")
    active_goals = sum(1 for g in goals.values() if g["status"] == "active")

    completed_tasks = sum(1 for t in tasks.values() if t["status"] == "completed")
    total_minutes = sum(
        _completed_task_actual_minutes(t)
        for t in tasks.values()
        if t["status"] == "completed"
    )
    total_hours = round(total_minutes / 60, 1)

    today = _local_date_from(now)
    today_tasks = [
        t for t in tasks.values() if _task_scheduled_date(t) == today
    ]

    stats: Dict[str, Any] = {
        "total_goals": total_goals,
        "completed_goals": completed_goals,
        "active_goals": active_goals,
        "total_hours_learned": total_hours,
        "total_tasks_completed": completed_tasks,
        "streak_days": 0,
        "average_daily_minutes": 0,
        "today_tasks": len(today_tasks),
        "today_completed": sum(1 for t in today_tasks if t["status"] == "completed"),
    }

    return stats


def _completed_task_actual_minutes(task: Dict[str, Any]) -> int:
    feedback = task.get("learning_feedback")
    if not isinstance(feedback, dict):
        return 0

    try:
        minutes = int(round(float(feedback.get("actual_minutes") or 0)))
    except (TypeError, ValueError):
        return 0

    return max(0, minutes)


@router.get("/today", response_model=Dict[str, Any])
async def get_today_tasks(request: Request):
    goals = _load_goals()
    plans = _load_plans()
    tasks = _load_tasks()
    profile = _load_learning_profile()
    now = _local_now()
    if _refresh_today_task_schedule(tasks, goals, profile, now, plans=plans):
        _save_tasks(tasks)
    today = _local_date_from(now)
    plan_order_by_id = _plan_order_by_id(plans)

    today_tasks = [
        t
        for t in tasks.values()
        if _task_scheduled_date(t) == today
    ]

    today_tasks.sort(key=lambda t: _daily_task_sort_key(t, today, plan_order_by_id))
    timetable_entries = _local_today_timetable_entries(today_tasks, profile)

    timetable_by_task = {
        str(entry.get("task_id")): entry
        for entry in timetable_entries
        if isinstance(entry, dict)
    }
    today_tasks = [dict(task) for task in today_tasks]
    for task in today_tasks:
        entry = timetable_by_task.get(str(task.get("id") or ""))
        if entry:
            task["recommended_start_time"] = entry.get("start_time")
            task["recommended_end_time"] = entry.get("end_time")
            task["schedule_reason"] = entry.get("reason")

    today_tasks.sort(key=lambda task: _daily_task_sort_key(task, today, plan_order_by_id))
    all_today_completed = bool(today_tasks) and all(
        task.get("status") == TaskStatus.COMPLETED.value for task in today_tasks
    )
    ahead_tasks = (
        _ahead_task_recommendations(tasks, goals, today, plans=plans)
        if all_today_completed
        else []
    )

    return {
        "date": today.isoformat(),
        "tasks": today_tasks,
        "ahead_date": (today + timedelta(days=1)).isoformat(),
        "ahead_tasks": ahead_tasks,
        "goal_names": {g["id"]: g["name"] for g in goals.values()},
        "generated_by": "local-timetable" if today_tasks else "none",
        "skill": None,
        "timetable_status": "ready" if today_tasks else "none",
        "timetable_error": None,
    }


_CHINESE_NUMBERS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "半": 0.5,
}

_TOPIC_HINTS = [
    ("machine learning", "机器学习"),
    ("javascript", "JavaScript"),
    ("typescript", "TypeScript"),
    ("python", "Python"),
    ("react", "React"),
    ("vue", "Vue"),
    ("java", "Java"),
    ("数据分析", "数据分析"),
    ("机器学习", "机器学习"),
    ("人工智能", "人工智能"),
    ("深度学习", "深度学习"),
    ("数据结构与算法", "数据结构与算法"),
    ("数据结构", "数据结构"),
    ("考研数学", "考研数学"),
    ("考研英语", "考研英语"),
    ("英语四六级", "英语四六级"),
    ("英语", "英语"),
    ("数学", "数学"),
]

_TOPIC_HINTS.extend(
    [
        ("\u9ad8\u6570", "\u9ad8\u7b49\u6570\u5b66"),
        ("\u9ad8\u7b49\u6570\u5b66", "\u9ad8\u7b49\u6570\u5b66"),
        ("\u7ebf\u4ee3", "\u7ebf\u6027\u4ee3\u6570"),
        ("\u7ebf\u6027\u4ee3\u6570", "\u7ebf\u6027\u4ee3\u6570"),
        ("\u6982\u7387\u8bba", "\u6982\u7387\u8bba\u4e0e\u6570\u7406\u7edf\u8ba1"),
        ("\u6570\u7406\u7edf\u8ba1", "\u6982\u7387\u8bba\u4e0e\u6570\u7406\u7edf\u8ba1"),
        ("\u5927\u7269", "\u5927\u5b66\u7269\u7406"),
        ("\u5927\u5b66\u7269\u7406", "\u5927\u5b66\u7269\u7406"),
    ]
)

_SYSTEM_LEARNING_PURPOSE = "\u7cfb\u7edf\u5b66\u4e60"
_EXAM_CERT_PURPOSE = "\u8003\u8bd5/\u8003\u8bc1"
_COURSE_TOPIC_KEYWORDS = [
    "\u9ad8\u6570",
    "\u9ad8\u7b49\u6570\u5b66",
    "\u7ebf\u4ee3",
    "\u7ebf\u6027\u4ee3\u6570",
    "\u6982\u7387\u8bba",
    "\u6570\u7406\u7edf\u8ba1",
    "\u6570\u636e\u7ed3\u6784",
    "\u6570\u636e\u7ed3\u6784\u4e0e\u7b97\u6cd5",
    "\u5927\u7269",
    "\u5927\u5b66\u7269\u7406",
    "\u671f\u672b",
    "\u8bfe\u7a0b",
]
_GOAL_CREATE_COMMAND_PREFIXES = [
    "\u6dfb\u52a0",
    "\u65b0\u589e",
    "\u65b0\u5efa",
    "\u521b\u5efa",
    "\u52a0\u5165",
    "\u5b89\u6392",
    "\u5236\u5b9a",
    "\u751f\u6210",
]
_GOAL_CREATE_LEADING_WORDS = [
    "\u8bf7",
    "\u9ebb\u70e6",
    "\u5e2e\u6211",
    "\u7ed9\u6211",
    "\u4e3a\u6211",
    "\u6211\u8981",
    "\u6211\u60f3",
]
_GOAL_CREATE_FILLER_WORDS = [
    "\u4e00\u4e2a",
    "\u4e00\u95e8",
    "\u4e00\u4efd",
    "\u4e00\u4e0b",
    "\u65b0\u7684",
    "\u5173\u4e8e",
    "\u6709\u5173",
]
_GOAL_TOPIC_TRAILING_WORDS = [
    "\u5b66\u4e60\u76ee\u6807",
    "\u5b66\u4e60\u8ba1\u5212",
    "\u5b66\u4e60",
    "\u76ee\u6807",
    "\u8ba1\u5212",
    "\u8bfe\u7a0b",
]
_GOAL_TOPIC_INLINE_STOPS = [
    "\u4e3a\u4e86",
    "\u7528\u6765",
    "\u6bcf\u5929",
    "\u6bcf\u65e5",
    "\u4e00\u5929",
    "\u5e0c\u671b",
    "\u60f3\u8981",
    "\u6253\u7b97",
]

_FIELD_QUESTIONS = {
    "goal_name": {
        "label": "学习目标",
        "question": "你具体想学习哪个方向或主题？",
        "type": "text",
        "placeholder": "例如 Java 后端开发",
    },
    "purpose": {
        "label": "学习用途",
        "question": "这次学习主要为了什么结果？",
        "type": "select",
        "options": ["找工作", "转行", "考试/考证", "完成项目", "提升工作能力", "兴趣探索"],
    },
    "current_level": {
        "label": "当前基础",
        "question": "你现在大概是什么基础？",
        "type": "select",
        "options": ["零基础", "入门", "初级", "中级", "高级"],
    },
    "target_level": {
        "label": "目标水平",
        "question": "你希望学到什么程度？",
        "type": "select",
        "options": ["能独立完成项目", "达到面试/就业水平", "通过考试", "系统掌握", "快速了解"],
    },
    "daily_minutes": {
        "label": "每日时间",
        "question": "每天能稳定投入多少分钟？",
        "type": "number",
        "placeholder": "例如 90",
    },
    "timeframe": {
        "label": "周期/截止",
        "question": "你希望多久完成，或具体截止到哪天？",
        "type": "select",
        "options": ["1个月", "3个月", "半年", "1年"],
    },
    "preference": {
        "label": "学习偏好",
        "question": "你更偏好哪种学习方式？",
        "type": "select",
        "options": ["项目实战", "视频课程", "文档阅读", "刷题训练", "AI 陪练"],
    },
}

_REQUIRED_REQUIREMENT_FIELDS = [
    "goal_name",
    "purpose",
    "current_level",
    "target_level",
    "preference",
]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _number_from_text(value: str) -> Optional[float]:
    raw = value.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    if raw in _CHINESE_NUMBERS:
        return float(_CHINESE_NUMBERS[raw])
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CHINESE_NUMBERS.get(left, 1) if left else 1
        ones = _CHINESE_NUMBERS.get(right, 0) if right else 0
        if isinstance(tens, (int, float)) and isinstance(ones, (int, float)):
            return float(tens * 10 + ones)
    return None


def _minutes_from_value(amount: float, unit: str, divider: float = 1.0) -> int:
    normalized = unit.lower()
    if normalized.startswith("分") or normalized.startswith("min"):
        minutes = amount
    else:
        minutes = amount * 60
    return max(1, int(round(minutes / divider)))


def _extract_daily_minutes(text: str) -> Optional[int]:
    number = r"(?P<num>\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)"
    units = r"(?P<unit>小时|h|hrs?|hours?|分钟|min(?:ute)?s?)"
    daily_patterns = [
        rf"(?:每天|每日|一天|daily|per day)[^\d一二两三四五六七八九十半]{{0,8}}{number}\s*{units}",
        rf"{number}\s*{units}[^。；,，\n]{{0,8}}(?:每天|每日|一天|daily|per day)",
    ]
    weekly_patterns = [
        rf"(?:每周|一周|weekly|per week)[^\d一二两三四五六七八九十半]{{0,8}}{number}\s*{units}",
        rf"{number}\s*{units}[^。；,，\n]{{0,8}}(?:每周|一周|weekly|per week)",
    ]

    for pattern in daily_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount = _number_from_text(match.group("num"))
            if amount:
                return _minutes_from_value(amount, match.group("unit"))

    for pattern in weekly_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount = _number_from_text(match.group("num"))
            if amount:
                return _minutes_from_value(amount, match.group("unit"), divider=7)

    return None


def _coerce_daily_minutes(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)) and value > 0:
        return int(round(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
            return max(1, int(round(float(stripped))))
        return _extract_daily_minutes(stripped)
    return None


def _extract_timeframe(text: str) -> Optional[Dict[str, Any]]:
    match = re.search(
        r"(?P<num>\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)\s*(?P<unit>个月|月|周|星期|天|年|days?|weeks?|months?|years?)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    amount = _number_from_text(match.group("num"))
    if not amount:
        return None
    unit = match.group("unit").lower()
    if unit in {"年", "year", "years"}:
        days = amount * 365
    elif unit in {"个月", "月", "month", "months"}:
        days = amount * 30
    elif unit in {"周", "星期", "week", "weeks"}:
        days = amount * 7
    else:
        days = amount
    return {"label": match.group(0).strip(), "days": max(1, int(round(days)))}


def _coerce_timeframe(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        label = str(value.get("label") or value.get("timeframe") or "").strip()
        days = value.get("days") or value.get("timeframe_days")
        try:
            day_count = int(days) if days is not None else None
        except (TypeError, ValueError):
            day_count = None
        if label or day_count:
            return {"label": label or f"{day_count}天", "days": day_count}
    if isinstance(value, (int, float)) and value > 0:
        return {"label": f"{int(value)}天", "days": int(value)}
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
            days = int(round(float(stripped)))
            return {"label": f"{days}天", "days": days}
        extracted = _extract_timeframe(stripped)
        if extracted:
            return extracted
        return {"label": stripped, "days": None}
    return None


def _extract_deadline(text: str) -> Optional[str]:
    patterns = [
        r"(?P<year>20\d{2})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})",
        r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            dt = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=timezone.utc,
            )
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


def _coerce_deadline(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        extracted = _extract_deadline(stripped)
        if extracted:
            return extracted
        parsed = _parse_datetime(stripped)
        if parsed:
            return parsed.date().isoformat()
    return None


def _days_until(deadline: Optional[str]) -> Optional[int]:
    parsed = _parse_datetime(deadline)
    if not parsed:
        return None
    today = _local_today()
    delta = (parsed.date() - today).days
    return max(1, delta) if delta >= 0 else None


def _clean_goal_name_candidate(value: str) -> Optional[str]:
    candidate = re.split(r"[\uff0c,\u3002\uff1b;!！?？\n\r]", value, maxsplit=1)[0]
    candidate = candidate.strip(" \t:：,，。.!！?？")
    for stop in _GOAL_TOPIC_INLINE_STOPS:
        if stop in candidate:
            candidate = candidate.split(stop, 1)[0].strip()
    changed = True
    while changed:
        changed = False
        for word in _GOAL_TOPIC_TRAILING_WORDS:
            if candidate.endswith(word) and len(candidate) > len(word):
                candidate = candidate[: -len(word)].strip()
                changed = True
    candidate = re.sub(r"\s+", " ", candidate).strip(" \t:：,，。.!！?？")
    if not candidate:
        return None
    has_topic_token = bool(re.search(r"[A-Za-z0-9+#.\u4e00-\u9fff]", candidate))
    if not has_topic_token or len(candidate) > 40:
        return None
    return candidate


def _extract_create_command_topic(text: str) -> Optional[str]:
    candidate = text.strip()
    if not candidate:
        return None
    changed = True
    while changed:
        changed = False
        for word in _GOAL_CREATE_LEADING_WORDS:
            if candidate.startswith(word):
                candidate = candidate[len(word) :].strip()
                changed = True
    for prefix in _GOAL_CREATE_COMMAND_PREFIXES:
        if not candidate.startswith(prefix):
            continue
        candidate = candidate[len(prefix) :].strip()
        changed = True
        while changed:
            changed = False
            for word in _GOAL_CREATE_FILLER_WORDS:
                if candidate.startswith(word):
                    candidate = candidate[len(word) :].strip()
                    changed = True
        return _clean_goal_name_candidate(candidate)
    return None


def _extract_goal_name(text: str) -> Optional[str]:
    lower = text.lower()
    for needle, label in _TOPIC_HINTS:
        if needle.lower() in lower:
            return label

    command_topic = _extract_create_command_topic(text)
    if command_topic:
        return command_topic

    match = re.search(
        r"(?:想学|学习|学会|掌握|入门|精通|准备|备考|攻克|刷)\s*([A-Za-z0-9+#. \u4e00-\u9fff]{2,32})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = match.group(1).strip(" ，,。.!！")
    for stop in [
        "找工作",
        "就业",
        "面试",
        "转行",
        "考试",
        "考证",
        "每天",
        "每日",
        "一天",
        "小时",
        "分钟",
    ]:
        if stop in value:
            value = value.split(stop)[0]
    value = re.sub(r"\s+", " ", value).strip(" ，,。.!！")
    return value or None


def _infer_purpose(text: str) -> Optional[str]:
    if any(word in text for word in _COURSE_TOPIC_KEYWORDS):
        return _EXAM_CERT_PURPOSE
    if any(word in text for word in ["找工作", "就业", "面试", "求职"]):
        return "找工作"
    if any(word in text for word in ["转行", "换工作"]):
        return "转行"
    if any(word in text for word in ["考试", "考证", "备考", "证书"]):
        return "考试/考证"
    if any(word in text for word in ["项目", "作品", "毕业设计"]):
        return "完成项目"
    if any(word in text for word in ["工作", "业务", "升职"]):
        return "提升工作能力"
    if any(word in text for word in ["兴趣", "爱好", "了解"]):
        return "兴趣探索"
    return None


def _infer_current_level(text: str) -> Optional[str]:
    if any(word in text for word in ["零基础", "小白", "没基础", "没有基础", "从零"]):
        return "零基础"
    if any(word in text for word in ["入门", "会一点", "刚开始"]):
        return "入门"
    if any(word in text for word in ["初级", "基础语法"]):
        return "初级"
    if any(word in text for word in ["中级", "做过项目", "有项目经验"]):
        return "中级"
    if any(word in text for word in ["高级", "熟练", "资深"]):
        return "高级"
    return None


def _infer_target_level(text: str) -> Optional[str]:
    if any(word in text for word in ["找工作", "就业", "面试", "求职"]):
        return "达到面试/就业水平"
    if any(word in text for word in ["独立项目", "做项目", "作品", "上线"]):
        return "能独立完成项目"
    if any(word in text for word in ["考试", "考证", "通过"]):
        return "通过考试"
    if any(word in text for word in ["系统掌握", "精通", "熟练掌握"]):
        return "系统掌握"
    if any(word in text for word in ["快速了解", "入门", "了解"]):
        return "快速了解"
    return None


def _infer_preference(text: str) -> Optional[str]:
    if any(word in text for word in ["项目", "实战", "作品", "动手"]):
        return "项目实战"
    if any(word in text for word in ["视频", "网课", "课程"]):
        return "视频课程"
    if any(word in text for word in ["文档", "书", "阅读"]):
        return "文档阅读"
    if any(word in text for word in ["刷题", "题目", "练题"]):
        return "刷题训练"
    if any(word in text for word in ["陪练", "互动", "问答", "AI"]):
        return "AI 陪练"
    return None


def _infer_goal_type(text: str, purpose: Optional[str]) -> str:
    if purpose == "考试/考证" or any(word in text for word in ["考试", "证书", "考证"]):
        return "certification"
    if purpose == "完成项目" or any(word in text for word in ["项目", "作品", "上线"]):
        return "project"
    if any(word in text.lower() for word in ["python", "java", "react", "vue", "javascript"]):
        return "skill"
    return "knowledge"


def _default_target_level(
    goal_type: str, purpose: Optional[str], goal_name: Optional[str]
) -> str:
    haystack = " ".join([goal_type or "", purpose or "", goal_name or ""])
    if purpose == _EXAM_CERT_PURPOSE or any(word in haystack for word in _COURSE_TOPIC_KEYWORDS):
        return "\u7cfb\u7edf\u638c\u63e1\u6838\u5fc3\u6982\u5ff5\u5e76\u80fd\u5b8c\u6210\u8bfe\u7a0b\u9898\u76ee"
    if goal_type == LearningGoalType.PROJECT.value:
        return "\u80fd\u72ec\u7acb\u5b8c\u6210\u53ef\u5c55\u793a\u9879\u76ee"
    if goal_type == LearningGoalType.SKILL.value:
        return "\u7cfb\u7edf\u638c\u63e1\u5e76\u80fd\u5b8c\u6210\u5b9e\u8df5\u4efb\u52a1"
    return "\u7cfb\u7edf\u638c\u63e1\u6838\u5fc3\u6982\u5ff5"


def _normalize_goal_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    aliases = {
        "技能": "skill",
        "skill": "skill",
        "知识": "knowledge",
        "knowledge": "knowledge",
        "考试": "certification",
        "考证": "certification",
        "certification": "certification",
        "项目": "project",
        "project": "project",
    }
    return aliases.get(raw)


def _profile_average_daily_minutes(profile: Dict[str, Any]) -> Optional[int]:
    weekday = _normalize_minutes(profile.get("weekday_minutes"))
    weekend = _normalize_minutes(profile.get("weekend_minutes"))
    if weekday is None and weekend is None:
        return None
    weekday = weekday or 0
    weekend = weekend or 0
    return int(round((weekday * 5 + weekend * 2) / 7))


def _student_context_from_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "nickname": profile.get("nickname") or "",
        "school": profile.get("school") or "",
        "major": profile.get("major") or "",
        "grade": profile.get("grade") or "",
        "semester": profile.get("semester") or "",
        "career_target": profile.get("career_target") or "",
        "baseline_level": profile.get("baseline_level") or "",
        "current_courses": _normalize_text_list(profile.get("current_courses")),
        "strengths": profile.get("strengths") or "",
        "weaknesses": profile.get("weaknesses") or "",
        "constraints": profile.get("constraints") or "",
    }


def _time_constraints_from_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    weekday = _normalize_minutes(profile.get("weekday_minutes"))
    weekend = _normalize_minutes(profile.get("weekend_minutes"))
    return {
        "weekday_minutes": weekday,
        "weekend_minutes": weekend,
        "weekly_minutes": (weekday or 0) * 5 + (weekend or 0) * 2
        if weekday is not None or weekend is not None
        else None,
        "preferred_study_time": profile.get("preferred_study_time") or "",
        "constraints": profile.get("constraints") or "",
    }


def _purpose_from_career_target(career_target: str) -> Optional[str]:
    if any(word in career_target for word in ["就业", "找工作", "实习", "面试"]):
        return "找工作"
    if any(word in career_target for word in ["考研", "保研", "考公", "考证", "考试"]):
        return "考试/考证"
    if any(word in career_target for word in ["竞赛", "项目", "作品"]):
        return "完成项目"
    if any(word in career_target for word in ["转专业", "转行"]):
        return "转行"
    return None


def _is_computing_major(major: str) -> bool:
    return any(
        word in major
        for word in ["计算机", "软件", "人工智能", "数据", "网络", "信息", "自动化"]
    )


def _missing_profile_fields(profile: Dict[str, Any]) -> List[str]:
    missing = []
    for field in ["major", "grade", "career_target", "baseline_level"]:
        if _blank(profile.get(field)):
            missing.append(field)
    weekday_minutes = _normalize_minutes(profile.get("weekday_minutes"))
    weekend_minutes = _normalize_minutes(profile.get("weekend_minutes"))
    if weekday_minutes is None or weekday_minutes <= 0:
        missing.append("weekday_minutes")
    if weekend_minutes is None or weekend_minutes <= 0:
        missing.append("weekend_minutes")
    if not _normalize_text_list(profile.get("learning_preferences")):
        missing.append("learning_preferences")
    return missing


def _profile_status(profile: Dict[str, Any]) -> Dict[str, Any]:
    missing = _missing_profile_fields(profile)
    return {
        "complete": len(missing) == 0,
        "missing_fields": missing,
        "completeness": round(1 - (len(missing) / 7), 2),
    }


def _profile_with_status(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {**profile, **_profile_status(profile)}


def _require_complete_learning_profile() -> Dict[str, Any]:
    profile = _load_learning_profile()
    status = _profile_status(profile)
    if not status["complete"]:
        raise HTTPException(
            status_code=428,
            detail={
                "message": "Learning profile is required before using the agent",
                **status,
            },
        )
    return profile


def _derive_tags(draft: Dict[str, Any], text: str) -> List[str]:
    tags = []
    for key in ["goal_name", "purpose", "current_level", "target_level", "preference"]:
        value = draft.get(key)
        if isinstance(value, str) and value and value not in tags:
            tags.append(value)
    for needle, label in _TOPIC_HINTS:
        if needle.lower() in text.lower() and label not in tags:
            tags.append(label)
    return tags[:8]


_INACTIVE_GOAL_STATUSES = {
    LearningGoalStatus.COMPLETED.value,
    LearningGoalStatus.ABANDONED.value,
}
_GOAL_DUPLICATE_THRESHOLD = 0.62
_GOAL_BROAD_ASCII_TOKENS = {
    "ai",
    "c",
    "c#",
    "c++",
    "css",
    "go",
    "html",
    "java",
    "javascript",
    "js",
    "ml",
    "python",
    "react",
    "sql",
    "ts",
    "vue",
}
_GOAL_TOKEN_ALIASES = {
    "js": "javascript",
    "py": "python",
    "ts": "typescript",
}
_GOAL_NAME_STOPWORDS = {
    "学习",
    "学会",
    "掌握",
    "了解",
    "入门",
    "基础",
    "进阶",
    "提升",
    "准备",
    "目标",
    "计划",
    "课程",
    "编程",
    "开发",
    "训练",
    "复习",
}


def _goal_candidate_name(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("goal_name") or candidate.get("name") or "").strip()


def _goal_candidate_text(candidate: Dict[str, Any]) -> str:
    requirement = candidate.get("requirement") if isinstance(candidate.get("requirement"), dict) else {}
    parts: List[str] = [
        _goal_candidate_name(candidate),
        str(candidate.get("goal_type") or ""),
        str(candidate.get("purpose") or requirement.get("purpose") or ""),
        str(candidate.get("target_level") or requirement.get("target_level") or ""),
        str(candidate.get("preference") or requirement.get("preference") or ""),
    ]
    for tag in candidate.get("tags", []) or []:
        parts.append(str(tag))
    return " ".join(part for part in parts if part)


def _canonical_goal_name(value: str) -> str:
    normalized = re.sub(r"[\s,，、。.!！？:：;；/\\|·_-]+", "", value.lower())
    for stopword in _GOAL_NAME_STOPWORDS:
        normalized = normalized.replace(stopword, "")
    return normalized


def _ascii_goal_tokens(value: str) -> set[str]:
    return {
        _GOAL_TOKEN_ALIASES.get(token, token)
        for token in re.findall(r"[a-z0-9+#.]+", value.lower())
        if token
    }


def _goal_tokens(value: str) -> set[str]:
    tokens = _ascii_goal_tokens(value)
    normalized = value.lower()

    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(sequence) >= 2 and sequence not in _GOAL_NAME_STOPWORDS:
            tokens.add(sequence)
        for width in (2, 3):
            if len(sequence) < width:
                continue
            for index in range(0, len(sequence) - width + 1):
                token = sequence[index : index + width]
                if token not in _GOAL_NAME_STOPWORDS:
                    tokens.add(token)

    return tokens


def _containment_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _goal_similarity_score(candidate: Dict[str, Any], goal: Dict[str, Any]) -> float:
    candidate_name = _goal_candidate_name(candidate)
    goal_name = _goal_candidate_name(goal)
    candidate_canonical = _canonical_goal_name(candidate_name)
    goal_canonical = _canonical_goal_name(goal_name)
    candidate_ascii = _ascii_goal_tokens(candidate_name)
    goal_ascii = _ascii_goal_tokens(goal_name)

    if candidate_canonical and candidate_canonical == goal_canonical:
        return 1.0

    has_ascii_conflict = bool(candidate_ascii and goal_ascii and not candidate_ascii.intersection(goal_ascii))
    if (
        not has_ascii_conflict
        and min(len(candidate_canonical), len(goal_canonical)) >= 4
        and (candidate_canonical in goal_canonical or goal_canonical in candidate_canonical)
    ):
        return 0.92

    candidate_name_tokens = _goal_tokens(candidate_name)
    goal_name_tokens = _goal_tokens(goal_name)
    candidate_tokens = _goal_tokens(_goal_candidate_text(candidate))
    goal_tokens = _goal_tokens(_goal_candidate_text(goal))
    name_score = _containment_score(candidate_name_tokens, goal_name_tokens)
    full_score = _containment_score(candidate_tokens, goal_tokens) * 0.86
    shared_name_tokens = candidate_name_tokens.intersection(goal_name_tokens)

    # Metadata such as "考试/考证", "系统掌握" or "刷题训练" often matches
    # across unrelated university courses. Only let full-text similarity help
    # duplicate detection after the course/topic names already share signal.
    if not shared_name_tokens:
        full_score = min(full_score, 0.45)

    score = max(name_score, full_score)

    if (
        len(shared_name_tokens) == 1
        and next(iter(shared_name_tokens)) in _GOAL_BROAD_ASCII_TOKENS
        and not any(re.search(r"[\u4e00-\u9fff]", token) for token in shared_name_tokens)
    ):
        score = min(score, 0.55)

    candidate_goal_type = str(candidate.get("goal_type") or "").strip()
    goal_type = str(goal.get("goal_type") or "").strip()
    if score > 0 and candidate_goal_type and candidate_goal_type == goal_type:
        score += 0.06

    return min(1.0, score)


def _find_similar_active_goal(
    candidate: Dict[str, Any], goals: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    best_goal: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for goal in goals.values():
        if str(goal.get("status") or "") in _INACTIVE_GOAL_STATUSES:
            continue

        score = _goal_similarity_score(candidate, goal)
        if score > best_score:
            best_goal = goal
            best_score = score

    if best_goal and best_score >= _GOAL_DUPLICATE_THRESHOLD:
        return best_goal
    return None


def _goal_records(
    goal_id: str, plans: Dict[str, Dict[str, Any]], tasks: Dict[str, Dict[str, Any]]
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    goal_plans = [plan for plan in plans.values() if plan.get("goal_id") == goal_id]
    goal_plan_ids = {str(plan.get("id")) for plan in goal_plans}
    plan_order_by_id = _plan_order_by_id(plans)
    goal_tasks = [
        task
        for task in tasks.values()
        if task.get("goal_id") == goal_id or str(task.get("plan_id")) in goal_plan_ids
    ]
    goal_plans.sort(key=lambda plan: _plan_sort_key(plan, plan_order_by_id.get(str(plan.get("id") or ""), 0)))
    goal_tasks.sort(
        key=lambda task: (
            plan_order_by_id.get(str(task.get("plan_id") or ""), 0),
            int(task.get("position") or 0),
            str(task.get("scheduled_date") or ""),
            str(task.get("created_at") or ""),
            str(task.get("id") or ""),
        )
    )
    return goal_plans, goal_tasks


def _related_goals(
    draft: Dict[str, Any], goals: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not _goal_candidate_name(draft):
        return []
    related: List[Dict[str, Any]] = []
    for goal in goals.values():
        if str(goal.get("status") or "") in _INACTIVE_GOAL_STATUSES:
            continue
        score = _goal_similarity_score(draft, goal)
        if score >= _GOAL_DUPLICATE_THRESHOLD:
            related.append(
                {
                    "id": goal.get("id"),
                    "name": goal.get("name"),
                    "status": goal.get("status"),
                    "progress": goal.get("progress", 0),
                    "score": round(score, 3),
                }
            )
    related.sort(key=lambda item: item["score"], reverse=True)
    return related[:5]


def _is_temp_requirement(text: str) -> bool:
    markers = ["临时", "今天复习", "讲一下", "解释一下", "帮我看看", "出几道题", "练一下"]
    return any(marker in text for marker in markers)


def _classify_requirement(
    text: str,
    draft: Dict[str, Any],
    goals: Dict[str, Dict[str, Any]],
    existing_goal_id: Optional[str],
) -> str:
    if existing_goal_id and existing_goal_id in goals:
        return "goal_extension"
    if _is_temp_requirement(text):
        return "temp_task"
    return "new_goal"


_REQUIREMENT_DRAFT_FIELDS = {
    "goal_name",
    "purpose",
    "current_level",
    "target_level",
    "daily_minutes",
    "timeframe",
    "timeframe_days",
    "deadline",
    "preference",
    "goal_type",
    "priority",
    "tags",
    "existing_goal_id",
}

_VALID_REQUIREMENT_CLASSIFICATIONS = {"new_goal", "goal_extension", "temp_task"}


def _apply_requirement_field(draft: Dict[str, Any], field: str, value: Any) -> None:
    if _blank(value) and field not in {"deadline", "timeframe_days"}:
        return

    if field == "daily_minutes":
        minutes = _coerce_daily_minutes(value)
        if minutes:
            draft[field] = max(10, min(360, minutes))
        return

    if field == "timeframe":
        parsed_timeframe = _coerce_timeframe(value)
        if parsed_timeframe:
            draft["timeframe"] = parsed_timeframe["label"]
            if parsed_timeframe.get("days"):
                draft["timeframe_days"] = parsed_timeframe["days"]
        return

    if field == "timeframe_days":
        try:
            days = int(value)
        except (TypeError, ValueError):
            return
        if days > 0:
            draft[field] = max(1, min(3650, days))
        return

    if field == "deadline":
        deadline = _coerce_deadline(value)
        if deadline:
            draft[field] = deadline
        return

    if field == "goal_type":
        goal_type = _normalize_goal_type(value)
        if goal_type:
            draft[field] = goal_type
        return

    if field == "priority":
        try:
            draft[field] = max(1, min(5, int(value)))
        except (TypeError, ValueError):
            pass
        return

    if field == "tags":
        tags = _normalize_text_list(value)
        if tags:
            draft[field] = tags[:8]
        return

    if field == "classification":
        classification = str(value or "").strip()
        if classification in _VALID_REQUIREMENT_CLASSIFICATIONS:
            draft[field] = classification
        return

    if field in {
        "goal_name",
        "purpose",
        "current_level",
        "target_level",
        "preference",
        "existing_goal_id",
    }:
        text_value = str(value).strip()
        if text_value:
            draft[field] = text_value[:160]


def _apply_requirement_updates(
    draft: Dict[str, Any], updates: Dict[str, Any]
) -> Dict[str, Any]:
    for field, value in updates.items():
        if field in _REQUIREMENT_DRAFT_FIELDS or field == "classification":
            _apply_requirement_field(draft, field, value)
    return draft


def _finalize_requirement_draft(
    draft: Dict[str, Any],
    text: str,
    goals: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if draft.get("timeframe_days") and not draft.get("timeframe"):
        try:
            draft["timeframe"] = f"{int(draft['timeframe_days'])}天"
        except (TypeError, ValueError):
            pass

    if draft.get("timeframe_days") and not draft.get("deadline"):
        try:
            draft["deadline"] = (
                _local_today() + timedelta(days=int(draft["timeframe_days"]))
            ).isoformat()
        except (TypeError, ValueError):
            pass

    if draft.get("deadline") and not draft.get("timeframe_days"):
        days = _days_until(draft["deadline"])
        if days:
            draft["timeframe"] = f"{days}天"
            draft["timeframe_days"] = days

    if not draft.get("tags"):
        draft["tags"] = _derive_tags(draft, text)

    existing_goal_id = draft.get("existing_goal_id")
    if draft.get("classification") not in _VALID_REQUIREMENT_CLASSIFICATIONS:
        draft["classification"] = _classify_requirement(
            text, draft, goals, existing_goal_id if isinstance(existing_goal_id, str) else None
        )

    related = _related_goals(draft, goals)
    if (
        draft["classification"] == "goal_extension"
        and existing_goal_id
        and related
        and not draft.get("existing_goal_id")
    ):
        draft["existing_goal_id"] = related[0]["id"]
    return draft


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}

    fence_match = re.search(
        r"```(?:json)?\s*([\s\S]*?)\s*```",
        raw,
        re.IGNORECASE,
    )
    if fence_match:
        raw = fence_match.group(1).strip()

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strip_skill_frontmatter(content: str) -> str:
    text = content.lstrip("\ufeff")
    if not text.startswith("---"):
        return text.strip()

    match = re.match(r"^---\s*\n.*?\n---\s*\n?", text, re.DOTALL)
    if not match:
        return text.strip()
    return text[match.end() :].strip()


def _load_learning_skill(skill_name: str) -> str:
    skill_dir_name = _LEARNING_SKILL_DIRS.get(skill_name)
    if not skill_dir_name:
        raise LearningSkillError(skill_name, f"Unknown learning skill: {skill_name}")

    skill_path = _LEARNING_SKILLS_DIR / skill_dir_name / "SKILL.md"
    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LearningSkillError(
            skill_name,
            f"Learning skill file is unavailable: {skill_path}",
        ) from exc

    body = _strip_skill_frontmatter(content)
    if not body:
        raise LearningSkillError(skill_name, f"Learning skill is empty: {skill_path}")
    return body


def _learning_skill_system_message(skill_name: str) -> str:
    return (
        f"{_load_learning_skill(skill_name)}\n\n"
        "Runtime contract: return exactly one JSON object that matches this skill's schema. "
        "Do not return Markdown, comments, code fences, or explanatory text."
    )


def _extract_learning_llm_text(response: Any) -> str:
    try:
        message = response.choices[0].message
        parsed = getattr(message, "parsed", None)
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") if part.get("type") == "text" else None
                else:
                    text = getattr(part, "text", None)
                if isinstance(text, str):
                    parts.append(text)
            return "".join(parts)
    except (AttributeError, IndexError, TypeError):
        pass
    if isinstance(response, dict):
        try:
            message = response["choices"][0]["message"]
            parsed = message.get("parsed") if isinstance(message, dict) else None
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                return content
        except (KeyError, IndexError, TypeError):
            pass
    return ""


def _learning_json_response_format(label: str) -> Dict[str, Any]:
    if label == "course-planner":
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "course_plan",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["g", "p"],
                        "properties": {
                            "g": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["n", "d", "type", "h", "tags", "pre"],
                                "properties": {
                                    "n": {"type": "string"},
                                    "d": {"type": "string"},
                                    "type": {
                                        "type": "string",
                                        "enum": ["skill", "knowledge", "certification", "project"],
                                    },
                                    "h": {"type": "number"},
                                    "tags": {"type": "array", "items": {"type": "string"}},
                                    "pre": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                            "p": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["n", "d", "t"],
                                    "properties": {
                                        "n": {"type": "string"},
                                        "d": {"type": "string"},
                                        "t": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["t", "d", "diff", "m", "p"],
                                                "properties": {
                                                    "t": {"type": "string"},
                                                    "d": {"type": "string"},
                                                    "diff": {
                                                        "type": "string",
                                                        "enum": ["easy", "medium", "hard"],
                                                    },
                                                    "m": {"type": "integer"},
                                                    "p": {"type": "integer"},
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }
        }
    return {"response_format": {"type": "json_object"}}


def _learning_auxiliary_task_name(label: str) -> str:
    return f"learning_{re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_') or 'json'}"


def _current_learning_model_config() -> tuple[str, Optional[str]]:
    env_model = (
        os.environ.get("HERMES_MODEL", "")
        or os.environ.get("HERMES_INFERENCE_MODEL", "")
    ).strip()
    cfg = {}
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
    except Exception:
        _log.debug("Failed to load config for learning model", exc_info=True)

    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    provider = os.environ.get("HERMES_TUI_PROVIDER", "").strip() or None
    model = env_model

    if isinstance(model_cfg, dict):
        if not model:
            model = str(
                model_cfg.get("default")
                or model_cfg.get("model")
                or model_cfg.get("name")
                or ""
            ).strip()
        provider = provider or str(model_cfg.get("provider") or "").strip() or None
    elif isinstance(model_cfg, str):
        model = model or model_cfg.strip()

    return model or "anthropic/claude-sonnet-4", provider


def _load_learning_provider_routing() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        routing = (load_config() or {}).get("provider_routing", {}) or {}
        return routing if isinstance(routing, dict) else {}
    except Exception:
        return {}


def _load_learning_fallback_model() -> List[Dict[str, Any]]:
    try:
        from hermes_cli.config import load_config
        from hermes_cli.fallback_config import get_fallback_chain

        return get_fallback_chain(load_config() or {})
    except Exception:
        return []


def _build_learning_llm_agent(max_tokens: Optional[int] = None):
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent

    model, provider = _current_learning_model_config()
    runtime = resolve_runtime_provider(requested=provider, target_model=model)
    routing = _load_learning_provider_routing()
    agent = AIAgent(
        model=model,
        max_iterations=_LEARNING_LLM_MAX_ITERATIONS,
        provider=runtime.get("provider"),
        base_url=runtime.get("base_url"),
        api_key=runtime.get("api_key"),
        api_mode=runtime.get("api_mode"),
        acp_command=runtime.get("command"),
        acp_args=runtime.get("args"),
        credential_pool=runtime.get("credential_pool"),
        quiet_mode=True,
        enabled_toolsets=[],
        providers_allowed=routing.get("only"),
        providers_ignored=routing.get("ignore"),
        providers_order=routing.get("order"),
        provider_sort=routing.get("sort"),
        provider_require_parameters=routing.get("require_parameters", False),
        provider_data_collection=routing.get("data_collection"),
        platform="desktop",
        session_id=f"learning-{_generate_id()}",
        skip_context_files=True,
        skip_memory=True,
        fallback_model=_load_learning_fallback_model(),
        max_tokens=max_tokens,
    )
    agent._persist_disabled = True
    agent._session_db = None
    return agent


def _call_learning_llm_json(
    *,
    system_message: str,
    payload: Dict[str, Any],
    label: str,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    user_message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        from agent.auxiliary_client import call_llm
        from hermes_cli.runtime_provider import resolve_runtime_provider

        model, provider = _current_learning_model_config()
        runtime = resolve_runtime_provider(requested=provider, target_model=model)
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]

        def call_once(extra_body: Optional[Dict[str, Any]]):
            return call_llm(
                task=_learning_auxiliary_task_name(label),
                provider=runtime.get("provider"),
                model=model,
                base_url=runtime.get("base_url"),
                api_key=runtime.get("api_key"),
                api_mode=runtime.get("api_mode"),
                main_runtime=runtime,
                messages=messages,
                temperature=0.1,
                max_tokens=max_tokens,
                timeout=_LEARNING_LLM_BACKGROUND_TIMEOUT_SECONDS,
                extra_body=extra_body,
            )

        used_response_format = True
        try:
            response = call_once(_learning_json_response_format(label))
        except Exception as exc:
            error_text = str(exc).lower()
            if (
                "response_format" not in error_text
                and "json_object" not in error_text
                and "json_schema" not in error_text
                and "schema" not in error_text
            ):
                raise
            _log.info(
                "Learning LLM provider rejected JSON response_format for %s; retrying with prompt-only JSON contract",
                label,
            )
            used_response_format = False
            response = call_once(None)
    except Exception:
        _log.exception("Learning LLM call failed for %s", label)
        return {}

    text = _extract_learning_llm_text(response)
    parsed = _extract_json_object(text)
    if not parsed and used_response_format:
        _log.info(
            "Learning LLM returned no usable JSON for %s with response_format; retrying same model without response_format",
            label,
        )
        try:
            response = call_once(None)
        except Exception:
            _log.exception("Learning LLM prompt-only retry failed for %s", label)
            return {}
        text = _extract_learning_llm_text(response)
        parsed = _extract_json_object(text)

    if not parsed:
        _log.warning("Learning LLM returned non-JSON for %s: %s", label, text[:500])
    return parsed


def _invoke_learning_llm_json_for_thread(
    *,
    system_message: str,
    payload: Dict[str, Any],
    label: str,
    max_tokens: Optional[int],
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "system_message": system_message,
        "payload": payload,
        "label": label,
    }
    if max_tokens is not None:
        try:
            signature = inspect.signature(_call_learning_llm_json)
            supports_max_tokens = "max_tokens" in signature.parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            supports_max_tokens = False
        if supports_max_tokens:
            kwargs["max_tokens"] = max_tokens

    return _call_learning_llm_json(**kwargs)


async def _call_learning_llm_json_async(
    *,
    system_message: str,
    payload: Dict[str, Any],
    label: str,
    timeout_seconds: Optional[float] = None,
    required: bool = False,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    call = asyncio.to_thread(
        _invoke_learning_llm_json_for_thread,
        system_message=system_message,
        payload=payload,
        label=label,
        max_tokens=max_tokens,
    )
    if timeout_seconds is None:
        result = await call
        if required and not result:
            raise LearningSkillError(
                label,
                f"{label} did not return a valid JSON object",
            )
        return result

    try:
        result = await asyncio.wait_for(call, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        _log.warning(
            "Learning skill call timed out for %s after %.1fs",
            label,
            timeout_seconds,
        )
        if required:
            raise LearningSkillTimeoutError(label, timeout_seconds)
        return {}

    if required and not result:
        raise LearningSkillError(
            label,
            f"{label} did not return a valid JSON object",
        )
    return result


def _learning_skill_http_exception(exc: LearningSkillError) -> HTTPException:
    if isinstance(exc, LearningSkillTimeoutError):
        if exc.skill_name == "course-planner":
            message = "模型仍在生成学习计划，已保留本次请求；请稍后重试，会复用同一次模型结果。"
        else:
            message = "模型生成超时，请稍后重试，或把学习周期、基础和每天可用时间写得更具体。"
    else:
        message = "模型没有返回可用的结构化结果，请重试或补充更明确的学习需求。"
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "message": message,
            "skill": exc.skill_name,
        },
    )


def _compact_goals_for_prompt(
    goals: Dict[str, Dict[str, Any]], limit: int = 8
) -> List[Dict[str, Any]]:
    active_goals = [
        goal
        for goal in goals.values()
        if str(goal.get("status") or "") not in _INACTIVE_GOAL_STATUSES
    ]
    active_goals.sort(key=lambda goal: str(goal.get("updated_at") or ""), reverse=True)
    return [
        {
            "id": goal.get("id"),
            "name": goal.get("name"),
            "description": goal.get("description"),
            "goal_type": goal.get("goal_type"),
            "status": goal.get("status"),
            "progress": goal.get("progress", 0),
            "tags": goal.get("tags", []),
        }
        for goal in active_goals[:limit]
    ]


async def _generate_requirement_draft_with_llm(
    *,
    message: str,
    answers: Dict[str, Any],
    profile: Dict[str, Any],
    goals: Dict[str, Dict[str, Any]],
    existing_goal_id: Optional[str],
) -> Dict[str, Any]:
    system_message = _learning_skill_system_message("requirement-intake")
    payload = {
        "today": _local_today().isoformat(),
        "message": message,
        "answers": answers,
        "student_profile": _compact_profile_context(profile),
        "existing_goal_id": existing_goal_id,
        "active_goals": _compact_goals_for_prompt(goals),
    }
    parsed = await _call_learning_llm_json_async(
        system_message=system_message,
        payload=payload,
        label="requirement-intake",
        timeout_seconds=_LEARNING_REQUIREMENT_LLM_TIMEOUT_SECONDS,
        max_tokens=_LEARNING_REQUIREMENT_MAX_OUTPUT_TOKENS,
    )
    draft = parsed.get("draft") if isinstance(parsed.get("draft"), dict) else parsed
    return draft if isinstance(draft, dict) else {}


def _build_requirement_draft(
    text: str,
    answers: Dict[str, Any],
    goals: Dict[str, Dict[str, Any]],
    existing_goal_id: Optional[str],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    clean_text = text.strip()
    purpose = _infer_purpose(clean_text)
    effective_purpose = purpose or _purpose_from_career_target(
        str(profile.get("career_target") or "")
    )
    profile_preferences = _normalize_text_list(profile.get("learning_preferences"))
    profile_daily_minutes = _profile_average_daily_minutes(profile)
    goal_name = _extract_goal_name(clean_text)
    if not effective_purpose and goal_name:
        effective_purpose = _SYSTEM_LEARNING_PURPOSE
    goal_type = _infer_goal_type(clean_text, effective_purpose)
    target_level = _infer_target_level(clean_text) or (
        _default_target_level(goal_type, effective_purpose, goal_name)
        if goal_name
        else None
    )
    draft: Dict[str, Any] = {
        "raw_text": clean_text,
        "goal_name": goal_name,
        "purpose": effective_purpose,
        "current_level": _infer_current_level(clean_text)
        or str(profile.get("baseline_level") or "").strip()
        or None,
        "target_level": target_level,
        "daily_minutes": _extract_daily_minutes(clean_text) or profile_daily_minutes,
        "deadline": _extract_deadline(clean_text),
        "preference": _infer_preference(clean_text)
        or (profile_preferences[0] if profile_preferences else None),
        "goal_type": goal_type,
        "priority": 2,
        "student_context": _student_context_from_profile(profile),
        "time_constraints": _time_constraints_from_profile(profile),
    }

    timeframe = _extract_timeframe(clean_text)
    if timeframe:
        draft["timeframe"] = timeframe["label"]
        draft["timeframe_days"] = timeframe["days"]

    if existing_goal_id:
        draft["existing_goal_id"] = existing_goal_id

    _apply_requirement_updates(draft, answers)
    return _finalize_requirement_draft(draft, clean_text, goals)


def _missing_requirement_fields(draft: Dict[str, Any]) -> List[str]:
    missing = []
    for field in _REQUIRED_REQUIREMENT_FIELDS:
        if field == "timeframe":
            if (
                not draft.get("timeframe")
                and not draft.get("deadline")
                and not draft.get("timeframe_days")
            ):
                missing.append(field)
            continue
        if field == "daily_minutes":
            if not draft.get(field):
                missing.append(field)
            continue
        if _blank(draft.get(field)):
            missing.append(field)
    return missing


def _requirement_questions(missing: List[str]) -> List[Dict[str, Any]]:
    return [
        {"field": field, **_FIELD_QUESTIONS[field]}
        for field in missing
        if field in _FIELD_QUESTIONS
    ]


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dynamic_requirement_estimate(draft: Dict[str, Any]) -> Dict[str, Any]:
    goal_type = str(draft.get("goal_type") or "skill")
    goal_name = str(draft.get("goal_name") or "")
    purpose = str(draft.get("purpose") or "")
    current_level = str(draft.get("current_level") or "")
    target_level = str(draft.get("target_level") or "")
    preference = str(draft.get("preference") or "")
    tags = " ".join(str(tag) for tag in draft.get("tags", []) or [])
    student_context = draft.get("student_context") if isinstance(draft.get("student_context"), dict) else {}
    time_constraints = draft.get("time_constraints") if isinstance(draft.get("time_constraints"), dict) else {}
    strengths = str(student_context.get("strengths") or "")
    weaknesses = str(student_context.get("weaknesses") or "")
    current_courses = " ".join(str(item) for item in student_context.get("current_courses", []) or [])
    major = str(student_context.get("major") or "")
    profile_text = " ".join([major, strengths, weaknesses, current_courses])
    full_text = " ".join([goal_name, purpose, target_level, preference, tags, profile_text]).lower()

    topic_hours = [
        (("高等数学", "高数", "calculus"), 96.0),
        (("数据结构", "算法"), 88.0),
        (("线性代数", "线代"), 64.0),
        (("概率论", "数理统计"), 72.0),
        (("大学物理", "大物"), 72.0),
        (("考研数学",), 180.0),
        (("英语六级", "英语四级", "四六级"), 76.0),
        (("java", "后端", "spring"), 132.0),
        (("python", "数据分析"), 86.0),
        (("react", "vue", "前端"), 82.0),
        (("机器学习", "深度学习", "人工智能"), 128.0),
    ]
    base_hours = None
    for keywords, hours in topic_hours:
        if any(keyword.lower() in full_text for keyword in keywords):
            base_hours = hours
            break

    if base_hours is None:
        base_hours = {
            "skill": 90.0,
            "knowledge": 62.0,
            "certification": 118.0,
            "project": 86.0,
        }.get(goal_type, 74.0)

    if any(word in current_level for word in ["零基础", "入门", "薄弱"]):
        base_hours *= 1.22
    elif any(word in current_level for word in ["初级", "一般"]):
        base_hours *= 1.08
    elif any(word in current_level for word in ["中级", "熟悉"]):
        base_hours *= 0.86
    elif any(word in current_level for word in ["高级", "熟练"]):
        base_hours *= 0.72

    if any(word in target_level + purpose for word in ["保研", "考研", "竞赛", "面试", "就业"]):
        base_hours *= 1.18
    elif any(word in target_level + purpose for word in ["期末", "考试", "通过"]):
        base_hours *= 1.08
    elif any(word in target_level for word in ["快速了解", "入门了解"]):
        base_hours *= 0.68

    if any(word in weaknesses for word in ["薄弱", "差", "不会", "不熟", "欠缺", "数学", "算法", goal_name]):
        base_hours *= 1.12
    if any(word in strengths for word in ["数学", "算法", "编程", "项目", "基础好", "熟悉"]):
        base_hours *= 0.92
    if goal_name and goal_name in current_courses:
        base_hours *= 0.9
    if major and _is_computing_major(major) and any(word in full_text for word in ["编程", "开发", "后端", "前端", "数据结构", "算法"]):
        base_hours *= 0.92
    elif major and not _is_computing_major(major) and any(word in full_text for word in ["编程", "开发", "算法", "机器学习"]):
        base_hours *= 1.1
    if "刷题" in preference:
        base_hours *= 1.04
    elif any(word in preference for word in ["项目", "实战"]):
        base_hours *= 1.08

    weekday_minutes = _normalize_minutes(time_constraints.get("weekday_minutes"))
    weekend_minutes = _normalize_minutes(time_constraints.get("weekend_minutes"))
    weekly_minutes = _normalize_minutes(time_constraints.get("weekly_minutes"))
    if weekly_minutes is None and (weekday_minutes is not None or weekend_minutes is not None):
        weekly_minutes = (weekday_minutes or 0) * 5 + (weekend_minutes or weekday_minutes or 0) * 2

    daily_minutes = _normalize_minutes(draft.get("daily_minutes"))
    if weekly_minutes and weekly_minutes > 0:
        daily_hours = weekly_minutes / 7 / 60
    else:
        daily_hours = float(daily_minutes or 0) / 60

    available_days_value = _float_or_none(draft.get("timeframe_days"))
    available_days = int(available_days_value) if available_days_value and available_days_value > 0 else _days_until(draft.get("deadline"))
    available_hours = round(float(available_days) * daily_hours, 1) if available_days and daily_hours else None
    base_hours = round(max(12.0, min(500.0, base_hours)), 1)
    estimated_days = int(round(base_hours / daily_hours + 0.499)) if daily_hours else None

    if available_hours:
        ratio = available_hours / base_hours
        confidence = round(min(0.95, max(0.25, ratio)), 2)
        fit = "comfortable" if ratio >= 1.15 else "tight" if ratio >= 0.75 else "at_risk"
    else:
        confidence = 0.55
        fit = "unknown"

    if any(word in purpose + target_level for word in ["考试", "通过", "保研", "考研"]):
        phase_specs = [
            ("诊断与路线", 0.18),
            ("核心输入", 0.32),
            ("练习实战", 0.36),
            ("复盘冲刺", 0.14),
        ]
    elif any(word in preference + goal_type for word in ["项目", "project"]):
        phase_specs = [
            ("诊断与路线", 0.12),
            ("核心输入", 0.28),
            ("练习实战", 0.45),
            ("复盘冲刺", 0.15),
        ]
    else:
        phase_specs = [
            ("诊断与路线", 0.15),
            ("核心输入", 0.35),
            ("练习实战", 0.35),
            ("复盘冲刺", 0.15),
        ]

    phases = []
    for name, weight in phase_specs:
        phase_hours = round(base_hours * weight, 1)
        phase_days = int(round(phase_hours / daily_hours + 0.499)) if daily_hours else None
        phases.append({"name": name, "hours": phase_hours, "days": phase_days})

    return {
        "estimated_hours": base_hours,
        "estimated_days": estimated_days,
        "available_days": available_days,
        "available_hours": available_hours,
        "confidence": confidence,
        "fit": fit,
        "phases": phases,
    }


def _estimate_requirement(draft: Dict[str, Any]) -> Dict[str, Any]:
    return _dynamic_requirement_estimate(draft)


def _requirement_period_days(
    draft: Dict[str, Any], estimate: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    value = _float_or_none(draft.get("timeframe_days"))
    if value and value > 0:
        return max(1, int(round(value)))

    deadline_days = _days_until(draft.get("deadline"))
    if deadline_days:
        return max(1, deadline_days)

    if isinstance(estimate, dict):
        estimated_days = _float_or_none(estimate.get("available_days"))
        if estimated_days and estimated_days > 0:
            return max(1, int(round(estimated_days)))

    return None


def _sync_requirement_daily_minutes(
    draft: Dict[str, Any], estimate: Dict[str, Any]
) -> Dict[str, Any]:
    estimated_hours = _float_or_none(estimate.get("estimated_hours"))
    period_days = _requirement_period_days(draft, estimate)
    if estimated_hours and period_days:
        draft["daily_minutes"] = max(
            1,
            min(14 * 60, int(round(estimated_hours * 60 / period_days))),
        )
    return draft


def _goal_description_from_requirement(draft: Dict[str, Any]) -> str:
    student_context = draft.get("student_context") or {}
    time_constraints = draft.get("time_constraints") or {}
    parts = [
        f"目标：{draft.get('goal_name')}",
        f"用途：{draft.get('purpose')}",
        f"基础：{draft.get('current_level')}",
        f"目标水平：{draft.get('target_level')}",
        f"学习偏好：{draft.get('preference')}",
    ]
    if student_context.get("major") or student_context.get("grade"):
        parts.append(
            f"学生画像：{student_context.get('grade') or '-'}，"
            f"{student_context.get('major') or '-'}"
        )
    if time_constraints.get("weekly_minutes") is not None:
        parts.append(
            f"时间约束：工作日 {time_constraints.get('weekday_minutes') or 0} 分钟/天，"
            f"周末 {time_constraints.get('weekend_minutes') or 0} 分钟/天"
        )
    if student_context.get("constraints"):
        parts.append(f"近期限制：{student_context.get('constraints')}")
    if draft.get("raw_text"):
        parts.append(f"原始需求：{draft.get('raw_text')}")
    return "\n".join(part for part in parts if part)


def _minutes_for_study_date(day: Any, draft: Dict[str, Any]) -> int:
    daily_minutes = int(draft.get("daily_minutes") or 60)
    constraints = draft.get("time_constraints") or {}
    weekday = _normalize_minutes(constraints.get("weekday_minutes"))
    weekend = _normalize_minutes(constraints.get("weekend_minutes"))
    if weekday is None and weekend is None:
        return max(20, min(180, daily_minutes))
    if weekday == 0 and weekend == 0:
        return max(20, min(180, daily_minutes))
    minutes = weekend if day.weekday() >= 5 else weekday
    if minutes is None:
        minutes = daily_minutes
    return max(0, min(240, minutes))


def _coerce_positive_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 10_000) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _coerce_positive_float(value: Any, default: float, *, minimum: float = 1.0, maximum: float = 10_000.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return round(max(minimum, min(maximum, parsed)), 1)


def _text_or_empty(value: Any, limit: int = 400) -> str:
    return str(value or "").strip()[:limit]


def _task_scheduled_date(task: Dict[str, Any]) -> Optional[date]:
    parsed = _parse_datetime(str(task.get("scheduled_date") or ""))
    return parsed.date() if parsed else None


def _task_scheduled_day(
    task: Dict[str, Any], fallback_day: date
) -> date:
    return _task_scheduled_date(task) or fallback_day


def _profile_daily_capacity(day: date, profile: Dict[str, Any]) -> int:
    weekday_minutes = _normalize_minutes(profile.get("weekday_minutes"))
    weekend_minutes = _normalize_minutes(profile.get("weekend_minutes"))
    if weekday_minutes == 0 and weekend_minutes == 0:
        return 60
    minutes = weekend_minutes if day.weekday() >= 5 else weekday_minutes
    if minutes is None:
        minutes = _profile_average_daily_minutes(profile) or 60
    return max(0, min(8 * 60, minutes))


def _task_remaining_minutes(task: Dict[str, Any]) -> int:
    if task.get("status") == TaskStatus.COMPLETED.value:
        return 0
    return _coerce_positive_int(
        task.get("estimated_minutes"),
        30,
        minimum=1,
        maximum=14 * 60,
    )


def _goal_target_day(goal: Dict[str, Any]) -> Optional[date]:
    parsed = _parse_datetime(str(goal.get("target_date") or ""))
    return _local_date_from(parsed) if parsed else None


def _goal_remaining_minutes(
    goal_id: str,
    tasks: Dict[str, Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
) -> int:
    return sum(
        _task_remaining_minutes(task)
        for task in tasks.values()
        if str(task.get("goal_id") or "") == goal_id
        and _is_daily_schedule_eligible(task, goals)
    )


def _deadline_pressure_capacity(
    today: date,
    tasks: Dict[str, Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
) -> int:
    pressure_minutes = 0

    for goal_id, goal in goals.items():
        if str(goal.get("status") or "") in _INACTIVE_GOAL_STATUSES:
            continue
        target_day = _goal_target_day(goal)
        if not target_day:
            continue
        remaining_minutes = _goal_remaining_minutes(goal_id, tasks, goals)
        if remaining_minutes <= 0:
            continue
        days_left = max(1, (target_day - today).days)
        pressure_minutes += max(1, int((remaining_minutes + days_left - 1) // days_left))

    return min(14 * 60, pressure_minutes)


def _dynamic_daily_capacity(
    today: date,
    profile: Dict[str, Any],
    tasks: Dict[str, Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
) -> int:
    base_capacity = _profile_daily_capacity(today, profile)
    if base_capacity <= 0:
        return 0

    pressure_capacity = _deadline_pressure_capacity(today, tasks, goals)
    if pressure_capacity <= 0:
        return base_capacity

    return min(14 * 60, max(base_capacity, pressure_capacity))


def _plan_position(plan: Dict[str, Any], fallback: int) -> int:
    position = _int_or_none(plan.get("position"))
    return position if position is not None else fallback


def _plan_order_by_id(plans: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, int]:
    source = plans if plans is not None else _load_plans()

    return {
        str(plan.get("id") or plan_id): _plan_position(plan, index)
        for index, (plan_id, plan) in enumerate(source.items())
        if isinstance(plan, dict)
    }


def _plan_sort_key(plan: Dict[str, Any], fallback: int = 0) -> tuple:
    return (
        _plan_position(plan, fallback),
        str(plan.get("created_at") or ""),
        str(plan.get("id") or ""),
    )


def _next_plan_position(plans: Dict[str, Dict[str, Any]], goal_id: str) -> int:
    positions = [
        _plan_position(plan, index)
        for index, plan in enumerate(plans.values())
        if str(plan.get("goal_id") or "") == goal_id
    ]

    return (max(positions) + 1) if positions else 0


def _daily_task_sort_key(
    task: Dict[str, Any],
    today: date,
    plan_order_by_id: Optional[Dict[str, int]] = None,
) -> tuple:
    scheduled_date = _task_scheduled_date(task)
    if scheduled_date and scheduled_date < today:
        schedule_rank = 0
    elif scheduled_date is None:
        schedule_rank = 1
    elif scheduled_date == today:
        schedule_rank = 2
    else:
        schedule_rank = 3
    scheduled_day = scheduled_date or today
    plan_order = (plan_order_by_id or {}).get(str(task.get("plan_id") or ""), 0)
    return (
        plan_order,
        int(task.get("position") or 0),
        schedule_rank,
        scheduled_day.isoformat(),
        int(task.get("priority") or 3),
        str(task.get("created_at") or ""),
        str(task.get("id") or ""),
    )


def _is_daily_schedule_eligible(
    task: Dict[str, Any], goals: Dict[str, Dict[str, Any]]
) -> bool:
    if task.get("status") == TaskStatus.COMPLETED.value:
        return False
    goal_id = str(task.get("goal_id") or "")
    goal = goals.get(goal_id)
    if not goal:
        return True
    return str(goal.get("status") or "") not in _INACTIVE_GOAL_STATUSES


def _goal_schedule_sort_key(
    goal_id: str,
    bucket: List[Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
    today: date,
) -> tuple:
    goal = goals.get(goal_id) or {}
    target = _parse_datetime(str(goal.get("target_date") or ""))
    target_day = _local_date_from(target) if target else None
    if target_day:
        days_left = max(-3650, (target_day - today).days)
    else:
        days_left = 3650
    progress = _float_or_none(goal.get("progress")) or 0.0
    first_task_key = _daily_task_sort_key(bucket[0], today) if bucket else ()
    return (days_left, progress, first_task_key, str(goal.get("created_at") or ""))


def _ordered_pending_tasks_for_daily_schedule(
    tasks: Dict[str, Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
    today: date,
    plans: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    plan_order_by_id = _plan_order_by_id(plans)
    for task in tasks.values():
        if not _is_daily_schedule_eligible(task, goals):
            continue
        goal_id = str(task.get("goal_id") or task.get("id") or "")
        buckets.setdefault(goal_id, []).append(task)

    for bucket in buckets.values():
        bucket.sort(key=lambda task: _daily_task_sort_key(task, today, plan_order_by_id))

    goal_ids = sorted(
        buckets,
        key=lambda goal_id: _goal_schedule_sort_key(goal_id, buckets[goal_id], goals, today),
    )
    ordered: List[Dict[str, Any]] = []

    while any(buckets[goal_id] for goal_id in goal_ids):
        for goal_id in goal_ids:
            if buckets[goal_id]:
                ordered.append(buckets[goal_id].pop(0))

    return ordered


def _refresh_today_task_schedule(
    tasks: Dict[str, Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
    profile: Dict[str, Any],
    now: datetime,
    *,
    force: bool = False,
    plans: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bool:
    today = _local_date_from(now)
    today_key = today.isoformat()
    state = _load_schedule_state()
    if (
        not force
        and state.get("last_today_schedule_date") == today_key
        and state.get("today_schedule_version") == _TODAY_SCHEDULE_VERSION
    ):
        return False

    capacity = _dynamic_daily_capacity(today, profile, tasks, goals)
    used_minutes = sum(
        _coerce_positive_int(
            task.get("estimated_minutes"), 30, minimum=1, maximum=8 * 60
        )
        for task in tasks.values()
        if task.get("status") == TaskStatus.COMPLETED.value
        and _task_scheduled_date(task) == today
    )
    changed = False
    scheduled_ids: set[str] = set()
    pending_tasks = _ordered_pending_tasks_for_daily_schedule(tasks, goals, today, plans)

    for task in pending_tasks:
        duration = _coerce_positive_int(
            task.get("estimated_minutes"), 30, minimum=1, maximum=8 * 60
        )
        fits_today = capacity > 0 and (used_minutes == 0 or used_minutes + duration <= capacity)
        next_date = today_key if fits_today else ""
        previous_date = str(task.get("scheduled_date") or "")
        if previous_date != next_date:
            task["scheduled_date"] = next_date
            task["updated_at"] = now.isoformat()
            changed = True
        if fits_today:
            scheduled_ids.add(str(task.get("id") or ""))
            used_minutes += duration

    for task in tasks.values():
        if task.get("status") == TaskStatus.COMPLETED.value:
            continue
        if str(task.get("id") or "") in scheduled_ids:
            continue
        if _task_scheduled_date(task) == today:
            task["scheduled_date"] = ""
            task["updated_at"] = now.isoformat()
            changed = True

    state["last_today_schedule_date"] = today_key
    state["today_schedule_version"] = _TODAY_SCHEDULE_VERSION
    state["updated_at"] = now.isoformat()
    _save_schedule_state(state)
    return changed


def _ahead_task_recommendations(
    tasks: Dict[str, Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
    today: date,
    *,
    plans: Optional[Dict[str, Dict[str, Any]]] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    tomorrow_key = (today + timedelta(days=1)).isoformat()
    recommendations: List[Dict[str, Any]] = []

    for task in _ordered_pending_tasks_for_daily_schedule(tasks, goals, today, plans):
        if _task_scheduled_date(task) == today:
            continue
        copied = dict(task)
        copied["ahead"] = True
        copied["ahead_reason"] = "今日任务已完成，可以作为明日任务提前推进。"
        copied["recommended_for_date"] = tomorrow_key
        copied["scheduled_date"] = tomorrow_key
        recommendations.append(copied)
        if len(recommendations) >= limit:
            break

    return recommendations


def _clock_minutes(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    match = re.search(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _clock_label(minutes: int) -> str:
    bounded = max(0, min(23 * 60 + 59, minutes))
    return f"{bounded // 60:02d}:{bounded % 60:02d}"


def _task_learning_feedback(task: Dict[str, Any]) -> Dict[str, Any]:
    feedback = task.get("learning_feedback")
    return feedback if isinstance(feedback, dict) else {}


def _recent_feedback_for_goal(
    goal_id: str,
    tasks: Dict[str, Dict[str, Any]],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    items = []
    for task in tasks.values():
        if str(task.get("goal_id") or "") != goal_id:
            continue
        feedback = _task_learning_feedback(task)
        if not feedback:
            continue
        items.append(
            {
                "task_id": task.get("id"),
                "task_title": task.get("title"),
                "completed_at": task.get("completed_at"),
                "actual_minutes": feedback.get("actual_minutes"),
                "mastery_level": feedback.get("mastery_level"),
                "summary": feedback.get("analysis", {}).get("summary") if isinstance(feedback.get("analysis"), dict) else "",
                "risk_level": feedback.get("analysis", {}).get("risk_level") if isinstance(feedback.get("analysis"), dict) else "",
                "next_focus": feedback.get("next_focus"),
                "raw_feedback": feedback.get("feedback"),
            }
        )

    items.sort(key=lambda item: str(item.get("completed_at") or ""), reverse=True)
    return items[:limit]


def _compact_learning_feedback(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    analysis = value.get("analysis")
    analysis_payload = analysis if isinstance(analysis, dict) else {}
    return {
        "actual_minutes": value.get("actual_minutes"),
        "mastery_level": _text_or_empty(value.get("mastery_level"), 60),
        "feedback": _text_or_empty(value.get("feedback"), 180),
        "next_focus": _text_or_empty(value.get("next_focus"), 160),
        "analysis_status": _text_or_empty(value.get("analysis_status"), 40),
        "summary": _text_or_empty(analysis_payload.get("summary"), 160),
        "risk_level": _text_or_empty(analysis_payload.get("risk_level"), 30),
        "schedule_hint": _text_or_empty(analysis_payload.get("schedule_hint"), 160),
    }


def _compact_recent_feedback(items: List[Dict[str, Any]], limit: int = 2) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "task_title": _text_or_empty(item.get("task_title"), 100),
                "actual_minutes": item.get("actual_minutes"),
                "mastery_level": _text_or_empty(item.get("mastery_level"), 60),
                "summary": _text_or_empty(item.get("summary"), 160),
                "risk_level": _text_or_empty(item.get("risk_level"), 30),
                "next_focus": _text_or_empty(item.get("next_focus"), 120),
            }
        )
    return compact


def _compact_goal_context(goal: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(goal, dict):
        return {}

    last_feedback = goal.get("last_learning_feedback")
    last_feedback_payload = last_feedback if isinstance(last_feedback, dict) else {}
    return {
        "id": goal.get("id"),
        "name": _text_or_empty(goal.get("name"), 120),
        "description": _text_or_empty(goal.get("description"), 240),
        "goal_type": goal.get("goal_type"),
        "target_date": goal.get("target_date"),
        "estimated_hours": goal.get("estimated_hours"),
        "progress": goal.get("progress"),
        "tags": _normalize_text_list(goal.get("tags"))[:5],
        "last_learning_feedback": {
            "task_title": _text_or_empty(last_feedback_payload.get("task_title"), 100),
            "summary": _text_or_empty(last_feedback_payload.get("summary"), 140),
            "mastery_signal": _text_or_empty(last_feedback_payload.get("mastery_signal"), 30),
            "pace_signal": _text_or_empty(last_feedback_payload.get("pace_signal"), 30),
            "risk_level": _text_or_empty(last_feedback_payload.get("risk_level"), 30),
            "schedule_hint": _text_or_empty(last_feedback_payload.get("schedule_hint"), 140),
        },
    }


def _compact_plan_context(plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        return {}

    return {
        "id": plan.get("id"),
        "name": _text_or_empty(plan.get("name"), 120),
        "description": _text_or_empty(plan.get("description"), 200),
        "total_tasks": plan.get("total_tasks"),
        "completed_tasks": plan.get("completed_tasks"),
    }


def _compact_profile_context(profile: Dict[str, Any]) -> Dict[str, Any]:
    status = _profile_status(profile)
    return {
        "major": _text_or_empty(profile.get("major"), 100),
        "grade": _text_or_empty(profile.get("grade"), 60),
        "baseline_level": _text_or_empty(profile.get("baseline_level"), 80),
        "career_target": _text_or_empty(profile.get("career_target"), 120),
        "weekday_minutes": profile.get("weekday_minutes"),
        "weekend_minutes": profile.get("weekend_minutes"),
        "preferred_study_time": _text_or_empty(profile.get("preferred_study_time"), 100),
        "learning_preferences": _normalize_text_list(profile.get("learning_preferences"))[:4],
        "current_courses": _normalize_text_list(profile.get("current_courses"))[:6],
        "strengths": _text_or_empty(profile.get("strengths"), 160),
        "weaknesses": _text_or_empty(profile.get("weaknesses"), 160),
        "constraints": _text_or_empty(profile.get("constraints"), 160),
        "complete": status["complete"],
        "missing_fields": status["missing_fields"],
    }


def _compact_task_context(task: Dict[str, Any], *, include_id: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "title": _text_or_empty(task.get("title"), 120),
        "description": _text_or_empty(task.get("description"), 260),
        "difficulty": task.get("difficulty"),
        "estimated_minutes": task.get("estimated_minutes"),
        "scheduled_date": task.get("scheduled_date"),
        "priority": task.get("priority"),
        "status": task.get("status"),
        "learning_feedback": _compact_learning_feedback(_task_learning_feedback(task)),
    }
    if include_id:
        payload["task_id"] = task.get("id")
    return payload


def _compact_requirement_draft_context(draft: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "goal_name": _text_or_empty(draft.get("goal_name"), 120),
        "purpose": _text_or_empty(draft.get("purpose"), 120),
        "current_level": _text_or_empty(draft.get("current_level"), 100),
        "target_level": _text_or_empty(draft.get("target_level"), 160),
        "daily_minutes": draft.get("daily_minutes"),
        "timeframe": _text_or_empty(draft.get("timeframe"), 60),
        "timeframe_days": draft.get("timeframe_days"),
        "deadline": draft.get("deadline"),
        "preference": _text_or_empty(draft.get("preference"), 100),
        "goal_type": draft.get("goal_type"),
        "tags": _normalize_text_list(draft.get("tags"))[:6],
        "student_context": draft.get("student_context") if isinstance(draft.get("student_context"), dict) else {},
        "time_constraints": draft.get("time_constraints") if isinstance(draft.get("time_constraints"), dict) else {},
    }


def _course_planner_payload(
    *,
    draft: Dict[str, Any],
    estimate: Dict[str, Any],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    period_days = draft.get("timeframe_days") or estimate.get("estimated_days")
    max_task_minutes = min(
        180,
        max(30, _coerce_positive_int(draft.get("daily_minutes"), 90, minimum=30, maximum=240)),
    )
    return {
        "iface": "course_plan.v2",
        "req": {
            "n": _text_or_empty(draft.get("goal_name"), 60),
            "use": _text_or_empty(draft.get("purpose"), 60),
            "base": _text_or_empty(draft.get("current_level"), 50),
            "target": _text_or_empty(draft.get("target_level"), 80),
            "days": period_days,
            "deadline": draft.get("deadline"),
            "pref": _text_or_empty(draft.get("preference"), 50),
            "type": _normalize_goal_type(draft.get("goal_type")) or "skill",
            "tags": _normalize_text_list(draft.get("tags"))[:5],
        },
        "student": {
            "major": _text_or_empty(profile.get("major"), 50),
            "grade": _text_or_empty(profile.get("grade"), 30),
            "career": _text_or_empty(profile.get("career_target"), 60),
            "base": _text_or_empty(profile.get("baseline_level"), 50),
            "weak": _text_or_empty(profile.get("weaknesses"), 80),
            "strong": _text_or_empty(profile.get("strengths"), 80),
            "courses": _normalize_text_list(profile.get("current_courses"))[:5],
            "habit": _normalize_text_list(profile.get("learning_preferences"))[:3],
            "weekday_min": profile.get("weekday_minutes"),
            "weekend_min": profile.get("weekend_minutes"),
            "time": _text_or_empty(profile.get("preferred_study_time"), 50),
            "limit": _text_or_empty(profile.get("constraints"), 80),
        },
        "est": {
            "h": estimate.get("estimated_hours"),
            "days": estimate.get("estimated_days"),
            "fit": estimate.get("fit"),
        },
        "limits": {
            "phase_policy": "按目标范围、周期、总时长、基础和目标动态决定阶段数量，不固定阶段数。",
            "task_policy": "每阶段只放必要任务，保持精简但不要漏掉关键阶段。",
            "title_chars": 20,
            "task_desc_chars": 36,
            "plan_desc_chars": 28,
            "max_task_minutes": max_task_minutes,
            "json_only": True,
        },
    }


def _course_plan_signature(
    *,
    draft: Dict[str, Any],
    estimate: Dict[str, Any],
    profile: Dict[str, Any],
) -> str:
    payload = {
        "draft": _compact_requirement_draft_context(draft),
        "estimate": estimate,
        "profile": _compact_profile_context(profile),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cached_course_plan_spec(signature: str) -> Optional[Dict[str, Any]]:
    state = _load_schedule_state()
    cache = state.get("course_plan_spec_cache")
    if not isinstance(cache, dict):
        return None

    entry = cache.get(signature)
    if not isinstance(entry, dict):
        return None

    updated_at = _parse_datetime(str(entry.get("updated_at") or ""))
    if not updated_at:
        return None

    age = (_local_now() - updated_at.astimezone(_LOCAL_TIMEZONE)).total_seconds()
    if age > _LEARNING_COURSE_PLAN_CACHE_TTL_SECONDS:
        return None

    plan_spec = entry.get("plan_spec")
    return plan_spec if isinstance(plan_spec, dict) else None


def _save_course_plan_spec_cache(signature: str, plan_spec: Dict[str, Any]) -> None:
    if not _normalize_plan_spec(plan_spec):
        return

    state = _load_schedule_state()
    cache = state.get("course_plan_spec_cache")
    if not isinstance(cache, dict):
        cache = {}

    cache[signature] = {
        "plan_spec": plan_spec,
        "updated_at": _local_now().isoformat(),
    }

    if len(cache) > 40:
        ordered = sorted(
            cache.items(),
            key=lambda item: str(item[1].get("updated_at") if isinstance(item[1], dict) else ""),
            reverse=True,
        )
        cache = dict(ordered[:40])

    state["course_plan_spec_cache"] = cache
    errors = state.get("course_plan_spec_errors")
    if isinstance(errors, dict):
        errors.pop(signature, None)
        state["course_plan_spec_errors"] = errors
    _save_schedule_state(state)


def _cached_course_plan_spec_error(signature: str) -> Optional[str]:
    state = _load_schedule_state()
    errors = state.get("course_plan_spec_errors")
    if not isinstance(errors, dict):
        return None

    entry = errors.get(signature)
    if not isinstance(entry, dict):
        return None

    message = _text_or_empty(entry.get("message"), 300)
    errors.pop(signature, None)
    state["course_plan_spec_errors"] = errors
    _save_schedule_state(state)
    return message or None


def _clear_course_plan_spec_error(signature: str) -> None:
    state = _load_schedule_state()
    errors = state.get("course_plan_spec_errors")
    if not isinstance(errors, dict) or signature not in errors:
        return

    errors.pop(signature, None)
    state["course_plan_spec_errors"] = errors
    _save_schedule_state(state)


def _save_course_plan_spec_error(signature: str, message: str) -> None:
    state = _load_schedule_state()
    errors = state.get("course_plan_spec_errors")
    if not isinstance(errors, dict):
        errors = {}

    errors[signature] = {
        "message": _text_or_empty(message, 500) or "模型没有返回可用的结构化结果。",
        "updated_at": _local_now().isoformat(),
    }
    state["course_plan_spec_errors"] = errors
    _save_schedule_state(state)


def _store_course_plan_inflight_result(signature: str, task: asyncio.Task) -> None:
    try:
        result = task.result()
    except asyncio.CancelledError:
        _COURSE_PLAN_INFLIGHT.pop(signature, None)
        return
    except Exception as exc:
        _save_course_plan_spec_error(signature, str(exc))
        _COURSE_PLAN_INFLIGHT.pop(signature, None)
        return

    if isinstance(result, dict) and _normalize_plan_spec(result):
        _save_course_plan_spec_cache(signature, result)
    else:
        _save_course_plan_spec_error(signature, "模型没有返回可用的结构化计划。")
    _COURSE_PLAN_INFLIGHT.pop(signature, None)


def _task_guidance_signature(
    *,
    task: Dict[str, Any],
    goal: Optional[Dict[str, Any]],
    plan: Optional[Dict[str, Any]],
    profile: Dict[str, Any],
    recent_feedback: List[Dict[str, Any]],
) -> str:
    payload = {
        "task": {
            **_compact_task_context(task),
            "id": task.get("id"),
            "updated_at": task.get("updated_at"),
        },
        "goal": _compact_goal_context(goal),
        "plan": _compact_plan_context(plan),
        "profile": _compact_profile_context(profile),
        "recent_goal_feedback": _compact_recent_feedback(recent_feedback),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cached_task_guidance(task_id: str, signature: str) -> Optional[Dict[str, Any]]:
    state = _load_schedule_state()
    cache = state.get("task_guidance_cache")
    if not isinstance(cache, dict):
        return None

    entry = cache.get(task_id)
    if not isinstance(entry, dict) or entry.get("signature") != signature:
        return None

    updated_at = _parse_datetime(str(entry.get("updated_at") or ""))
    if not updated_at:
        return None

    age = (_local_now() - updated_at.astimezone(_LOCAL_TIMEZONE)).total_seconds()
    if age > _LEARNING_TASK_GUIDANCE_CACHE_TTL_SECONDS:
        return None

    guidance = entry.get("guidance")
    return guidance if isinstance(guidance, dict) else None


def _save_task_guidance_cache(task_id: str, signature: str, guidance: Dict[str, Any]) -> None:
    state = _load_schedule_state()
    cache = state.get("task_guidance_cache")
    if not isinstance(cache, dict):
        cache = {}

    cache[task_id] = {
        "signature": signature,
        "guidance": guidance,
        "updated_at": _local_now().isoformat(),
    }

    if len(cache) > 80:
        ordered = sorted(
            cache.items(),
            key=lambda item: str(item[1].get("updated_at") if isinstance(item[1], dict) else ""),
            reverse=True,
        )
        cache = dict(ordered[:80])

    state["task_guidance_cache"] = cache
    errors = state.get("task_guidance_errors")
    if isinstance(errors, dict):
        errors.pop(f"{task_id}:{signature}", None)
        state["task_guidance_errors"] = errors
    _save_schedule_state(state)


def _task_guidance_error_key(task_id: str, signature: str) -> str:
    return f"{task_id}:{signature}" if task_id else signature


def _cached_task_guidance_error(task_id: str, signature: str) -> Optional[str]:
    state = _load_schedule_state()
    errors = state.get("task_guidance_errors")
    if not isinstance(errors, dict):
        return None

    entry = errors.get(_task_guidance_error_key(task_id, signature))
    if not isinstance(entry, dict):
        return None

    return _text_or_empty(entry.get("message"), 300) or None


def _clear_task_guidance_error(task_id: str, signature: str) -> None:
    state = _load_schedule_state()
    errors = state.get("task_guidance_errors")
    key = _task_guidance_error_key(task_id, signature)
    if not isinstance(errors, dict) or key not in errors:
        return

    errors.pop(key, None)
    state["task_guidance_errors"] = errors
    _save_schedule_state(state)


def _save_task_guidance_error(task_id: str, signature: str, message: str) -> None:
    state = _load_schedule_state()
    errors = state.get("task_guidance_errors")
    if not isinstance(errors, dict):
        errors = {}

    errors[_task_guidance_error_key(task_id, signature)] = {
        "message": _text_or_empty(message, 500) or "模型没有返回可用的任务建议。",
        "updated_at": _local_now().isoformat(),
    }
    state["task_guidance_errors"] = errors
    _save_schedule_state(state)


def _store_task_guidance_inflight_result(task_id: str, signature: str, task: asyncio.Task) -> None:
    try:
        parsed = task.result()
    except asyncio.CancelledError:
        _TASK_GUIDANCE_INFLIGHT.pop(_task_guidance_error_key(task_id, signature), None)
        return
    except Exception as exc:
        _save_task_guidance_error(task_id, signature, str(exc))
        _TASK_GUIDANCE_INFLIGHT.pop(_task_guidance_error_key(task_id, signature), None)
        return

    guidance = _normalize_task_guidance_payload(parsed)
    if guidance:
        guidance["updated_at"] = datetime.now(timezone.utc).isoformat()
        if task_id:
            _save_task_guidance_cache(task_id, signature, guidance)
    else:
        _save_task_guidance_error(task_id, signature, "模型没有返回可用的结构化任务建议。")
    _TASK_GUIDANCE_INFLIGHT.pop(_task_guidance_error_key(task_id, signature), None)


def _today_timetable_signature(
    today: date,
    tasks: List[Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
    profile: Dict[str, Any],
) -> str:
    payload = {
        "today": today.isoformat(),
        "tasks": [
            {
                "id": task.get("id"),
                "goal_id": task.get("goal_id"),
                "title": task.get("title"),
                "description": task.get("description"),
                "estimated_minutes": task.get("estimated_minutes"),
                "status": task.get("status"),
                "updated_at": task.get("updated_at"),
                "learning_feedback": task.get("learning_feedback"),
            }
            for task in tasks
        ],
        "goals": {
            goal_id: {
                "name": goal.get("name"),
                "target_date": goal.get("target_date"),
                "progress": goal.get("progress"),
                "last_learning_feedback": goal.get("last_learning_feedback"),
            }
            for goal_id, goal in sorted(goals.items())
            if any(task.get("goal_id") == goal_id for task in tasks)
        },
        "profile": {
            "major": profile.get("major"),
            "grade": profile.get("grade"),
            "baseline_level": profile.get("baseline_level"),
            "weekday_minutes": profile.get("weekday_minutes"),
            "weekend_minutes": profile.get("weekend_minutes"),
            "preferred_study_time": profile.get("preferred_study_time"),
            "learning_preferences": profile.get("learning_preferences"),
            "constraints": profile.get("constraints"),
            "weaknesses": profile.get("weaknesses"),
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _task_timetable_duration(task: Dict[str, Any]) -> int:
    return _coerce_positive_int(task.get("estimated_minutes"), 30, minimum=15, maximum=8 * 60)


def _snap_timetable_minute(minutes: int) -> int:
    return int(round(minutes / 15) * 15)


def _preferred_timetable_start(profile: Dict[str, Any], total_minutes: int) -> int:
    preference = str(profile.get("preferred_study_time") or "")
    if any(marker in preference for marker in ("晚", "夜")):
        preferred = 19 * 60
    elif any(marker in preference for marker in ("下午", "午后")):
        preferred = 14 * 60
    elif "中午" in preference:
        preferred = 12 * 60
    elif any(marker in preference for marker in ("早", "上午")):
        preferred = 8 * 60
    else:
        preferred = 9 * 60

    latest_start = max(8 * 60, 22 * 60 - total_minutes)
    return max(8 * 60, min(latest_start, _snap_timetable_minute(preferred)))


def _local_today_timetable_entries(
    tasks: List[Dict[str, Any]],
    profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if not tasks:
        return []

    durations = [_task_timetable_duration(task) for task in tasks]
    day_minutes = 14 * 60
    total_duration = sum(durations)
    available_gap = max(0, day_minutes - total_duration)
    if len(tasks) <= 1 or available_gap <= 0:
        gap = 0
    else:
        minimum_gap = 10 if available_gap >= 10 * (len(tasks) - 1) else 0
        extra_gap = max(0, available_gap - minimum_gap * (len(tasks) - 1))
        gap = minimum_gap + min(45, extra_gap // max(1, len(tasks) - 1))

    total_block = min(day_minutes, total_duration + gap * max(0, len(tasks) - 1))
    cursor = _preferred_timetable_start(profile, total_block)
    entries: List[Dict[str, Any]] = []

    for task, duration in zip(tasks, durations):
        latest_start = max(8 * 60, 22 * 60 - duration)
        start_minutes = max(8 * 60, min(latest_start, _snap_timetable_minute(cursor)))
        entries.append(
            {
                "task_id": str(task.get("id") or ""),
                "start_time": _clock_label(start_minutes),
                "end_time": _clock_label(start_minutes + duration),
                "reason": "本地快速排布：按今日任务顺序安排。",
            }
        )
        cursor = start_minutes + duration + gap

    return entries


def _normalize_today_timetable_payload(
    payload: Dict[str, Any],
    tasks: List[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(payload, dict):
        return None

    raw_entries = payload.get("timetable") or payload.get("schedule") or payload.get("items")
    if not isinstance(raw_entries, list):
        return None

    task_by_id = {str(task.get("id")): task for task in tasks if task.get("id")}
    task_by_slot = {
        str(index): task
        for index, task in enumerate(tasks, start=1)
        if task.get("id")
    }
    task_order = {
        str(task.get("id")): index
        for index, task in enumerate(tasks)
        if task.get("id")
    }
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        task_id = str(raw_entry.get("task_id") or raw_entry.get("id") or "").strip()
        task = task_by_id.get(task_id)
        if not task:
            for slot_key in ("slot", "no", "index", "order", "task_no", "task_number", "number"):
                slot_text = str(raw_entry.get(slot_key) or "").strip()
                slot_match = re.search(r"\d+", slot_text)
                if not slot_match:
                    continue
                task = task_by_slot.get(slot_match.group(0))
                if task:
                    task_id = str(task.get("id") or "")
                    break
        if not task or task_id in seen:
            continue
        start_minutes = _clock_minutes(
            raw_entry.get("start_time")
            or raw_entry.get("start")
            or raw_entry.get("begin_time")
            or raw_entry.get("time")
        )
        if start_minutes is None:
            continue
        duration = _task_timetable_duration(task)
        latest_start = max(8 * 60, 22 * 60 - duration)
        start_minutes = max(8 * 60, min(latest_start, start_minutes))
        entries.append(
            {
                "task_id": task_id,
                "start_minutes": start_minutes,
                "duration_minutes": duration,
                "reason": _text_or_empty(raw_entry.get("reason"), 180),
            }
        )
        seen.add(task_id)

    if len(entries) != len(task_by_id):
        return None

    entries.sort(key=lambda entry: task_order.get(str(entry.get("task_id") or ""), 100_000))
    cursor = 8 * 60
    normalized: List[Dict[str, Any]] = []
    for entry in entries:
        start_minutes = max(cursor, entry["start_minutes"])
        duration = entry["duration_minutes"]
        if start_minutes + duration > 22 * 60:
            return None
        normalized.append(
            {
                "task_id": entry["task_id"],
                "start_time": _clock_label(start_minutes),
                "end_time": _clock_label(start_minutes + duration),
                "reason": entry["reason"],
            }
        )
        cursor = start_minutes + duration + 10

    return normalized


def _save_today_timetable_entries(
    *,
    today: date,
    signature: str,
    entries: List[Dict[str, Any]],
) -> None:
    state = _load_schedule_state()
    state["today_timetable"] = {
        "date": today.isoformat(),
        "signature": signature,
        "entries": entries,
        "updated_at": _local_now().isoformat(),
    }
    state.pop("today_timetable_error", None)
    _save_schedule_state(state)


def _save_today_timetable_error(
    *,
    today: date,
    signature: str,
    skill: str,
    message: str,
) -> None:
    state = _load_schedule_state()
    state["today_timetable_error"] = {
        "date": today.isoformat(),
        "signature": signature,
        "skill": skill,
        "message": _text_or_empty(message, 500) or "模型没有返回可用的今日排课。",
        "updated_at": _local_now().isoformat(),
    }
    _save_schedule_state(state)


def _store_today_timetable_inflight_result(
    signature: str,
    today: date,
    tasks: List[Dict[str, Any]],
    task: asyncio.Task,
) -> None:
    try:
        result = task.result()
    except asyncio.CancelledError:
        _TODAY_TIMETABLE_INFLIGHT.pop(signature, None)
        return
    except LearningSkillError as exc:
        _save_today_timetable_error(
            today=today,
            signature=signature,
            skill=exc.skill_name,
            message=str(exc),
        )
        _TODAY_TIMETABLE_INFLIGHT.pop(signature, None)
        return
    except Exception as exc:
        _save_today_timetable_error(
            today=today,
            signature=signature,
            skill="daily-scheduler",
            message=str(exc),
        )
        _TODAY_TIMETABLE_INFLIGHT.pop(signature, None)
        return

    entries = _normalize_today_timetable_payload(result, tasks)
    if entries:
        _save_today_timetable_entries(
            today=today,
            signature=signature,
            entries=entries,
        )
    else:
        _save_today_timetable_error(
            today=today,
            signature=signature,
            skill="daily-scheduler",
            message="daily-scheduler returned an incomplete or invalid timetable",
        )
    _TODAY_TIMETABLE_INFLIGHT.pop(signature, None)


async def _generate_today_timetable_with_llm(
    *,
    all_tasks: Dict[str, Dict[str, Any]],
    today: date,
    tasks: List[Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    payload = {
        "today": today.isoformat(),
        "contract": "只为今天给定 slot 安排开始时间；不要反问，不做周计划，只返回 JSON。",
        "day_window": "08:00-22:00",
        "student": {
            "major": _text_or_empty(profile.get("major"), 60),
            "grade": _text_or_empty(profile.get("grade"), 60),
            "baseline_level": _text_or_empty(profile.get("baseline_level"), 80),
            "weekday_minutes": profile.get("weekday_minutes"),
            "weekend_minutes": profile.get("weekend_minutes"),
            "preferred_study_time": _text_or_empty(profile.get("preferred_study_time"), 80),
            "learning_preferences": _normalize_text_list(profile.get("learning_preferences"))[:3],
            "weaknesses": _text_or_empty(profile.get("weaknesses"), 120),
            "constraints": _text_or_empty(profile.get("constraints"), 120),
        },
        "rules": [
            "只给今天已经选入容量的任务安排开始时间，不新增任务。",
            "每个 slot 必须出现且只能出现一次；slot 是今天左侧任务编号。",
            "优先返回 slot，task_id 只作为辅助识别。",
            "开始时间要贴合 preferred_study_time、constraints、课程难度和任务时长。",
            "任务之间至少留 10 分钟缓冲，不能超出 08:00-22:00。",
        ],
        "output_schema": {"timetable": [{"slot": 1, "start_time": "HH:MM", "reason": "30字内"}]},
        "tasks": [
            {
                "slot": index,
                "task_id": task.get("id"),
                "title": _text_or_empty(task.get("title"), 80),
                "minutes": _task_timetable_duration(task),
                "difficulty": task.get("difficulty"),
                "goal": _text_or_empty(
                    (goals.get(str(task.get("goal_id") or "")) or {}).get("name"),
                    80,
                ),
                "target_date": (goals.get(str(task.get("goal_id") or "")) or {}).get("target_date"),
            }
            for index, task in enumerate(tasks, start=1)
        ],
    }
    parsed = await asyncio.to_thread(
        _invoke_learning_llm_json_for_thread,
        system_message=_learning_skill_system_message("daily-scheduler"),
        payload=payload,
        label="daily-scheduler",
        max_tokens=_LEARNING_TIMETABLE_MAX_OUTPUT_TOKENS,
    )
    if not parsed:
        raise LearningSkillError(
            "daily-scheduler",
            "daily-scheduler did not return a valid JSON object",
        )
    return parsed


async def _today_timetable_entries(
    *,
    all_tasks: Dict[str, Dict[str, Any]],
    today: date,
    tasks: List[Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
    profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if not tasks:
        return []

    signature = _today_timetable_signature(today, tasks, goals, profile)
    state = _load_schedule_state()
    cached = state.get("today_timetable")
    if (
        isinstance(cached, dict)
        and cached.get("date") == today.isoformat()
        and cached.get("signature") == signature
        and isinstance(cached.get("entries"), list)
    ):
        return cached["entries"]

    cached_error = state.get("today_timetable_error")
    if (
        isinstance(cached_error, dict)
        and cached_error.get("date") == today.isoformat()
        and cached_error.get("signature") == signature
    ):
        state.pop("today_timetable_error", None)
        _save_schedule_state(state)
        raise LearningSkillError(
            str(cached_error.get("skill") or "daily-scheduler"),
            _text_or_empty(cached_error.get("message"), 500)
            or "daily-scheduler returned an incomplete or invalid timetable",
        )

    running = _TODAY_TIMETABLE_INFLIGHT.get(signature)
    if running is None:
        running = asyncio.create_task(
            _generate_today_timetable_with_llm(
                all_tasks=all_tasks,
                today=today,
                tasks=tasks,
                goals=goals,
                profile=profile,
            )
        )
        _TODAY_TIMETABLE_INFLIGHT[signature] = running
        running.add_done_callback(
            lambda completed, timetable_signature=signature, timetable_today=today, timetable_tasks=[
                dict(task) for task in tasks
            ]: _store_today_timetable_inflight_result(
                timetable_signature,
                timetable_today,
                timetable_tasks,
                completed,
            )
        )

    if not running.done():
        raise LearningSkillPendingError("daily-scheduler")

    try:
        parsed = await running
    except LearningSkillError as exc:
        state["today_timetable_error"] = {
            "date": today.isoformat(),
            "signature": signature,
            "skill": exc.skill_name,
            "message": str(exc),
            "updated_at": _local_now().isoformat(),
        }
        _save_schedule_state(state)
        raise

    entries = _normalize_today_timetable_payload(parsed, tasks)
    if not entries:
        _log.warning(
            "daily-scheduler returned invalid timetable payload: %s",
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))[:800],
        )
        exc = LearningSkillError(
            "daily-scheduler",
            "daily-scheduler returned an incomplete or invalid timetable",
        )
        state["today_timetable_error"] = {
            "date": today.isoformat(),
            "signature": signature,
            "skill": exc.skill_name,
            "message": str(exc),
            "updated_at": _local_now().isoformat(),
        }
        _save_schedule_state(state)
        raise exc

    _save_today_timetable_entries(
        today=today,
        signature=signature,
        entries=entries,
    )
    return entries


def _guidance_list(value: Any, *, limit: int = 5, item_limit: int = 180) -> List[str]:
    if isinstance(value, str):
        raw_items = [
            item.strip()
            for item in re.split(r"[;；\n]", value)
            if item.strip()
        ]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    items: List[str] = []
    for item in raw_items:
        text = _text_or_empty(item, item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _normalize_task_guidance_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    data = payload.get("guidance") if isinstance(payload.get("guidance"), dict) else payload
    if not isinstance(data, dict):
        return None

    summary = _text_or_empty(data.get("summary"), 120)
    steps = _guidance_list(data.get("steps"), limit=3, item_limit=100)
    focus_points = _guidance_list(data.get("focus_points"), limit=2, item_limit=80)
    pitfalls = _guidance_list(data.get("pitfalls"), limit=2, item_limit=80)
    checklist = _guidance_list(data.get("checklist"), limit=2, item_limit=80)

    if not summary or len(steps) < 2 or not focus_points or not pitfalls or not checklist:
        return None

    return {
        "summary": summary,
        "steps": steps,
        "focus_points": focus_points,
        "pitfalls": pitfalls,
        "checklist": checklist,
    }


def _normalize_progress_feedback_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    data = payload.get("feedback_analysis") if isinstance(payload.get("feedback_analysis"), dict) else payload
    if not isinstance(data, dict):
        return None

    mastery_signal = _text_or_empty(data.get("mastery_signal"), 24).lower()
    if mastery_signal not in {"strong", "ok", "weak"}:
        mastery_signal = "ok"

    pace_signal = _text_or_empty(data.get("pace_signal"), 24).lower()
    if pace_signal not in {"ahead", "on_track", "behind"}:
        pace_signal = "on_track"

    risk_level = _text_or_empty(data.get("risk_level"), 24).lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium" if mastery_signal == "weak" else "low"

    follow_up_raw = data.get("follow_up_task") if isinstance(data.get("follow_up_task"), dict) else {}
    follow_up_needed = bool(follow_up_raw.get("needed"))
    follow_up_title = _text_or_empty(follow_up_raw.get("title"), 120)
    follow_up_description = _text_or_empty(follow_up_raw.get("description"), 500)

    follow_up_task = {
        "needed": follow_up_needed and bool(follow_up_title),
        "title": follow_up_title,
        "description": follow_up_description,
        "estimated_minutes": _coerce_positive_int(
            follow_up_raw.get("estimated_minutes"),
            45,
            minimum=15,
            maximum=180,
        ),
        "priority": _coerce_positive_int(
            follow_up_raw.get("priority"),
            1,
            minimum=1,
            maximum=5,
        ),
    }

    return {
        "summary": _text_or_empty(data.get("summary"), 300),
        "mastery_signal": mastery_signal,
        "pace_signal": pace_signal,
        "risk_level": risk_level,
        "schedule_hint": _text_or_empty(data.get("schedule_hint"), 240),
        "next_guidance_focus": _guidance_list(data.get("next_guidance_focus"), limit=4),
        "duration_multiplier": _coerce_positive_float(
            data.get("duration_multiplier"),
            1.0,
            minimum=0.75,
            maximum=1.5,
        ),
        "follow_up_task": follow_up_task,
    }


async def _generate_progress_feedback_with_llm(
    *,
    task: Dict[str, Any],
    goal: Optional[Dict[str, Any]],
    plan: Optional[Dict[str, Any]],
    profile: Dict[str, Any],
    feedback: Dict[str, Any],
    recent_feedback: List[Dict[str, Any]],
) -> Dict[str, Any]:
    payload = {
        "today": _local_today().isoformat(),
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "description": task.get("description"),
            "difficulty": task.get("difficulty"),
            "estimated_minutes": task.get("estimated_minutes"),
            "scheduled_date": task.get("scheduled_date"),
        },
        "goal": goal or {},
        "plan": plan or {},
        "student_profile": _profile_with_status(profile),
        "completion_feedback": feedback,
        "recent_goal_feedback": recent_feedback,
    }
    return await _call_learning_llm_json_async(
        system_message=_learning_skill_system_message("progress-feedback"),
        payload=payload,
        label="progress-feedback",
        timeout_seconds=_LEARNING_PROGRESS_FEEDBACK_LLM_TIMEOUT_SECONDS,
        required=True,
        max_tokens=_LEARNING_PROGRESS_FEEDBACK_MAX_OUTPUT_TOKENS,
    )


def _create_feedback_follow_up_task(
    *,
    tasks: Dict[str, Dict[str, Any]],
    plans: Dict[str, Dict[str, Any]],
    source_task: Dict[str, Any],
    analysis: Dict[str, Any],
    now: datetime,
) -> Optional[Dict[str, Any]]:
    follow_up = analysis.get("follow_up_task")
    if not isinstance(follow_up, dict) or not follow_up.get("needed"):
        return None

    title = _text_or_empty(follow_up.get("title"), 120)
    if not title:
        return None

    plan_id = str(source_task.get("plan_id") or "")
    goal_id = str(source_task.get("goal_id") or "")
    sibling_positions = [
        _coerce_positive_int(task.get("position"), 0, minimum=0, maximum=100_000)
        for task in tasks.values()
        if str(task.get("plan_id") or "") == plan_id
    ]
    position = (max(sibling_positions) + 1) if sibling_positions else 0
    task_id = _generate_id()
    new_task = {
        "id": task_id,
        "plan_id": plan_id,
        "goal_id": goal_id,
        "title": title,
        "description": _text_or_empty(follow_up.get("description"), 500)
        or _text_or_empty(analysis.get("schedule_hint"), 240),
        "status": TaskStatus.PENDING.value,
        "difficulty": "medium",
        "estimated_minutes": _coerce_positive_int(
            follow_up.get("estimated_minutes"),
            45,
            minimum=15,
            maximum=180,
        ),
        "scheduled_date": "",
        "completed_at": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "position": position,
        "priority": _coerce_positive_int(
            follow_up.get("priority"),
            1,
            minimum=1,
            maximum=5,
        ),
        "source": "progress-feedback",
        "feedback_source_task_id": source_task.get("id"),
    }
    tasks[task_id] = new_task

    if plan_id in plans:
        plans[plan_id]["total_tasks"] = _coerce_positive_int(
            plans[plan_id].get("total_tasks"),
            0,
            minimum=0,
            maximum=100_000,
        ) + 1
        plans[plan_id]["updated_at"] = now.isoformat()

    return new_task


def _adjust_pending_tasks_from_feedback(
    *,
    tasks: Dict[str, Dict[str, Any]],
    source_task: Dict[str, Any],
    analysis: Dict[str, Any],
    now: datetime,
) -> None:
    multiplier = _coerce_positive_float(
        analysis.get("duration_multiplier"),
        1.0,
        minimum=0.75,
        maximum=1.5,
    )
    if abs(multiplier - 1.0) < 0.05:
        return

    goal_id = str(source_task.get("goal_id") or "")
    pending = [
        task
        for task in tasks.values()
        if str(task.get("goal_id") or "") == goal_id
        and task.get("status") == TaskStatus.PENDING.value
        and task.get("id") != source_task.get("id")
    ]
    pending.sort(
        key=lambda task: (
            str(task.get("scheduled_date") or ""),
            _coerce_positive_int(task.get("priority"), 3, minimum=1, maximum=5),
            _coerce_positive_int(task.get("position"), 0, minimum=0, maximum=100_000),
        )
    )

    for task in pending[:2]:
        original = _coerce_positive_int(
            task.get("estimated_minutes"),
            45,
            minimum=15,
            maximum=240,
        )
        adjusted = int(round(original * multiplier / 5) * 5)
        task["estimated_minutes"] = max(15, min(240, adjusted))
        task["adjusted_by_feedback"] = source_task.get("id")
        task["updated_at"] = now.isoformat()


async def _analyze_and_apply_task_feedback(
    *,
    tasks: Dict[str, Dict[str, Any]],
    plans: Dict[str, Dict[str, Any]],
    goals: Dict[str, Dict[str, Any]],
    task_id: str,
    profile: Dict[str, Any],
    feedback: Dict[str, Any],
    now: datetime,
) -> Dict[str, Any]:
    task = tasks[task_id]
    goal_id = str(task.get("goal_id") or "")
    plan_id = str(task.get("plan_id") or "")
    raw_feedback = {
        **feedback,
        "submitted_at": now.isoformat(),
        "analysis_status": "pending",
    }
    task["learning_feedback"] = raw_feedback

    try:
        parsed = await _generate_progress_feedback_with_llm(
            task=task,
            goal=goals.get(goal_id),
            plan=plans.get(plan_id),
            profile=profile,
            feedback=feedback,
            recent_feedback=_recent_feedback_for_goal(goal_id, tasks),
        )
        analysis = _normalize_progress_feedback_payload(parsed)
        if not analysis:
            raise LearningSkillError(
                "progress-feedback",
                "progress-feedback returned incomplete analysis",
            )
    except LearningSkillError as exc:
        raw_feedback["analysis_status"] = "failed"
        raw_feedback["analysis_error"] = str(exc)
        return raw_feedback

    raw_feedback["analysis_status"] = "ready"
    raw_feedback["analysis"] = analysis
    task["learning_feedback"] = raw_feedback

    if goal_id in goals:
        goals[goal_id]["last_learning_feedback"] = {
            "task_id": task_id,
            "task_title": task.get("title"),
            "summary": analysis.get("summary"),
            "mastery_signal": analysis.get("mastery_signal"),
            "pace_signal": analysis.get("pace_signal"),
            "risk_level": analysis.get("risk_level"),
            "schedule_hint": analysis.get("schedule_hint"),
            "updated_at": now.isoformat(),
        }
        goals[goal_id]["updated_at"] = now.isoformat()

    _create_feedback_follow_up_task(
        tasks=tasks,
        plans=plans,
        source_task=task,
        analysis=analysis,
        now=now,
    )
    _adjust_pending_tasks_from_feedback(
        tasks=tasks,
        source_task=task,
        analysis=analysis,
        now=now,
    )
    return raw_feedback


async def _generate_task_guidance_with_llm(
    *,
    task: Dict[str, Any],
    goal: Optional[Dict[str, Any]],
    plan: Optional[Dict[str, Any]],
    profile: Dict[str, Any],
    recent_feedback: List[Dict[str, Any]],
) -> Dict[str, Any]:
    system_message = _learning_skill_system_message("task-guidance")
    payload = {
        "today": _local_today().isoformat(),
        "task": _compact_task_context(task),
        "goal": _compact_goal_context(goal),
        "plan": _compact_plan_context(plan),
        "student_profile": _compact_profile_context(profile),
        "recent_goal_feedback": _compact_recent_feedback(recent_feedback),
    }
    return await _call_learning_llm_json_async(
        system_message=system_message,
        payload=payload,
        label="task-guidance",
        timeout_seconds=None,
        required=True,
        max_tokens=_LEARNING_TASK_GUIDANCE_MAX_OUTPUT_TOKENS,
    )


async def _build_task_guidance_response(
    *,
    task: Dict[str, Any],
    goal: Optional[Dict[str, Any]],
    plan: Optional[Dict[str, Any]],
    profile: Dict[str, Any],
    recent_feedback: Optional[List[Dict[str, Any]]] = None,
    force_retry: bool = False,
) -> Dict[str, Any]:
    task_id = str(task.get("id") or "")
    signature = _task_guidance_signature(
        task=task,
        goal=goal,
        plan=plan,
        profile=profile,
        recent_feedback=recent_feedback or [],
    )
    if force_retry:
        _clear_task_guidance_error(task_id, signature)

    cached_guidance = _cached_task_guidance(task_id, signature) if task_id else None
    if cached_guidance and not force_retry:
        return {
            "task_id": task.get("id"),
            "status": "ready",
            "generated_by": "learning-skill-cache",
            "skill": "task-guidance",
            "updated_at": cached_guidance.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            **cached_guidance,
        }

    cached_error = None if force_retry else _cached_task_guidance_error(task_id, signature)
    if cached_error:
        return {
            "task_id": task.get("id"),
            "status": "failed",
            "generated_by": "learning-skill-failed",
            "skill": "task-guidance",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": cached_error,
            "summary": "",
            "steps": [],
            "focus_points": [],
            "pitfalls": [],
            "checklist": [],
        }

    cache_key = _task_guidance_error_key(task_id, signature)
    running = _TASK_GUIDANCE_INFLIGHT.get(cache_key)
    if running is None:
        running = asyncio.create_task(
            _generate_task_guidance_with_llm(
                task=task,
                goal=goal,
                plan=plan,
                profile=profile,
                recent_feedback=recent_feedback or [],
            )
        )
        _TASK_GUIDANCE_INFLIGHT[cache_key] = running
        running.add_done_callback(
            lambda completed, guidance_task_id=task_id, guidance_signature=signature: _store_task_guidance_inflight_result(
                guidance_task_id,
                guidance_signature,
                completed,
            )
        )

    if not running.done():
        return {
            "task_id": task.get("id"),
            "status": "generating",
            "generated_by": "learning-skill-pending",
            "skill": "task-guidance",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summary": "",
            "steps": [],
            "focus_points": [],
            "pitfalls": [],
            "checklist": [],
        }

    try:
        parsed = await running
    except LearningSkillError as exc:
        _save_task_guidance_error(task_id, signature, str(exc))
        raise _learning_skill_http_exception(exc) from exc

    guidance = _normalize_task_guidance_payload(parsed)
    if not guidance:
        message = "模型没有返回可用的结构化任务建议。"
        _save_task_guidance_error(task_id, signature, message)
        return {
            "task_id": task.get("id"),
            "status": "failed",
            "generated_by": "learning-skill-failed",
            "skill": "task-guidance",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": message,
            "summary": "",
            "steps": [],
            "focus_points": [],
            "pitfalls": [],
            "checklist": [],
        }

    guidance["updated_at"] = datetime.now(timezone.utc).isoformat()
    if task_id:
        _save_task_guidance_cache(task_id, signature, guidance)
    _TASK_GUIDANCE_INFLIGHT.pop(cache_key, None)

    return {
        "task_id": task.get("id"),
        "status": "ready",
        "generated_by": "learning-skill",
        "skill": "task-guidance",
        "updated_at": guidance["updated_at"],
        **guidance,
    }


def _first_value(source: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return value
    return None


def _first_list_value(source: Dict[str, Any], *keys: str) -> List[Any]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, list):
            return value
    return []


def _duration_minutes_value(value: Any, default: int) -> int:
    if isinstance(value, str):
        text = value.strip().lower()
        number = _number_from_text(text)
        if number is not None:
            if any(unit in text for unit in ["小时", "小時", "hour", "hr", "h"]):
                return _coerce_positive_int(number * 60, default, minimum=10, maximum=240)
            return _coerce_positive_int(number, default, minimum=10, maximum=240)
    return _coerce_positive_int(value, default, minimum=10, maximum=240)


def _raw_plan_list(plan_spec: Dict[str, Any]) -> List[Any]:
    raw_plans = _first_value(
        plan_spec,
        "plans",
        "p",
        "phases",
        "phase",
        "stages",
        "modules",
        "sections",
        "units",
    )
    if isinstance(raw_plans, dict):
        nested = _raw_plan_list(raw_plans)
        if nested:
            return nested
    if isinstance(raw_plans, list):
        return raw_plans

    raw_plan = plan_spec.get("plan")
    if isinstance(raw_plan, list):
        return raw_plan
    if isinstance(raw_plan, dict):
        nested = _raw_plan_list(raw_plan)
        return nested or [raw_plan]

    raw_tasks = _first_list_value(
        plan_spec,
        "tasks",
        "t",
        "items",
        "lessons",
        "steps",
        "activities",
        "topics",
    )
    if raw_tasks:
        return [
            {
                "n": _text_or_empty(
                    _first_value(plan_spec, "name", "n", "title", "phase", "module"),
                    120,
                )
                or "学习任务",
                "d": _text_or_empty(
                    _first_value(plan_spec, "description", "d", "summary", "objective"),
                    200,
                ),
                "t": raw_tasks,
            }
        ]
    return []


def _raw_task_list(raw_plan: Dict[str, Any]) -> List[Any]:
    raw_tasks = _first_list_value(
        raw_plan,
        "tasks",
        "t",
        "items",
        "lessons",
        "steps",
        "activities",
        "topics",
    )
    if raw_tasks:
        return raw_tasks

    raw_task = raw_plan.get("task")
    if isinstance(raw_task, list):
        return raw_task
    if isinstance(raw_task, dict):
        return [raw_task]
    return []


def _plan_spec_goal_payload(plan_spec: Dict[str, Any]) -> Dict[str, Any]:
    goal = _first_value(plan_spec, "goal", "g", "course", "target")
    return goal if isinstance(goal, dict) else plan_spec


def _estimated_hours_from_plans(plan_spec: Dict[str, Any]) -> Optional[float]:
    total_minutes = 0
    for raw_plan in _raw_plan_list(plan_spec):
        if not isinstance(raw_plan, dict):
            continue
        for raw_task in _raw_task_list(raw_plan):
            if not isinstance(raw_task, dict):
                continue
            total_minutes += _duration_minutes_value(
                _first_value(
                    raw_task,
                    "estimated_minutes",
                    "m",
                    "minutes",
                    "duration_minutes",
                    "duration",
                    "time",
                ),
                0,
            )
    return round(total_minutes / 60, 1) if total_minutes > 0 else None


def _normalize_plan_spec(plan_spec: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(plan_spec, dict):
        return []
    raw_plans = _raw_plan_list(plan_spec)
    if not raw_plans:
        return []

    plans: List[Dict[str, Any]] = []
    for plan_index, raw_plan in enumerate(raw_plans, start=1):
        if isinstance(raw_plan, str):
            raw_plan = {"n": raw_plan, "t": [raw_plan]}
        if not isinstance(raw_plan, dict):
            continue
        name = _text_or_empty(
            _first_value(raw_plan, "name", "n", "title", "phase", "module", "stage"),
            120,
        ) or f"阶段 {plan_index}"
        description = _text_or_empty(
            _first_value(raw_plan, "description", "d", "summary", "objective", "goal"),
            200,
        )
        raw_tasks = _raw_task_list(raw_plan)

        normalized_tasks: List[Dict[str, Any]] = []
        for raw_task in raw_tasks:
            if isinstance(raw_task, str):
                raw_task = {"title": raw_task}
            if not isinstance(raw_task, dict):
                continue
            title = _text_or_empty(
                _first_value(raw_task, "title", "t", "name", "n", "topic", "task"),
                140,
            )
            if not title:
                continue
            difficulty = _text_or_empty(
                _first_value(raw_task, "difficulty", "diff", "level"),
                20,
            ).lower()
            if difficulty not in {"easy", "medium", "hard"}:
                difficulty = "medium"
            task: Dict[str, Any] = {
                "title": title,
                "description": _text_or_empty(
                    _first_value(raw_task, "description", "d", "summary", "objective", "detail"),
                    180,
                ),
                "difficulty": difficulty,
                "estimated_minutes": _duration_minutes_value(
                    _first_value(
                        raw_task,
                        "estimated_minutes",
                        "m",
                        "minutes",
                        "duration_minutes",
                        "duration",
                        "time",
                    ),
                    60,
                ),
                "priority": _coerce_positive_int(
                    _first_value(raw_task, "priority", "p", "order", "rank"),
                    plan_index,
                    minimum=1,
                    maximum=5,
                ),
            }
            if raw_task.get("scheduled_offset_days") is not None:
                task["scheduled_offset_days"] = _coerce_positive_int(
                    raw_task.get("scheduled_offset_days"),
                    len(normalized_tasks),
                    minimum=0,
                    maximum=3650,
                )
            scheduled_date = _coerce_deadline(raw_task.get("scheduled_date"))
            if scheduled_date:
                task["scheduled_date"] = scheduled_date
            normalized_tasks.append(task)

        if normalized_tasks:
            plans.append(
                {
                    "name": name,
                    "description": description,
                    "tasks": normalized_tasks,
                }
            )

    return plans


def _require_course_plan_spec(plan_spec: Dict[str, Any]) -> None:
    if not isinstance(plan_spec, dict):
        raise LearningSkillError(
            "course-planner",
            "course-planner returned no valid JSON object",
        )
    if not _normalize_plan_spec(plan_spec):
        raise LearningSkillError(
            "course-planner",
            "course-planner returned no valid plans or tasks",
        )
    goal = _plan_spec_goal_payload(plan_spec)
    if not isinstance(goal, dict):
        raise LearningSkillError(
            "course-planner",
            "course-planner returned no goal payload",
        )
    if not _text_or_empty(
        _first_value(goal, "name", "n", "title", "course_name", "goal_name"),
        160,
    ):
        raise LearningSkillError(
            "course-planner",
            "course-planner returned an empty goal name",
        )
    if (
        _float_or_none(
            _first_value(goal, "estimated_hours", "h", "total_hours", "hours")
        )
        is None
        and _estimated_hours_from_plans(plan_spec) is None
    ):
        raise LearningSkillError(
            "course-planner",
            "course-planner returned no estimated_hours",
        )


async def _generate_requirement_plan_spec_with_llm(
    *,
    draft: Dict[str, Any],
    estimate: Dict[str, Any],
    profile: Dict[str, Any],
    force_retry: bool = False,
) -> Dict[str, Any]:
    system_message = _learning_skill_system_message("course-planner")
    signature = _course_plan_signature(draft=draft, estimate=estimate, profile=profile)
    if force_retry:
        _clear_course_plan_spec_error(signature)

    cached = _cached_course_plan_spec(signature)
    if cached:
        return cached

    cached_error = _cached_course_plan_spec_error(signature)
    if cached_error:
        raise LearningSkillError("course-planner", cached_error)

    payload = _course_planner_payload(draft=draft, estimate=estimate, profile=profile)
    running = _COURSE_PLAN_INFLIGHT.get(signature)
    if running is None:
        running = asyncio.create_task(
            asyncio.to_thread(
                _invoke_learning_llm_json_for_thread,
                system_message=system_message,
                payload=payload,
                label="course-planner",
                max_tokens=_LEARNING_PLAN_MAX_OUTPUT_TOKENS,
            )
        )
        _COURSE_PLAN_INFLIGHT[signature] = running
        running.add_done_callback(
            lambda completed, course_signature=signature: _store_course_plan_inflight_result(
                course_signature,
                completed,
            )
        )

    if not running.done():
        raise LearningSkillPendingError("course-planner")

    parsed = await running

    if not parsed:
        _save_course_plan_spec_error(signature, "模型没有返回可用的结构化计划。")
        raise LearningSkillError(
            "course-planner",
            "course-planner did not return a valid JSON object",
        )
    _save_course_plan_spec_cache(signature, parsed)
    return parsed if isinstance(parsed, dict) else {}


def _goal_payload_from_plan_spec(
    draft: Dict[str, Any],
    estimate: Dict[str, Any],
    plan_spec: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    goal_payload = _plan_spec_goal_payload(plan_spec) if isinstance(plan_spec, dict) else None
    if not isinstance(goal_payload, dict):
        goal_payload = {}
    model_hours = _estimated_hours_from_plans(plan_spec) if isinstance(plan_spec, dict) else None

    goal_type = _normalize_goal_type(goal_payload.get("goal_type") or goal_payload.get("type")) or str(
        draft.get("goal_type") or "skill"
    )
    try:
        goal_type = LearningGoalType(goal_type).value
    except ValueError:
        goal_type = LearningGoalType.SKILL.value

    return {
        "name": _text_or_empty(
            _first_value(goal_payload, "name", "n", "title", "course_name", "goal_name"),
            160,
        )
        or str(draft.get("goal_name") or "学习目标"),
        "description": _text_or_empty(
            _first_value(goal_payload, "description", "d", "summary", "objective"),
            1200,
        )
        or _goal_description_from_requirement(draft),
        "goal_type": goal_type,
        "estimated_hours": _coerce_positive_float(
            _first_value(goal_payload, "estimated_hours", "h", "total_hours", "hours")
            or model_hours,
            float(estimate.get("estimated_hours") or model_hours or 70.0),
            minimum=1,
            maximum=2000,
        ),
        "tags": _normalize_text_list(goal_payload.get("tags")) or draft.get("tags", []),
        "prerequisites": _normalize_text_list(
            goal_payload.get("prerequisites") or goal_payload.get("pre")
        ),
    }


def _build_requirement_plans_and_tasks(
    goal_id: str,
    draft: Dict[str, Any],
    estimate: Dict[str, Any],
    now: datetime,
    plan_spec: Optional[Dict[str, Any]] = None,
    start_position: int = 0,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    goal_name = str(draft.get("goal_name") or "学习目标")
    plans: List[Dict[str, Any]] = []
    tasks: List[Dict[str, Any]] = []
    llm_plans = _normalize_plan_spec(plan_spec)
    if not llm_plans:
        raise LearningSkillError(
            "course-planner",
            "course-planner returned no valid plans or tasks",
        )

    if llm_plans:
        for phase_index, plan_template in enumerate(llm_plans, start=1):
            plan_id = _generate_id()
            phase_tasks = plan_template["tasks"]
            plan: Dict[str, Any] = {
                "id": plan_id,
                "goal_id": goal_id,
                "name": plan_template["name"],
                "description": plan_template["description"],
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "total_tasks": len(phase_tasks),
                "completed_tasks": 0,
                "position": start_position + phase_index - 1,
            }
            plans.append(plan)

            for position, task_template in enumerate(phase_tasks):
                task_id = _generate_id()
                task: Dict[str, Any] = {
                    "id": task_id,
                    "plan_id": plan_id,
                    "goal_id": goal_id,
                    "title": task_template["title"],
                    "description": task_template["description"],
                    "status": "pending",
                    "difficulty": task_template["difficulty"],
                    "estimated_minutes": task_template["estimated_minutes"],
                    "scheduled_date": "",
                    "completed_at": None,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "position": position,
                    "priority": task_template["priority"],
                }
                tasks.append(task)

        return plans, tasks

    task_templates = {
        "诊断与路线": [
            "完成基础自测并记录薄弱点",
            "拆解核心知识地图",
            "确认阶段成果和检查标准",
        ],
        "核心输入": [
            "学习本阶段核心概念",
            "整理一页结构化笔记",
            "完成针对性练习",
        ],
        "练习实战": [
            "完成一个小型实战任务",
            "复盘错误和卡点",
            "补齐一个薄弱知识点",
        ],
        "复盘冲刺": [
            "完成综合复盘",
            "做一次模拟检验",
            "更新下一阶段学习策略",
        ],
    }

    for phase_index, phase in enumerate(estimate.get("phases", []), start=1):
        phase_name = str(phase.get("name") or f"阶段 {phase_index}")
        plan_id = _generate_id()
        phase_tasks = task_templates.get(phase_name, task_templates["核心输入"])
        plan: Dict[str, Any] = {
            "id": plan_id,
            "goal_id": goal_id,
            "name": f"{goal_name} - {phase_name}",
            "description": f"{phase_name}：预计 {phase.get('hours')} 小时",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "total_tasks": len(phase_tasks),
            "completed_tasks": 0,
            "position": start_position + phase_index - 1,
        }
        plans.append(plan)

        for position, title in enumerate(phase_tasks):
            task_minutes = _coerce_positive_int(
                draft.get("daily_minutes"),
                60,
                minimum=20,
                maximum=120,
            )
            task_id = _generate_id()
            task: Dict[str, Any] = {
                "id": task_id,
                "plan_id": plan_id,
                "goal_id": goal_id,
                "title": f"{phase_name}：{title}",
                "description": f"围绕 {goal_name}，按「{draft.get('preference')}」方式推进。",
                "status": "pending",
                "difficulty": "medium",
                "estimated_minutes": task_minutes,
                "scheduled_date": "",
                "completed_at": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "position": position,
                "priority": min(5, phase_index),
            }
            tasks.append(task)

    return plans, tasks


@router.post("/requirements/analyze", response_model=Dict[str, Any])
async def analyze_learning_requirement(
    request: Request, body: AnalyzeLearningRequirementRequest
):
    sessions = _load_requirement_sessions()
    previous = sessions.get(body.session_id or "", {})
    message = body.message.strip() or previous.get("message", "")
    previous_answers = previous.get("answers", {})
    answers = {**previous_answers, **body.answers}

    if not message and not answers:
        raise HTTPException(status_code=400, detail="Requirement message is required")

    goals = _load_goals()
    profile = _require_complete_learning_profile()
    draft = _build_requirement_draft(
        message, answers, goals, body.existing_goal_id, profile
    )
    llm_draft = await _generate_requirement_draft_with_llm(
        message=message,
        answers=answers,
        profile=profile,
        goals=goals,
        existing_goal_id=body.existing_goal_id,
    )
    if llm_draft:
        draft = dict(draft)
        _apply_requirement_updates(draft, llm_draft)
        _apply_requirement_updates(draft, answers)
        draft["raw_text"] = message
        draft["student_context"] = _student_context_from_profile(profile)
        draft["time_constraints"] = _time_constraints_from_profile(profile)
        draft = _finalize_requirement_draft(draft, message, goals)
    missing = _missing_requirement_fields(draft)
    estimate = _estimate_requirement(draft)
    draft = _sync_requirement_daily_minutes(draft, estimate)
    related = _related_goals(draft, goals)
    profile_status = _profile_status(profile)
    session_id = body.session_id or _generate_id()
    now = datetime.now(timezone.utc)

    result: Dict[str, Any] = {
        "session_id": session_id,
        "draft": draft,
        "missing_fields": missing,
        "questions": _requirement_questions(missing),
        "ready": len(missing) == 0,
        "classification": draft.get("classification"),
        "classification_label": {
            "new_goal": "新学习目标",
            "goal_extension": "已有目标补充",
            "temp_task": "临时学习任务",
        }.get(str(draft.get("classification")), "新学习目标"),
        "related_goals": related,
        "estimate": estimate,
        "profile": _profile_with_status(profile),
        "profile_missing_fields": profile_status["missing_fields"],
        "profile_completeness": profile_status["completeness"],
    }

    sessions[session_id] = {
        "id": session_id,
        "message": message,
        "answers": answers,
        "profile": profile,
        "draft": draft,
        "result": result,
        "status": "ready" if result["ready"] else "clarifying",
        "created_at": previous.get("created_at") or now.isoformat(),
        "updated_at": now.isoformat(),
    }
    _save_requirement_sessions(sessions)
    return result


@router.post("/requirements/finalize", response_model=Dict[str, Any])
async def finalize_learning_requirement(
    request: Request, body: FinalizeLearningRequirementRequest
):
    profile = _require_complete_learning_profile()
    sessions = _load_requirement_sessions()
    session = sessions.get(body.session_id or "", {})
    session_id = body.session_id or str(session.get("id") or _generate_id())
    draft = body.draft or session.get("draft")
    if not isinstance(draft, dict):
        raise HTTPException(status_code=400, detail="Requirement draft is required")
    draft = dict(draft)
    _apply_requirement_updates(draft, dict(draft))

    if not draft.get("student_context"):
        draft["student_context"] = _student_context_from_profile(profile)
    if not draft.get("time_constraints"):
        draft["time_constraints"] = _time_constraints_from_profile(profile)
    draft = _finalize_requirement_draft(
        draft,
        str(draft.get("raw_text") or session.get("message") or ""),
        _load_goals(),
    )

    missing = _missing_requirement_fields(draft)
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Requirement is incomplete",
                "missing_fields": missing,
                "questions": _requirement_questions(missing),
            },
        )

    try:
        goal_type = LearningGoalType(str(draft.get("goal_type") or "skill")).value
    except ValueError:
        goal_type = LearningGoalType.SKILL.value

    estimate = _estimate_requirement(draft)
    draft = _sync_requirement_daily_minutes(draft, estimate)
    finalized_goal_id = session.get("goal_id") if session.get("status") == "finalized" else None
    if isinstance(finalized_goal_id, str):
        existing_goals = _load_goals()
        if finalized_goal_id in existing_goals:
            existing_plans = _load_plans()
            existing_tasks = _load_tasks()
            goal_plans, goal_tasks = _goal_records(finalized_goal_id, existing_plans, existing_tasks)
            return {
                "message": "该学习目标已生成，未重复创建。",
                "status": "ready",
                "plan_status": "ready",
                "session_id": session_id,
                "duplicate_goal": False,
                "goal_created": False,
                "goal": existing_goals[finalized_goal_id],
                "plans": goal_plans,
                "tasks": goal_tasks,
                "estimate": estimate,
            }

    goals_snapshot = _load_goals()
    existing_goal_id = draft.get("existing_goal_id")
    use_existing_snapshot = (
        draft.get("classification") in {"goal_extension", "temp_task"}
        and isinstance(existing_goal_id, str)
        and existing_goal_id in goals_snapshot
    )
    duplicate_snapshot = (
        None
        if use_existing_snapshot
        else _find_similar_active_goal({**draft, "goal_type": goal_type}, goals_snapshot)
    )
    plan_spec: Dict[str, Any] = {}
    if use_existing_snapshot or duplicate_snapshot is None:
        plan_signature = _course_plan_signature(draft=draft, estimate=estimate, profile=profile)
        try:
            plan_spec = await _generate_requirement_plan_spec_with_llm(
                draft=draft,
                estimate=estimate,
                profile=profile,
                force_retry=body.force_retry,
            )
            _require_course_plan_spec(plan_spec)
        except LearningSkillPendingError as exc:
            if exc.skill_name != "course-planner":
                raise _learning_skill_http_exception(exc) from exc
            now = datetime.now(timezone.utc)
            sessions = _load_requirement_sessions()
            existing_session = sessions.get(session_id, {})
            sessions[session_id] = {
                **existing_session,
                "id": session_id,
                "draft": draft,
                "estimate": estimate,
                "status": "planning",
                "plan_generation": {
                    "status": "generating",
                    "signature": plan_signature,
                    "skill": "course-planner",
                    "started_at": (
                        existing_session.get("plan_generation", {}).get("started_at")
                        if isinstance(existing_session.get("plan_generation"), dict)
                        else None
                    )
                    or now.isoformat(),
                    "updated_at": now.isoformat(),
                },
                "updated_at": now.isoformat(),
            }
            _save_requirement_sessions(sessions)
            return {
                "message": "模型正在生成学习计划，已保留本次请求；完成后会自动复用同一次模型结果。",
                "status": "generating",
                "plan_status": "generating",
                "session_id": session_id,
                "retry_after_seconds": _LEARNING_PLAN_RETRY_AFTER_SECONDS,
                "duplicate_goal": False,
                "duplicate_of": None,
                "goal_created": False,
                "goal": None,
                "plans": [],
                "tasks": [],
                "estimate": estimate,
            }
        except LearningSkillError as exc:
            now = datetime.now(timezone.utc)
            sessions = _load_requirement_sessions()
            existing_session = sessions.get(session_id, {})
            sessions[session_id] = {
                **existing_session,
                "id": session_id,
                "draft": draft,
                "estimate": estimate,
                "status": "planning_failed",
                "plan_generation": {
                    "status": "failed",
                    "signature": plan_signature,
                    "skill": exc.skill_name,
                    "message": str(exc),
                    "updated_at": now.isoformat(),
                },
                "updated_at": now.isoformat(),
            }
            _save_requirement_sessions(sessions)
            raise _learning_skill_http_exception(exc) from exc
    goal_payload = _goal_payload_from_plan_spec(draft, estimate, plan_spec)
    estimate["estimated_hours"] = goal_payload["estimated_hours"]

    with _LEARNING_CREATE_LOCK:
        now = datetime.now(timezone.utc)
        sessions = _load_requirement_sessions()
        session = sessions.get(body.session_id or "", {})
        goals = _load_goals()
        plans = _load_plans()
        tasks = _load_tasks()

        finalized_goal_id = session.get("goal_id") if session.get("status") == "finalized" else None
        if isinstance(finalized_goal_id, str) and finalized_goal_id in goals:
            goal_plans, goal_tasks = _goal_records(finalized_goal_id, plans, tasks)
            return {
                "message": "该学习目标已生成，未重复创建。",
                "status": "ready",
                "plan_status": "ready",
                "session_id": session_id,
                "duplicate_goal": False,
                "goal_created": False,
                "goal": goals[finalized_goal_id],
                "plans": goal_plans,
                "tasks": goal_tasks,
                "estimate": estimate,
            }

        existing_goal_id = draft.get("existing_goal_id")
        use_existing_goal = (
            draft.get("classification") in {"goal_extension", "temp_task"}
            and isinstance(existing_goal_id, str)
            and existing_goal_id in goals
        )
        duplicate_goal = None if use_existing_goal else _find_similar_active_goal({**draft, "goal_type": goal_type}, goals)
        duplicate_reused = duplicate_goal is not None

        if use_existing_goal:
            goal_id = existing_goal_id
            goal = goals[goal_id]
            goal["updated_at"] = now.isoformat()
            goal_created = False
            new_plans, new_tasks = _build_requirement_plans_and_tasks(
                goal_id,
                draft,
                estimate,
                now,
                plan_spec,
                start_position=_next_plan_position(plans, goal_id),
            )
            for plan in new_plans:
                plans[plan["id"]] = plan
            for task in new_tasks:
                tasks[task["id"]] = task
            _refresh_today_task_schedule(
                tasks,
                goals,
                profile,
                _local_now(),
                force=True,
                plans=plans,
            )
            _save_goals(goals)
            _save_plans(plans)
            _save_tasks(tasks)
        elif duplicate_goal:
            goal_id = str(duplicate_goal["id"])
            goal = duplicate_goal
            goal_created = False
            new_plans, new_tasks = _goal_records(goal_id, plans, tasks)
        else:
            goal_id = _generate_id()
            target_date = draft.get("deadline")
            if not target_date and draft.get("timeframe_days"):
                target_date = (now.date() + timedelta(days=int(draft["timeframe_days"]))).isoformat()
            goal = {
                "id": goal_id,
                "name": goal_payload["name"],
                "description": goal_payload["description"],
                "goal_type": goal_payload["goal_type"],
                "status": "active",
                "target_date": target_date,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "estimated_hours": goal_payload["estimated_hours"],
                "progress": 0.0,
                "tags": goal_payload["tags"],
                "prerequisites": goal_payload["prerequisites"],
                "requirement": draft,
            }
            goals[goal_id] = goal
            goal_created = True
            new_plans, new_tasks = _build_requirement_plans_and_tasks(goal_id, draft, estimate, now, plan_spec)
            for plan in new_plans:
                plans[plan["id"]] = plan
            for task in new_tasks:
                tasks[task["id"]] = task
            _refresh_today_task_schedule(
                tasks,
                goals,
                profile,
                _local_now(),
                force=True,
                plans=plans,
            )
            _save_goals(goals)
            _save_plans(plans)
            _save_tasks(tasks)

        if session_id:
            sessions[session_id] = {
                **session,
                "id": session_id,
                "draft": draft,
                "status": "finalized",
                "goal_id": goal_id,
                "plan_generation": {
                    "status": "ready",
                    "updated_at": now.isoformat(),
                },
                "updated_at": now.isoformat(),
            }
            _save_requirement_sessions(sessions)

        return {
            "message": "已复用相似学习目标，未重复创建。" if duplicate_reused else "学习目标与计划已生成",
            "status": "ready",
            "plan_status": "ready",
            "session_id": session_id,
            "duplicate_goal": duplicate_reused,
            "duplicate_of": goal_id if duplicate_reused else None,
            "goal_created": goal_created,
            "goal": goal,
            "plans": new_plans,
            "tasks": new_tasks,
            "estimate": estimate,
        }


class GeneratePlanRequest(BaseModel):
    topic: str


@router.post("/generate", response_model=Dict[str, Any])
async def generate_learning_plan(request: Request, body: GeneratePlanRequest):
    profile = _require_complete_learning_profile()

    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")

    goals = _load_goals()
    draft = _build_requirement_draft(topic, {}, goals, None, profile)
    llm_draft = await _generate_requirement_draft_with_llm(
        message=topic,
        answers={},
        profile=profile,
        goals=goals,
        existing_goal_id=None,
    )
    if llm_draft:
        _apply_requirement_updates(draft, llm_draft)

    profile_preferences = _normalize_text_list(profile.get("learning_preferences"))
    draft["goal_name"] = str(draft.get("goal_name") or topic).strip()
    draft["purpose"] = str(
        draft.get("purpose")
        or _purpose_from_career_target(str(profile.get("career_target") or ""))
        or "系统学习"
    ).strip()
    draft["current_level"] = str(
        draft.get("current_level") or profile.get("baseline_level") or "入门"
    ).strip()
    draft["target_level"] = str(draft.get("target_level") or "系统掌握并能完成实践").strip()
    draft["daily_minutes"] = draft.get("daily_minutes") or _profile_average_daily_minutes(profile) or 60
    draft["preference"] = str(
        draft.get("preference")
        or (profile_preferences[0] if profile_preferences else "")
        or "项目实战"
    ).strip()
    draft["goal_type"] = _normalize_goal_type(draft.get("goal_type")) or _infer_goal_type(
        topic,
        str(draft.get("purpose") or ""),
    )
    draft["student_context"] = _student_context_from_profile(profile)
    draft["time_constraints"] = _time_constraints_from_profile(profile)
    draft = _finalize_requirement_draft(draft, topic, goals)

    result = await finalize_learning_requirement(
        request,
        FinalizeLearningRequirementRequest(draft=draft),
    )
    if result.get("plan_status") == "generating":
        return {
            "message": result.get("message") or "模型正在生成学习计划",
            "status": "generating",
            "plan_status": "generating",
            "session_id": result.get("session_id"),
            "retry_after_seconds": result.get("retry_after_seconds"),
            "duplicate_goal": False,
            "duplicate_of": None,
            "goal_created": False,
            "goal_id": "",
            "goal_name": str(draft.get("goal_name") or topic),
            "total_plans": 0,
            "total_tasks": 0,
        }
    return {
        "message": result.get("message") or "学习计划已生成",
        "duplicate_goal": result.get("duplicate_goal", False),
        "duplicate_of": result.get("duplicate_of"),
        "goal_created": result.get("goal_created", True),
        "goal_id": result["goal"]["id"],
        "goal_name": result["goal"]["name"],
        "total_plans": len(result.get("plans") or []),
        "total_tasks": len(result.get("tasks") or []),
    }

def _load_wrong_questions() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "wrong_questions.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_wrong_questions(questions: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "wrong_questions.json"
    path.write_text(json.dumps(questions, indent=2), encoding="utf-8")


def _load_question_reviews() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "question_reviews.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_question_reviews(reviews: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "question_reviews.json"
    path.write_text(json.dumps(reviews, indent=2), encoding="utf-8")


def _load_similar_questions() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "similar_questions.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_similar_questions(questions: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "similar_questions.json"
    path.write_text(json.dumps(questions, indent=2), encoding="utf-8")


class CreateWrongQuestionRequest(BaseModel):
    subject: str = Field(..., description="学科名称")
    knowledge_point: str = Field(..., description="知识点")
    question_content: str = Field(..., description="题目内容")
    user_answer: str = Field(..., description="用户答案")
    correct_answer: str = Field(..., description="正确答案")
    error_type: str = Field(..., description="错误类型")
    difficulty: str = Field(default="medium", description="难度")
    error_analysis: str = Field(default="", description="错误分析")
    solution: str = Field(default="", description="解题思路")
    tags: List[str] = Field(default_factory=list)
    question_file: Optional[str] = Field(default=None, description="题目原始文件路径")
    correct_answer_file: Optional[str] = Field(default=None, description="正确答案原始文件路径")
    user_answer_file: Optional[str] = Field(default=None, description="用户答案原始文件路径")


class UpdateWrongQuestionRequest(BaseModel):
    subject: Optional[str] = None
    knowledge_point: Optional[str] = None
    question_content: Optional[str] = None
    user_answer: Optional[str] = None
    correct_answer: Optional[str] = None
    error_type: Optional[str] = None
    difficulty: Optional[str] = None
    error_analysis: Optional[str] = None
    solution: Optional[str] = None
    tags: Optional[List[str]] = None
    question_file: Optional[str] = None
    correct_answer_file: Optional[str] = None
    user_answer_file: Optional[str] = None


class ReviewQuestionRequest(BaseModel):
    user_answer: str = Field(..., description="用户答案")
    is_correct: bool = Field(..., description="是否正确")
    time_spent_seconds: int = Field(default=0)
    notes: str = Field(default="")


@router.get("/wrong-questions", response_model=List[Dict[str, Any]])
async def get_wrong_questions(
    request: Request,
    subject: Optional[str] = None,
    knowledge_point: Optional[str] = None,
    error_type: Optional[str] = None,
    difficulty: Optional[str] = None,
    mastery_status: Optional[str] = None,
):
    questions = _load_wrong_questions()
    result = list(questions.values())

    if subject:
        result = [q for q in result if q["subject"] == subject]
    if knowledge_point:
        result = [q for q in result if q["knowledge_point"] == knowledge_point]
    if error_type:
        result = [q for q in result if q["error_type"] == error_type]
    if difficulty:
        result = [q for q in result if q["difficulty"] == difficulty]
    if mastery_status:
        result = [q for q in result if q["mastery_status"] == mastery_status]

    result.sort(key=lambda q: q["created_at"], reverse=True)
    return result


@router.get("/wrong-questions/{question_id}", response_model=Dict[str, Any])
async def get_wrong_question(request: Request, question_id: str):
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")
    return questions[question_id]


@router.post("/wrong-questions", response_model=Dict[str, Any])
async def create_wrong_question(request: Request, body: CreateWrongQuestionRequest):
    questions = _load_wrong_questions()
    now = datetime.now(timezone.utc)
    question_id = _generate_id()

    try:
        ErrorType(body.error_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的错误类型")

    try:
        TaskDifficulty(body.difficulty)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的难度等级")

    question: Dict[str, Any] = {
        "id": question_id,
        "subject": body.subject,
        "knowledge_point": body.knowledge_point,
        "question_content": body.question_content,
        "user_answer": body.user_answer,
        "correct_answer": body.correct_answer,
        "error_type": body.error_type,
        "difficulty": body.difficulty,
        "mastery_status": "weak",
        "error_analysis": body.error_analysis,
        "solution": body.solution,
        "tags": body.tags,
        "question_file": body.question_file,
        "correct_answer_file": body.correct_answer_file,
        "user_answer_file": body.user_answer_file,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "review_count": 0,
        "correct_count": 0,
        "last_review_at": None,
        "next_review_at": (now + timedelta(days=1)).isoformat(),
    }

    questions[question_id] = question
    _save_wrong_questions(questions)

    return question


@router.put("/wrong-questions/{question_id}", response_model=Dict[str, Any])
async def update_wrong_question(request: Request, question_id: str, body: UpdateWrongQuestionRequest):
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")

    question = questions[question_id]

    if body.subject is not None:
        question["subject"] = body.subject
    if body.knowledge_point is not None:
        question["knowledge_point"] = body.knowledge_point
    if body.question_content is not None:
        question["question_content"] = body.question_content
    if body.user_answer is not None:
        question["user_answer"] = body.user_answer
    if body.correct_answer is not None:
        question["correct_answer"] = body.correct_answer
    if body.error_type is not None:
        try:
            ErrorType(body.error_type)
            question["error_type"] = body.error_type
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的错误类型")
    if body.difficulty is not None:
        try:
            TaskDifficulty(body.difficulty)
            question["difficulty"] = body.difficulty
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的难度等级")
    if body.error_analysis is not None:
        question["error_analysis"] = body.error_analysis
    if body.solution is not None:
        question["solution"] = body.solution
    if body.tags is not None:
        question["tags"] = body.tags
    if body.question_file is not None:
        question["question_file"] = body.question_file
    if body.correct_answer_file is not None:
        question["correct_answer_file"] = body.correct_answer_file
    if body.user_answer_file is not None:
        question["user_answer_file"] = body.user_answer_file

    question["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_wrong_questions(questions)

    return question


@router.delete("/wrong-questions/{question_id}")
async def delete_wrong_question(request: Request, question_id: str):
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")

    del questions[question_id]
    _save_wrong_questions(questions)

    reviews = _load_question_reviews()
    remaining_reviews = {rid: r for rid, r in reviews.items() if r["question_id"] != question_id}
    _save_question_reviews(remaining_reviews)

    similar = _load_similar_questions()
    remaining_similar = {sid: s for sid, s in similar.items() if s["original_question_id"] != question_id}
    _save_similar_questions(remaining_similar)

    return {"message": "错题删除成功"}


@router.post("/wrong-questions/{question_id}/review", response_model=Dict[str, Any])
async def review_wrong_question(request: Request, question_id: str, body: ReviewQuestionRequest):
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")

    question = questions[question_id]
    now = datetime.now(timezone.utc)

    reviews = _load_question_reviews()
    review_id = _generate_id()
    review: Dict[str, Any] = {
        "id": review_id,
        "question_id": question_id,
        "user_answer": body.user_answer,
        "is_correct": body.is_correct,
        "review_date": now.isoformat(),
        "time_spent_seconds": body.time_spent_seconds,
        "notes": body.notes,
    }
    reviews[review_id] = review
    _save_question_reviews(reviews)

    question["review_count"] += 1
    question["last_review_at"] = now.isoformat()

    if body.is_correct:
        question["correct_count"] += 1
        correct_rate = question["correct_count"] / question["review_count"]
        if correct_rate >= 0.8:
            question["mastery_status"] = "mastered"
        elif correct_rate >= 0.6:
            question["mastery_status"] = "proficient"
        elif correct_rate >= 0.4:
            question["mastery_status"] = "basic"
        else:
            question["mastery_status"] = "weak"

        days_interval = min(int(question["review_count"] * 1.5), 30)
        question["next_review_at"] = (now + timedelta(days=days_interval)).isoformat()
    else:
        question["mastery_status"] = "weak"
        question["next_review_at"] = (now + timedelta(days=1)).isoformat()

    question["updated_at"] = now.isoformat()
    _save_wrong_questions(questions)

    return {
        "message": "复习记录已保存",
        "review_id": review_id,
        "mastery_status": question["mastery_status"],
        "next_review_at": question["next_review_at"],
    }


@router.get("/wrong-questions/{question_id}/reviews", response_model=List[Dict[str, Any]])
async def get_question_reviews(request: Request, question_id: str):
    reviews = _load_question_reviews()
    return [r for r in reviews.values() if r["question_id"] == question_id]


@router.get("/wrong-question-stats", response_model=Dict[str, Any])
@router.get("/wrong-questions/stats", response_model=Dict[str, Any])
async def get_wrong_question_stats(request: Request):
    questions = _load_wrong_questions()

    total = len(questions)
    by_subject: Dict[str, int] = {}
    by_knowledge: Dict[str, int] = {}
    by_error_type: Dict[str, int] = {}
    by_mastery: Dict[str, int] = {}

    for q in questions.values():
        by_subject[q["subject"]] = by_subject.get(q["subject"], 0) + 1
        by_knowledge[q["knowledge_point"]] = by_knowledge.get(q["knowledge_point"], 0) + 1
        by_error_type[q["error_type"]] = by_error_type.get(q["error_type"], 0) + 1
        by_mastery[q["mastery_status"]] = by_mastery.get(q["mastery_status"], 0) + 1

    today = datetime.now(timezone.utc).date()
    need_review = sum(
        1 for q in questions.values()
        if q["next_review_at"] and _parse_datetime(q["next_review_at"]).date() <= today
    )

    return {
        "total": total,
        "by_subject": by_subject,
        "by_knowledge": by_knowledge,
        "by_error_type": by_error_type,
        "by_mastery": by_mastery,
        "need_review": need_review,
    }


@router.get("/wrong-questions-need-review", response_model=List[Dict[str, Any]])
@router.get("/wrong-questions/need-review", response_model=List[Dict[str, Any]])
async def get_questions_need_review(request: Request):
    questions = _load_wrong_questions()
    today = datetime.now(timezone.utc).date()

    result = [
        q for q in questions.values()
        if q["next_review_at"] and _parse_datetime(q["next_review_at"]).date() <= today
    ]

    result.sort(key=lambda q: q["next_review_at"])
    return result


# ---------------------------------------------------------------------------
# AI 分析：知识点识别 / 错误原因分析 / 生成解析
# ---------------------------------------------------------------------------

class AnalyzeQuestionResponse(BaseModel):
    knowledge_points: List[str] = Field(default_factory=list, description="识别出的知识点")
    error_reason: str = Field(default="", description="错误原因分析")
    solution: str = Field(default="", description="详细解题思路")
    related_concepts: List[str] = Field(default_factory=list, description="相关概念")
    difficulty_suggestion: str = Field(default="", description="难度建议")


@router.post("/wrong-questions/{question_id}/analyze", response_model=Dict[str, Any])
async def analyze_wrong_question(request: Request, question_id: str):
    """AI 分析错题：识别知识点、分析错误原因、生成解题思路。"""
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")

    q = questions[question_id]

    instructions = (
        "你是一位经验丰富的学科教师，擅长分析学生的错题并给出针对性指导。\n"
        "必须根据题目内容、学生答案和正确答案做具体分析，不能写泛泛而谈的套话。\n"
        "如果是编程题，solution 必须包含算法思路、关键步骤、复杂度或易错点。\n"
        "如果学生答案或正确答案明显缺失/无意义，也要结合题干指出无法判断的部分，并仍然给出题目本身的正确解法。\n"
        "请分析以下错题，并以 JSON 格式返回结果，包含以下字段：\n"
        "- knowledge_points: 知识点列表（数组）\n"
        "- error_reason: 错误原因分析（字符串，必须指出具体误区/遗漏/错误步骤）\n"
        "- solution: 详细解题思路（字符串，必须分步骤讲解，不少于 120 字）\n"
        "- related_concepts: 相关概念列表（数组）\n"
        "- difficulty_suggestion: 难度建议（easy/medium/hard）\n\n"
        "只返回 JSON，不要添加其他内容。JSON 字符串中的换行必须写成 \\n，双引号必须正确转义。"
    )

    user_input = (
        f"学科：{q['subject']}\n"
        f"知识点：{q['knowledge_point']}\n"
        f"题目内容：{q['question_content']}\n"
        f"学生答案：{q['user_answer']}\n"
        f"正确答案：{q['correct_answer']}\n"
        f"当前错误类型：{q['error_type']}\n"
        f"当前难度：{q['difficulty']}"
    )

    parsed: Dict[str, Any] = {}
    last_issues: List[str] = []
    attempt_input = user_input
    for attempt in range(2):
        raw = await asyncio.to_thread(_run_llm, instructions, attempt_input, 3072)
        try:
            parsed_raw = _parse_llm_json(raw)
            if not isinstance(parsed_raw, dict):
                raise ValueError("LLM response is not a JSON object")
            parsed = _clean_analysis(parsed_raw, q)
            break
        except (json.JSONDecodeError, ValueError) as exc:
            last_issues = [f"JSON 解析失败：{exc}"]

        if attempt == 1:
            parsed = _analysis_from_raw_llm_text(raw, q)
            break

        raw_excerpt = raw.strip()
        if len(raw_excerpt) > 4000:
            raw_excerpt = raw_excerpt[:4000] + "\n[内容已截断]"
        attempt_input = (
            f"{user_input}\n\n"
            f"上一次分析结果格式无法解析：{'；'.join(last_issues)}。\n"
            f"上一次模型返回如下：\n{raw_excerpt}\n\n"
            "请修正格式并重新生成 JSON。"
        )

    if not parsed:
        raise HTTPException(status_code=502, detail=f"AI 分析结果格式无法解析：{'；'.join(last_issues)}")

    # 将分析结果写回错题（更新 error_analysis 和 solution）
    if parsed.get("error_reason"):
        q["error_analysis"] = parsed["error_reason"]
    if parsed.get("solution"):
        q["solution"] = parsed["solution"]
    q["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_wrong_questions(questions)

    return {
        "question_id": question_id,
        "analysis": parsed,
    }


# ---------------------------------------------------------------------------
# 相似题推荐
# ---------------------------------------------------------------------------

class SimilarQuestionResponse(BaseModel):
    id: str
    original_question_id: str
    question_content: str
    correct_answer: str
    explanation: str = ""
    options: Optional[List[str]] = None
    created_at: str


@router.post("/wrong-questions/{question_id}/similar", response_model=Dict[str, Any])
async def generate_similar_question(request: Request, question_id: str):
    """基于错题生成相似练习题。"""
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")

    q = questions[question_id]

    instructions = (
        "你是一位出题专家。请根据给定的错题，生成一道真正的新练习题。\n"
        "要求：\n"
        "1. 考查相同知识点，但必须更换题目数据、情境或输入输出样例\n"
        "2. 不允许复制原题，不允许在题目中出现“参考原题”“请围绕”“重新完成一道同类型练习题”等模板话术\n"
        "3. 如果是编程题，question_content 必须是一道完整题面，包含输入输出示例或约束；correct_answer 必须是新题答案，不要返回 0 这种占位值\n"
        "4. explanation 必须说明新题的解法思路，不少于 60 字\n"
        "5. 以 JSON 格式返回，包含字段：\n"
        "   - question_content: 题目内容\n"
        "   - correct_answer: 正确答案\n"
        "   - explanation: 解题说明\n"
        "   - options: 选项列表（如果是选择题，否则为 null）\n\n"
        "只返回 JSON，不要添加其他内容。JSON 字符串中的换行必须写成 \\n，双引号必须正确转义。"
    )

    original_question_text = str(q.get("question_content") or "")
    user_input = (
        f"学科：{q['subject']}\n"
        f"知识点：{q['knowledge_point']}\n"
        f"原题内容（仅用于理解考点，不可复制到新题中）：{original_question_text}\n"
        f"正确答案：{q['correct_answer']}\n"
        f"难度：{q['difficulty']}\n"
        "请直接输出新题本身，不要输出生成任务说明。"
    )

    parsed: Dict[str, Any] = {}
    last_issues: List[str] = []
    attempt_input = user_input
    try:
        for attempt in range(2):
            raw = await asyncio.to_thread(_run_llm, instructions, attempt_input, 3072)
            try:
                parsed_raw = _parse_llm_json(raw)
                if not isinstance(parsed_raw, dict):
                    raise ValueError("LLM response is not a JSON object")
                parsed = _clean_similar_question(parsed_raw, q)
                break
            except (json.JSONDecodeError, ValueError) as exc:
                last_issues = [f"JSON 解析失败：{exc}"]

            if attempt == 1:
                parsed = _similar_from_raw_llm_text(raw)
                break

            raw_excerpt = raw.strip()
            if len(raw_excerpt) > 4000:
                raw_excerpt = raw_excerpt[:4000] + "\n[内容已截断]"
            attempt_input = (
                f"{user_input}\n\n"
                f"上一次相似题格式无法解析：{'；'.join(last_issues)}。\n"
                f"上一次模型返回如下：\n{raw_excerpt}\n\n"
                "请修正格式并重新生成 JSON。"
            )
    except HTTPException:
        raise

    if not parsed:
        raise HTTPException(status_code=502, detail=f"AI 相似题生成结果格式无法解析：{'；'.join(last_issues)}")

    similar_id = _generate_id()
    now_iso = datetime.now(timezone.utc).isoformat()
    similar_record: Dict[str, Any] = {
        "id": similar_id,
        "original_question_id": question_id,
        "question_content": parsed.get("question_content", ""),
        "correct_answer": parsed.get("correct_answer", ""),
        "explanation": parsed.get("explanation", ""),
        "options": parsed.get("options"),
        "created_at": now_iso,
    }

    similar_store = _load_similar_questions()
    similar_store[similar_id] = similar_record
    _save_similar_questions(similar_store)

    return similar_record


@router.get("/wrong-questions/{question_id}/similar", response_model=List[Dict[str, Any]])
async def get_similar_questions(request: Request, question_id: str):
    """获取某道错题的所有相似练习题。"""
    similar = _load_similar_questions()
    result = [s for s in similar.values() if s["original_question_id"] == question_id]
    result.sort(key=lambda s: s["created_at"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# 分析研究报告
# ---------------------------------------------------------------------------

class ReportResponse(BaseModel):
    summary: str = Field(default="", description="总体概况")
    weak_points: List[str] = Field(default_factory=list, description="薄弱知识点")
    error_patterns: List[str] = Field(default_factory=list, description="错误模式")
    suggestions: List[str] = Field(default_factory=list, description="改进建议")
    review_plan: str = Field(default="", description="复习计划建议")


@router.get("/wrong-question-report", response_model=Dict[str, Any])
@router.get("/wrong-questions/report", response_model=Dict[str, Any])
async def get_wrong_question_report(
    request: Request,
    subject: Optional[str] = None,
):
    """生成错题分析研究报告。

    可选 subject 参数按学科过滤。报告包含总体概况、薄弱知识点、
    错误模式、改进建议和复习计划。
    """
    questions = _load_wrong_questions()
    q_list = list(questions.values())

    if subject:
        q_list = [q for q in q_list if q["subject"] == subject]

    if not q_list:
        return {
            "subject": subject,
            "total_questions": 0,
            "total_reviews": 0,
            "accuracy": 0.0,
            "by_knowledge": {},
            "by_error_type": {},
            "by_mastery": {},
            "report": {
                "summary": "暂无错题数据。添加错题后，这里会生成统计概览、薄弱知识点和复习建议。",
                "weak_points": [],
                "error_patterns": [],
                "suggestions": ["先添加错题，再按学科和知识点持续复盘。"],
                "review_plan": "",
            },
        }

    # 先生成统计摘要（纯统计部分，不需要 LLM）
    by_knowledge: Dict[str, int] = {}
    by_error_type: Dict[str, int] = {}
    by_mastery: Dict[str, int] = {}
    total_reviews = 0
    total_correct = 0

    for q in q_list:
        by_knowledge[q["knowledge_point"]] = by_knowledge.get(q["knowledge_point"], 0) + 1
        by_error_type[q["error_type"]] = by_error_type.get(q["error_type"], 0) + 1
        by_mastery[q["mastery_status"]] = by_mastery.get(q["mastery_status"], 0) + 1
        total_reviews += q.get("review_count", 0)
        total_correct += q.get("correct_count", 0)

    weak_knowledge = sorted(by_knowledge.items(), key=lambda x: x[1], reverse=True)[:5]
    frequent_errors = sorted(by_error_type.items(), key=lambda x: x[1], reverse=True)

    accuracy = round((total_correct / total_reviews * 100), 1) if total_reviews > 0 else 0.0

    # 调用 LLM 生成深度分析报告
    instructions = (
        "你是一位学习分析师，请根据学生的错题统计数据生成一份分析研究报告。\n"
        "以 JSON 格式返回，包含以下字段：\n"
        "- summary: 总体概况（字符串，2-3 段，分析学习现状）\n"
        "- weak_points: 薄弱知识点列表（数组，指出最需要加强的知识点）\n"
        "- error_patterns: 错误模式分析（数组，总结反复出现的错误类型）\n"
        "- suggestions: 改进建议（数组，给出具体可执行的学习建议）\n"
        "- review_plan: 复习计划建议（字符串，给出未来一周的复习安排建议）\n\n"
        "只返回 JSON，不要添加其他内容。"
    )

    user_input = (
        f"学科范围：{subject or '全部'}\n"
        f"错题总数：{len(q_list)}\n"
        f"总复习次数：{total_reviews}\n"
        f"复习正确率：{accuracy}%\n\n"
        f"知识点错题分布（前5）：{dict(weak_knowledge)}\n"
        f"错误类型分布：{dict(frequent_errors)}\n"
        f"掌握状态分布：{by_mastery}\n\n"
        f"错题列表（前10题摘要）：\n"
    )
    for idx, q in enumerate(q_list[:10], 1):
        user_input += (
            f"{idx}. [{q['subject']}] {q['knowledge_point']} - "
            f"{q['question_content'][:60]}... "
            f"(错误类型: {q['error_type']}, 掌握: {q['mastery_status']})\n"
        )

    fallback_report: Dict[str, Any] = {
        "summary": (
            f"当前共有 {len(q_list)} 道错题，累计复习 {total_reviews} 次，复习正确率为 {accuracy}%。"
            "建议优先处理出现频率最高的知识点，并结合待复习题目安排短周期复盘。"
        ),
        "weak_points": [k for k, _ in weak_knowledge],
        "error_patterns": [k for k, _ in frequent_errors],
        "suggestions": [
            "优先复习错题数量最多的知识点。",
            "每次复习后记录错因，区分概念、审题、计算和策略问题。",
            "对已掌握题目拉长复习间隔，对薄弱题目保持高频回看。",
        ],
        "review_plan": "本周先集中复习高频错题知识点，再穿插相似题训练，最后用待复习列表做查漏补缺。",
    }

    try:
        raw = await asyncio.to_thread(_run_llm, instructions, user_input, 3072)
        parsed_raw = _parse_llm_json(raw)
        if not isinstance(parsed_raw, dict):
            raise ValueError("LLM response is not a JSON object")
        parsed = {
            "summary": str(parsed_raw.get("summary") or fallback_report["summary"]),
            "weak_points": parsed_raw.get("weak_points") if isinstance(parsed_raw.get("weak_points"), list) else fallback_report["weak_points"],
            "error_patterns": parsed_raw.get("error_patterns") if isinstance(parsed_raw.get("error_patterns"), list) else fallback_report["error_patterns"],
            "suggestions": parsed_raw.get("suggestions") if isinstance(parsed_raw.get("suggestions"), list) else fallback_report["suggestions"],
            "review_plan": str(parsed_raw.get("review_plan") or fallback_report["review_plan"]),
        }
    except (HTTPException, json.JSONDecodeError, ValueError):
        parsed = fallback_report

    return {
        "subject": subject,
        "total_questions": len(q_list),
        "total_reviews": total_reviews,
        "accuracy": accuracy,
        "by_knowledge": by_knowledge,
        "by_error_type": by_error_type,
        "by_mastery": by_mastery,
        "report": parsed,
    }


# ---------------------------------------------------------------------------
# 文件上传解析 + AI 自动提取
# ---------------------------------------------------------------------------

class AiExtractResult(BaseModel):
    subject: str = Field(default="", description="学科")
    knowledge_point: str = Field(default="", description="知识点")
    question_content: str = Field(default="", description="题目内容")
    user_answer: str = Field(default="", description="用户答案")
    correct_answer: str = Field(default="", description="正确答案")
    error_type: str = Field(default="other", description="错误类型")
    difficulty: str = Field(default="medium", description="难度")
    error_analysis: str = Field(default="", description="错误分析")
    solution: str = Field(default="", description="解题思路")


_EXTRACT_INSTRUCTIONS = (
    "你是一位学科教师，请从以下题目文本中提取结构化信息。\n"
    "以 JSON 格式返回，包含以下字段：\n"
    "- subject: 学科或课程名称（根据题目真实课程识别，例如：数据结构、线性代数、数字电路、自动控制原理、数据库系统；不要限制在中小学学科内）\n"
    "- knowledge_point: 知识点（一句话描述）\n"
    "- question_content: 题目完整内容\n"
    "- user_answer: 用户的答案（如果文本中有则填，没有则空字符串）\n"
    "- correct_answer: 正确答案（如果文本中有则填，没有则空字符串）\n"
    "- error_type: 错误类型（从以下选择：concept_misunderstanding/calculation_error/misreading/careless/strategy_error/other）\n"
    "- difficulty: 难度（easy/medium/hard）\n"
    "- error_analysis: 错误原因分析（如果没有用户答案则留空）\n"
    "- solution: 解题思路\n\n"
    "只返回 JSON，不要添加其他内容。"
)


_EXTRACT_INSTRUCTIONS += (
    "\nAlways infer subject and knowledge_point from the question itself. "
    "Do not leave subject or knowledge_point empty when question_content is present. "
    "Return valid JSON only."
)


_LLM_FIELD_ALIASES: Dict[str, List[str]] = {
    "knowledge_points": ["knowledge_points", "knowledge point", "knowledge points", "knowledgepoints", "知识点", "识别知识点"],
    "error_reason": ["error_reason", "error reason", "error analysis", "错误原因", "错因分析", "错误分析"],
    "solution": ["solution", "detail_solution", "detailed_solution", "detail solution", "detail", "解题思路", "详细解析", "详细解答", "解析"],
    "related_concepts": ["related_concepts", "relatedrelated_concepts", "related concepts", "relatedconcepts", "相关概念"],
    "difficulty_suggestion": ["difficulty_suggestion", "difficulty suggestion", "难度建议"],
    "question_content": ["question_content", "question content", "题目内容", "题干", "新题"],
    "correct_answer": ["correct_answer", "correct answer", "正确答案", "答案"],
    "explanation": ["explanation", "解析说明", "解题说明"],
    "options": ["options", "选项"],
    "subject": ["subject", "学科"],
    "knowledge_point": ["knowledge_point", "knowledge point", "知识点"],
    "user_answer": ["user_answer", "user answer", "我的答案", "学生答案"],
    "error_type": ["error_type", "error type", "错误类型"],
    "difficulty": ["difficulty", "难度"],
    "error_analysis": ["error_analysis", "error analysis", "错误分析"],
    "summary": ["summary", "总结", "总体概况"],
    "weak_points": ["weak_points", "weak points", "薄弱知识点"],
    "error_patterns": ["error_patterns", "error patterns", "错误模式"],
    "suggestions": ["suggestions", "建议", "改进建议"],
    "review_plan": ["review_plan", "review plan", "复习计划"],
}

_LIST_LLM_FIELDS = {
    "knowledge_points",
    "related_concepts",
    "options",
    "weak_points",
    "error_patterns",
    "suggestions",
}


def _canonical_llm_field_name(name: Any) -> str:
    normalized = re.sub(r"[\s_\-]+", "", str(name or "").strip().lower())
    for canonical, aliases in _LLM_FIELD_ALIASES.items():
        if normalized == re.sub(r"[\s_\-]+", "", canonical.lower()):
            return canonical
        for alias in aliases:
            if normalized == re.sub(r"[\s_\-]+", "", alias.lower()):
                return canonical
    return str(name or "")


def _normalize_llm_field_names(parsed: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in parsed.items():
        canonical = _canonical_llm_field_name(key)
        normalized.setdefault(canonical, value)
    return normalized


def _strip_llm_container(raw: str) -> str:
    cleaned = raw.strip().lstrip("\ufeff")
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline >= 0:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3].rstrip()
    return cleaned.strip()


def _try_parse_json_object(cleaned: str) -> Dict[str, Any]:
    decoder = json.JSONDecoder(strict=False)
    starts = [0]
    starts.extend(index for index, char in enumerate(cleaned) if char in "[{" and index != 0)
    last_error: Optional[Exception] = None

    for start in starts:
        candidate = cleaned[start:].lstrip()
        if not candidate:
            continue
        try:
            parsed, _end = decoder.raw_decode(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

        if isinstance(parsed, dict):
            return _normalize_llm_field_names(parsed)
        if isinstance(parsed, list):
            dict_items = [item for item in parsed if isinstance(item, dict)]
            if len(dict_items) == 1:
                return _normalize_llm_field_names(dict_items[0])
            if dict_items:
                return {"items": [_normalize_llm_field_names(item) for item in dict_items]}
        last_error = ValueError("LLM response is not a JSON object")

    if last_error is not None:
        raise last_error
    raise ValueError("LLM response does not contain a JSON object")


def _decode_loose_string(value: str) -> str:
    text = value.strip()
    while text.endswith(","):
        text = text[:-1].rstrip()
    if text in {"null", "None", "NONE"}:
        return ""
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        text = text[1:-1]
    elif text.startswith(("\"", "'")):
        text = text[1:]
    if "\\" not in text:
        return _clean_llm_text(text)
    if re.search(r"\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}", text):
        text = _decode_unicode_escape_sequences(text)
    try:
        return _clean_llm_text(
            text.replace(r"\"", '"')
            .replace(r"\n", "\n")
            .replace(r"\r", "\n")
            .replace(r"\t", "\t")
            .replace(r"\/", "/")
            .replace(r"\\", "\\")
        )
    except Exception:
        return _clean_llm_text(text)


def _decode_unicode_escape_sequences(text: str) -> str:
    def replace_short(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    def replace_long(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    text = re.sub(r"\\u([0-9a-fA-F]{4})", replace_short, text)
    return re.sub(r"\\U([0-9a-fA-F]{8})", replace_long, text)


def _clean_llm_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\ufffd", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = _trim_repetitive_llm_noise(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+([,，。；;：:])", r"\1", text)
    text = re.sub(r"([,，；;：:])\s+", r"\1", text)
    text = text.strip(" \t\r\n,，")
    return text


def _trim_repetitive_llm_noise(text: str) -> str:
    if not text:
        return ""

    repeated_match = re.search(r"([^\sA-Za-z0-9])(?:\s*\1){15,}", text)
    if repeated_match:
        prefix = text[: repeated_match.start()].rstrip(" \t\r\n,，。；;：:\"'“”")
        suffix = text[repeated_match.end():]
        if len(prefix) >= 20:
            return prefix
        text = (prefix + "\n" + suffix).strip()

    cleaned_lines: List[str] = []
    for line in text.splitlines():
        compact = re.sub(r"\s+", "", line)
        if len(compact) >= 80:
            counts: Dict[str, int] = {}
            for char in compact:
                counts[char] = counts.get(char, 0) + 1
            dominant = max(counts.values()) if counts else 0
            if dominant / len(compact) >= 0.45:
                continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _split_loose_list(value: str) -> List[str]:
    text = value.strip().rstrip(",")
    if not text or text in {"null", "None", "NONE"}:
        return []
    try:
        parsed = json.loads(text, strict=False)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass

    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    pieces = re.split(r"[,，;；\n]+", text)
    return [_decode_loose_string(piece).strip(" -") for piece in pieces if _decode_loose_string(piece).strip(" -")]


def _coerce_loose_value(field: str, value: str) -> Any:
    text = value.strip()
    while text.endswith("}") and text.count("{") < text.count("}"):
        text = text[:-1].rstrip()
    while text.endswith(","):
        text = text[:-1].rstrip()

    if field in _LIST_LLM_FIELDS:
        if text in {"null", "None", "NONE"}:
            return None if field == "options" else []
        return _split_loose_list(text)
    if text.startswith("[") and text.endswith("]"):
        values = _split_loose_list(text)
        return values
    return _decode_loose_string(text)


def _parse_loose_llm_object(cleaned: str) -> Dict[str, Any]:
    aliases: List[tuple[str, str]] = []
    for canonical, names in _LLM_FIELD_ALIASES.items():
        for name in names:
            aliases.append((canonical, name))
    aliases.sort(key=lambda item: len(item[1]), reverse=True)

    matches: List[tuple[int, int, str]] = []
    for canonical, alias in aliases:
        pattern = re.compile(
            rf"(?im)(?:^|[{{,\n]\s*)[\"'`]?{re.escape(alias)}[\"'`]?\s*[:：]\s*"
        )
        for match in pattern.finditer(cleaned):
            matches.append((match.start(), match.end(), canonical))

    if not matches:
        raise ValueError("LLM response does not contain recognizable fields")

    matches.sort(key=lambda item: item[0])
    parsed: Dict[str, Any] = {}
    for index, (_match_start, value_start, field) in enumerate(matches):
        value_end = matches[index + 1][0] if index + 1 < len(matches) else len(cleaned)
        raw_value = cleaned[value_start:value_end].strip()
        if not raw_value:
            continue
        parsed.setdefault(field, _coerce_loose_value(field, raw_value))

    if not parsed:
        raise ValueError("LLM response fields are empty")
    return parsed


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    """Parse JSON-like model output while preserving real LLM content."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("LLM returned an empty response")

    cleaned = _strip_llm_container(raw)
    try:
        return _try_parse_json_object(cleaned)
    except (json.JSONDecodeError, ValueError) as strict_error:
        try:
            return _parse_loose_llm_object(cleaned)
        except ValueError:
            raise strict_error


def _infer_subject_and_knowledge(text: str) -> tuple[str, str]:
    normalized = (text or "").lower()
    original = text or ""

    programming_points = [
        ("二叉树", ["二叉树", "binary tree", "root =", "treenode", "翻转二叉树", "树的翻转"]),
        ("链表", ["链表", "linked list", "listnode"]),
        ("数组", ["数组", "array", "nums", "下标"]),
        ("字符串", ["字符串", "string", "子串"]),
        ("动态规划", ["动态规划", "dp", "状态转移"]),
        ("栈和队列", ["栈", "队列", "stack", "queue"]),
        ("哈希表", ["哈希", "hash", "map", "字典"]),
        ("递归", ["递归", "recursion"]),
        ("算法", ["leetcode", "输入：", "输出：", "示例", "class solution", "def ", "return "]),
    ]

    for point, keywords in programming_points:
        if any(keyword.lower() in normalized or keyword in original for keyword in keywords):
            return "编程", point

    subject_keywords = [
        ("数学", ["函数", "方程", "几何", "概率", "导数", "积分", "数列"]),
        ("物理", ["速度", "加速度", "电路", "电压", "电流", "力学", "功率"]),
        ("化学", ["化学式", "反应", "溶液", "物质的量", "离子"]),
        ("英语", ["grammar", "reading", "choose the best", "单词", "语法"]),
        ("语文", ["阅读理解", "文言文", "古诗", "作文", "修辞"]),
    ]

    for subject, keywords in subject_keywords:
        if any(keyword.lower() in normalized or keyword in original for keyword in keywords):
            return subject, keywords[0]

    return "其他", ""


def _normalize_subject(value: str, source_text: str) -> str:
    subject = (value or "").strip()
    aliases = {
        "coding": "编程",
        "programming": "编程",
        "math": "数学",
        "mathematics": "数学",
    }
    subject = aliases.get(subject.lower(), aliases.get(subject, subject))
    if subject:
        return subject
    inferred, _knowledge = _infer_subject_and_knowledge(source_text)
    return inferred


def _is_unparsed_ai_text(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if "AI 返回内容无法解析" in text or "AI returned content" in text:
        return True
    return text.startswith("{") and (
        '"knowledge_points"' in text or '"error_reason"' in text or '"solution"' in text
    )


def _normalize_compare_text(value: str) -> str:
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())


def _listify_text(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _analysis_quality_issues(analysis: Dict[str, Any], question: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    joined = "\n".join(
        [
            str(analysis.get("error_reason") or ""),
            str(analysis.get("solution") or ""),
            " ".join(str(item) for item in analysis.get("knowledge_points") or []),
        ]
    )
    knowledge = str(question.get("knowledge_point") or "").strip()

    if not analysis.get("knowledge_points"):
        issues.append("knowledge_points 不能为空")
    if len(str(analysis.get("error_reason") or "").strip()) < 40:
        issues.append("error_reason 过短，必须具体说明学生错在哪里")
    if len(str(analysis.get("solution") or "").strip()) < 120:
        issues.append("solution 过短，必须给出可执行的分步解析")
    if _is_unparsed_ai_text(str(analysis.get("solution") or "")):
        issues.append("solution 仍然是未解析的 JSON 或错误文本")
    if knowledge and knowledge not in joined:
        issues.append(f"分析内容没有围绕知识点「{knowledge}」展开")

    generic_phrases = [
        "当前答案与正确答案不一致",
        "建议重点核对题意",
        "你的答案与正确答案存在差异",
        "把握不够准确",
        "AI 返回内容无法解析",
    ]
    if any(phrase in joined for phrase in generic_phrases):
        issues.append("分析内容仍然是通用套话")

    return issues


def _similar_quality_issues(similar: Dict[str, Any], question: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    generated = str(similar.get("question_content") or "").strip()
    original = str(question.get("question_content") or "").strip()
    explanation = str(similar.get("explanation") or "").strip()
    correct_answer = str(similar.get("correct_answer") or "").strip()

    if len(generated) < 80:
        issues.append("question_content 过短，必须是一道完整新题")
    if not correct_answer:
        issues.append("correct_answer 不能为空")
    if len(explanation) < 60:
        issues.append("explanation 过短，必须说明解题思路")

    normalized_generated = _normalize_compare_text(generated)
    normalized_original = _normalize_compare_text(original)
    if normalized_original and (
        normalized_generated == normalized_original
        or normalized_original[:80] in normalized_generated
    ):
        issues.append("相似题复制了原题内容")

    forbidden_phrases = [
        "参考原题",
        "请围绕",
        "重新完成一道",
        "同类型练习题",
        "更换具体数据或情境",
        "保持考查点一致",
    ]
    if any(phrase in generated for phrase in forbidden_phrases):
        issues.append("相似题仍然是模板说明，不是真正的新题")

    return issues


def _fallback_analysis(question: Dict[str, Any]) -> Dict[str, Any]:
    text = str(question.get("question_content") or "")
    inferred_subject, inferred_knowledge = _infer_subject_and_knowledge(text)
    knowledge = str(question.get("knowledge_point") or inferred_knowledge or "").strip()
    subject = str(question.get("subject") or inferred_subject or "其他").strip()
    correct_answer = str(question.get("correct_answer") or "").strip()
    user_answer = str(question.get("user_answer") or "").strip()

    reason = (
        f"这道题主要考查{knowledge or subject}。当前答案与正确答案不一致，建议重点核对题意、关键概念和作答步骤。"
    )
    if user_answer and correct_answer:
        reason = f"你的答案与正确答案存在差异，说明对{knowledge or subject}的关键要求把握不够准确。"

    solution = str(question.get("solution") or "").strip()
    if _is_unparsed_ai_text(solution):
        solution = ""
    if not solution:
        solution = (
            f"先明确题目考查的核心知识点：{knowledge or subject}；再对照正确答案"
            f"{f'（{correct_answer}）' if correct_answer else ''}梳理每一步依据，最后总结容易误读或漏掉的条件。"
        )

    return {
        "knowledge_points": [knowledge] if knowledge else [],
        "error_reason": reason,
        "solution": solution,
        "related_concepts": [knowledge] if knowledge else [],
        "difficulty_suggestion": str(question.get("difficulty") or "medium"),
    }


def _fallback_similar_question(question: Dict[str, Any]) -> Dict[str, Any]:
    text = str(question.get("question_content") or "").strip()
    knowledge = str(question.get("knowledge_point") or "").strip()
    subject = str(question.get("subject") or "").strip()
    correct = str(question.get("correct_answer") or "").strip()

    return {
        "question_content": (
            f"请围绕「{knowledge or subject or '同一知识点'}」重新完成一道同类型练习题：\n"
            f"参考原题：{text[:300]}\n"
            "要求：更换具体数据或情境，保持考查点一致，并写出完整作答过程。"
        ),
        "correct_answer": correct or "请根据新题条件推导正确答案。",
        "explanation": f"这道练习题用于巩固{knowledge or subject or '原题'}，重点训练识别题意和应用核心方法。",
        "options": None,
    }


def _raw_llm_display_text(raw: str) -> str:
    text = _clean_llm_text(_strip_llm_container(raw or ""))
    if len(text) > 20000:
        text = text[:20000] + "\n[AI 返回内容过长，已截断]"
    return text


def _analysis_from_raw_llm_text(raw: str, question: Dict[str, Any]) -> Dict[str, Any]:
    text = _raw_llm_display_text(raw)
    if not text:
        return {}
    knowledge = str(question.get("knowledge_point") or "").strip()
    return {
        "knowledge_points": [knowledge] if knowledge else [],
        "error_reason": text,
        "solution": text,
        "related_concepts": [],
        "difficulty_suggestion": str(question.get("difficulty") or "medium").strip(),
    }


def _similar_from_raw_llm_text(raw: str) -> Dict[str, Any]:
    text = _raw_llm_display_text(raw)
    if not text:
        return {}
    return {
        "question_content": text,
        "correct_answer": "",
        "explanation": "",
        "options": None,
    }


def _clean_analysis(parsed: Dict[str, Any], question: Dict[str, Any]) -> Dict[str, Any]:
    knowledge_points = [_clean_llm_text(item) for item in _listify_text(parsed.get("knowledge_points"))]
    knowledge_points = [item for item in knowledge_points if item]
    if not knowledge_points and question.get("knowledge_point"):
        knowledge_points = [_clean_llm_text(question.get("knowledge_point"))]

    related_concepts = [_clean_llm_text(item) for item in _listify_text(parsed.get("related_concepts"))]
    related_concepts = [item for item in related_concepts if item]

    error_reason = _clean_llm_text(parsed.get("error_reason"))
    solution = _clean_llm_text(parsed.get("solution"))
    if _is_unparsed_ai_text(solution):
        solution = ""

    return {
        "knowledge_points": knowledge_points,
        "error_reason": error_reason,
        "solution": solution,
        "related_concepts": related_concepts,
        "difficulty_suggestion": _clean_llm_text(parsed.get("difficulty_suggestion") or question.get("difficulty") or "medium"),
    }


def _clean_similar_question(parsed: Dict[str, Any], question: Dict[str, Any]) -> Dict[str, Any]:
    options = parsed.get("options")
    if isinstance(options, list):
        options = [_clean_llm_text(item) for item in options if _clean_llm_text(item)]
    else:
        options = None

    return {
        "question_content": _clean_llm_text(parsed.get("question_content")),
        "correct_answer": _clean_llm_text(parsed.get("correct_answer")),
        "explanation": _clean_llm_text(parsed.get("explanation")),
        "options": options,
    }


def _clean_extracted_question(parsed: Dict[str, Any], fallback_text: str = "") -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {
        "subject": str(parsed.get("subject") or "").strip(),
        "knowledge_point": str(parsed.get("knowledge_point") or "").strip(),
        "question_content": str(parsed.get("question_content") or "").strip(),
        "user_answer": str(parsed.get("user_answer") or "").strip(),
        "correct_answer": str(parsed.get("correct_answer") or "").strip(),
        "error_type": str(parsed.get("error_type") or "other").strip() or "other",
        "difficulty": str(parsed.get("difficulty") or "medium").strip() or "medium",
        "error_analysis": str(parsed.get("error_analysis") or "").strip(),
        "solution": str(parsed.get("solution") or "").strip(),
    }

    if not cleaned["question_content"] and fallback_text:
        cleaned["question_content"] = fallback_text.strip()[:500]

    source_text = "\n".join(
        part for part in [cleaned["question_content"], fallback_text] if part
    )
    inferred_subject, inferred_knowledge = _infer_subject_and_knowledge(source_text)
    cleaned["subject"] = _normalize_subject(cleaned["subject"], source_text) or inferred_subject

    if inferred_knowledge and (
        not cleaned["knowledge_point"] or cleaned["knowledge_point"] in {"算法", "数据结构", "题目理解", "其他"}
    ):
        cleaned["knowledge_point"] = inferred_knowledge

    if cleaned["error_type"] not in {
        "concept_misunderstanding",
        "calculation_error",
        "misreading",
        "careless",
        "strategy_error",
        "other",
    }:
        cleaned["error_type"] = "other"

    if cleaned["difficulty"] not in {"easy", "medium", "hard"}:
        cleaned["difficulty"] = "medium"

    return cleaned


@router.post("/wrong-questions/extract", response_model=Dict[str, Any])
async def ai_extract_question(request: Request):
    """从粘贴的文本中 AI 提取结构化错题信息，用于自动填充表单。"""
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本内容不能为空")

    raw = await asyncio.to_thread(_run_llm, _EXTRACT_INSTRUCTIONS, text, 2048)

    try:
        parsed = _clean_extracted_question(_parse_llm_json(raw), text)
    except (json.JSONDecodeError, ValueError):
        parsed = _clean_extracted_question({}, text)

    return {"raw_text": text, "extracted": parsed}


@router.post("/wrong-questions/upload", response_model=Dict[str, Any])
async def upload_question_file(request: Request):
    """上传图片/PDF/Word 文件（base64 编码），解析后 AI 自动提取结构化错题信息。

    请求体 JSON: { file_data: "base64...", filename: "xxx.pdf", content_type: "application/pdf", field_type: "question" }
    field_type 可选值: question(题目), correct_answer(正确答案), user_answer(错误答案)
    返回 { raw_text, extracted, field_type, file_path }，前端用 extracted 自动填充表单。
    """
    import base64

    body = await request.json()
    file_data = body.get("file_data") or ""
    filename = body.get("filename") or ""
    content_type = body.get("content_type") or ""
    field_type = body.get("field_type") or "question"

    if not file_data:
        raise HTTPException(status_code=400, detail="文件数据为空")

    try:
        file_bytes = base64.b64decode(file_data)
    except Exception:
        raise HTTPException(status_code=400, detail="文件 base64 解码失败")

    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空")

    # 保存文件到本地，返回相对路径供前端存储
    temp_id = _generate_id()
    file_path = _save_file(file_data, filename, temp_id, field_type)

    raw_text = ""

    # 图片：使用 LLM vision 识别
    if content_type.startswith("image/") or filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
        mime = content_type or "image/png"
        image_b64 = base64.b64encode(file_bytes).decode("utf-8")

        if field_type == "question":
            prompt = (
                "请识别图片中的题目内容，提取所有文字信息，包括题目、选项、答案等。\n"
                "请完整输出识别到的文字内容，保持原始格式。"
            )
        elif field_type == "correct_answer":
            prompt = "请识别图片中的正确答案内容，提取所有文字信息。"
        else:
            prompt = "请识别图片中的答案内容，提取所有文字信息。"

        raw_text = await asyncio.to_thread(_run_vision_llm, image_b64, mime, prompt, 2048)

    # PDF
    elif content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        raw_text = _parse_pdf(file_bytes)

    # Word
    elif content_type in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",) or filename.lower().endswith(".docx"):
        raw_text = _parse_docx(file_bytes)

    # 纯文本
    elif content_type.startswith("text/") or filename.lower().endswith(".txt"):
        raw_text = file_bytes.decode("utf-8", errors="replace")

    else:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {content_type or filename}")

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="文件解析结果为空，请确认文件内容或手动录入")

    # AI 结构化提取（只有题目需要完整提取，答案只提取文本）
    if field_type == "question":
        raw = await asyncio.to_thread(_run_llm, _EXTRACT_INSTRUCTIONS, raw_text, 2048)

        try:
            parsed = _clean_extracted_question(_parse_llm_json(raw), raw_text)
        except (json.JSONDecodeError, ValueError):
            parsed = _clean_extracted_question({}, raw_text)
    else:
        parsed = {
            "subject": "",
            "knowledge_point": "",
            "question_content": "",
            "user_answer": raw_text if field_type == "user_answer" else "",
            "correct_answer": raw_text if field_type == "correct_answer" else "",
            "error_type": "other",
            "difficulty": "medium",
            "error_analysis": "",
            "solution": "",
        }

    return {"raw_text": raw_text, "extracted": parsed, "field_type": field_type, "file_path": file_path}


@router.get("/wrong-questions/files/{file_name}/data-url", response_model=Dict[str, Any])
async def get_question_file_data_url(request: Request, file_name: str):
    """Return a wrong-question attachment as a JSON data URL for Desktop."""
    file_path = _FILES_DIR / file_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    data = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return {
        "file_name": file_path.name,
        "mime_type": mime_type,
        "data_url": f"data:{mime_type};base64,{data}",
    }


@router.get("/wrong-questions/files/{file_name}")
async def download_question_file(request: Request, file_name: str):
    """下载错题关联的原始文件。"""
    file_path = _FILES_DIR / file_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    from fastapi.responses import FileResponse

    return FileResponse(file_path, filename=file_name)
