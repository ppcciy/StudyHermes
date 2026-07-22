import json
import time
from datetime import datetime, timezone

import pytest

from hermes_cli import learning_api as api


def _complete_profile():
    return {
        "baseline_level": "鍏ラ棬",
        "career_target": "瀹炰範",
        "constraints": "",
        "current_courses": [],
        "grade": "澶т笁",
        "learning_preferences": ["椤圭洰瀹炴垬"],
        "major": "璁＄畻鏈虹瀛︿笌鎶€鏈?",
        "nickname": "",
        "preferred_study_time": "鏅氫笂",
        "school": "",
        "semester": "",
        "strengths": "",
        "updated_at": "2026-07-21T00:00:00+00:00",
        "weaknesses": "",
        "weekday_minutes": 60,
        "weekend_minutes": 180,
    }


def _complete_draft(**overrides):
    draft = {
        "current_level": "鍏ラ棬",
        "daily_minutes": 60,
        "goal_name": "Java 鍚庣鎵惧疄涔?",
        "goal_type": "skill",
        "preference": "椤圭洰瀹炴垬",
        "purpose": "鎵惧疄涔?",
        "tags": ["Java 鍚庣"],
        "target_level": "杈惧埌闈㈣瘯/灏变笟姘村钩",
        "timeframe": "6涓湀",
        "timeframe_days": 180,
    }
    draft.update(overrides)
    return draft


def _existing_goal(**overrides):
    goal = {
        "created_at": "2026-07-21T00:00:00+00:00",
        "description": "鐩爣锛氬涔?Java 鍚庣",
        "estimated_hours": 100,
        "goal_type": "skill",
        "id": "goal-java",
        "name": "瀛︿範 Java 鍚庣",
        "prerequisites": [],
        "progress": 0,
        "status": "active",
        "tags": ["Java 鍚庣"],
        "target_date": None,
        "updated_at": "2026-07-21T00:00:00+00:00",
    }
    goal.update(overrides)
    return goal


def _existing_plan(**overrides):
    plan = {
        "completed_tasks": 0,
        "created_at": "2026-07-21T00:00:00+00:00",
        "description": "瀹屾垚鍩虹璇婃柇鍜岃矾绾挎⒊鐞?",
        "goal_id": "goal-java",
        "id": "plan-1",
        "name": "璇婃柇涓庤矾绾?",
        "total_tasks": 1,
        "updated_at": "2026-07-21T00:00:00+00:00",
    }
    plan.update(overrides)
    return plan


def _existing_task(**overrides):
    task = {
        "completed_at": None,
        "created_at": "2026-07-21T00:00:00+00:00",
        "description": "姊崇悊褰撳墠鎺屾彙鍐呭鍜岃杽寮辩偣",
        "difficulty": "medium",
        "estimated_minutes": 45,
        "goal_id": "goal-java",
        "id": "task-1",
        "plan_id": "plan-1",
        "position": 0,
        "priority": 1,
        "scheduled_date": "2026-07-21",
        "status": "pending",
        "title": "鍩虹璇婃柇娴嬭瘯",
        "updated_at": "2026-07-21T00:00:00+00:00",
    }
    task.update(overrides)
    return task


def _plan_response(
    *,
    estimated_hours=48,
    goal_name="楂樼瓑鏁板寮哄寲",
    task_title="鍑芥暟鏋侀檺璇婃柇",
):
    return {
        "goal": {
            "description": f"{goal_name} 鐨勪釜鎬у寲瀛︿範璁″垝",
            "estimated_hours": estimated_hours,
            "goal_type": "knowledge",
            "name": goal_name,
            "prerequisites": ["鍩虹姒傚康"],
            "tags": [goal_name],
        },
        "plans": [
            {
                "description": "鍏堝畾浣嶈杽寮辩偣锛屽啀杩涘叆閲嶇偣璁粌",
                "name": "璇婃柇涓庡熀纭€閲嶅缓",
                "tasks": [
                    {
                        "description": "缁撳悎褰撳墠鍩虹瀹屾垚涓€娆¤杽寮辩偣瀹氫綅",
                        "difficulty": "medium",
                        "estimated_minutes": 60,
                        "priority": 1,
                        "title": task_title,
                    }
                ],
            }
        ],
    }


@pytest.fixture()
def learning_home(tmp_path, monkeypatch):
    api._COURSE_PLAN_INFLIGHT.clear()
    api._TASK_GUIDANCE_INFLIGHT.clear()
    api._TODAY_TIMETABLE_INFLIGHT.clear()
    monkeypatch.setattr(api, "_LEARNING_DATA_DIR", tmp_path)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "profile.json").write_text(json.dumps(_complete_profile()), encoding="utf-8")
    yield tmp_path
    api._COURSE_PLAN_INFLIGHT.clear()
    api._TASK_GUIDANCE_INFLIGHT.clear()
    api._TODAY_TIMETABLE_INFLIGHT.clear()


async def _finalize_until_ready(draft, *, session_id=None, force_retry=False):
    result = await api.finalize_learning_requirement(
        None,
        api.FinalizeLearningRequirementRequest(
            draft=draft,
            force_retry=force_retry,
            session_id=session_id,
        ),
    )
    current_session_id = result.get("session_id") or session_id

    for _ in range(10):
        if result.get("plan_status") != "generating":
            return result
        await api.asyncio.sleep(0.02)
        result = await api.finalize_learning_requirement(
            None,
            api.FinalizeLearningRequirementRequest(
                draft=draft,
                session_id=current_session_id,
            ),
        )
        current_session_id = result.get("session_id") or current_session_id

    return result


async def _today_until_ready():
    result = await api.get_today_tasks(None)

    for _ in range(10):
        if result.get("timetable_status") != "model_generating":
            return result
        await api.asyncio.sleep(0.02)
        result = await api.get_today_tasks(None)

    return result


def test_similar_active_goal_is_detected():
    goals = {"goal-java": _existing_goal()}

    match = api._find_similar_active_goal(_complete_draft(), goals)

    assert match is not None
    assert match["id"] == "goal-java"


def test_completed_similar_goal_does_not_block_new_goal():
    goals = {"goal-java": _existing_goal(status="completed")}

    assert api._find_similar_active_goal(_complete_draft(), goals) is None


def test_different_university_courses_do_not_duplicate_high_math():
    goals = {
        "goal-math": _existing_goal(
            goal_type="certification",
            id="goal-math",
            name="\u9ad8\u7b49\u6570\u5b66",
            tags=["\u9ad8\u7b49\u6570\u5b66", "\u8003\u8bd5/\u8003\u8bc1", "\u5237\u9898\u8bad\u7ec3"],
        )
    }
    candidate = _complete_draft(
        goal_name="\u6570\u636e\u7ed3\u6784",
        goal_type="certification",
        preference="\u5237\u9898\u8bad\u7ec3",
        purpose="\u8003\u8bd5/\u8003\u8bc1",
        tags=["\u6570\u636e\u7ed3\u6784", "\u8003\u8bd5/\u8003\u8bc1", "\u5237\u9898\u8bad\u7ec3"],
        target_level="\u7cfb\u7edf\u638c\u63e1\u6838\u5fc3\u6982\u5ff5\u5e76\u80fd\u5b8c\u6210\u8bfe\u7a0b\u9898\u76ee",
    )

    assert api._find_similar_active_goal(candidate, goals) is None


def test_course_planner_normalization_keeps_dynamic_phase_count():
    plan_spec = {
        "g": {
            "h": 90,
            "n": "鍔ㄦ€侀樁娈佃绋?",
            "type": "knowledge",
        },
        "p": [
            {
                "n": f"闃舵 {index}",
                "t": [{"m": 45, "t": f"浠诲姟 {index}"}],
            }
            for index in range(1, 8)
        ],
    }

    plans = api._normalize_plan_spec(plan_spec)

    assert len(plans) == 7
    assert plans[-1]["name"] == "闃舵 7"


def test_extract_json_object_accepts_prefixed_model_text():
    parsed = api._extract_json_object('prefix {"g":{"n":"math","h":12},"p":[]} suffix')

    assert parsed["g"]["n"] == "math"


def test_extract_json_object_accepts_nested_fenced_json():
    parsed = api._extract_json_object(
        '```json\n{"g":{"n":"data-structure","h":72},"p":[{"n":"linear-list","t":[{"t":"sequence-list","m":60}]}]}\n```'
    )

    assert parsed["g"]["n"] == "data-structure"
    assert parsed["p"][0]["t"][0]["t"] == "sequence-list"


def test_learning_json_loader_accepts_utf8_bom(tmp_path):
    path = tmp_path / "schedule_state.json"
    path.write_text('{"course_plan_spec_errors":{}}', encoding="utf-8-sig")

    assert api._read_learning_json_dict(path) == {"course_plan_spec_errors": {}}


def test_completed_task_actual_minutes_ignores_estimate_without_timer_record():
    assert api._completed_task_actual_minutes({"estimated_minutes": 180}) == 0
    assert api._completed_task_actual_minutes({"learning_feedback": {"actual_minutes": 37}}) == 37


def test_course_planner_normalization_accepts_common_model_schema_variants():
    plan_spec = {
        "course": {
            "title": "楂樼瓑鏁板寮哄寲",
            "summary": "缁撳悎鏈熸湯鐩爣杩涜寮哄寲",
            "total_hours": 6,
            "type": "knowledge",
        },
        "phases": [
            {
                "title": "鏋侀檺鍩虹",
                "summary": "鍏堣ˉ瀹氫箟鍜岃绠?",
                "steps": [
                    {
                        "name": "姊崇悊鏋侀檺瀹氫箟",
                        "detail": "鏁寸悊甯歌鏋侀檺棰樺瀷",
                        "duration": "1灏忔椂",
                        "level": "medium",
                        "order": 1,
                    }
                ],
            }
        ],
    }

    plans = api._normalize_plan_spec(plan_spec)
    api._require_course_plan_spec(plan_spec)
    goal_payload = api._goal_payload_from_plan_spec({}, {"estimated_hours": 10}, plan_spec)

    assert plans[0]["name"] == "鏋侀檺鍩虹"
    assert plans[0]["tasks"][0]["title"] == "姊崇悊鏋侀檺瀹氫箟"
    assert plans[0]["tasks"][0]["estimated_minutes"] == 60
    assert goal_payload["name"] == "楂樼瓑鏁板寮哄寲"
    assert goal_payload["estimated_hours"] == 6


@pytest.mark.asyncio
async def test_short_add_course_requirement_is_ready_without_timeframe(learning_home, monkeypatch):
    monkeypatch.setattr(api, "_call_learning_llm_json", lambda **kwargs: {})

    result = await api.analyze_learning_requirement(
        None,
        api.AnalyzeLearningRequirementRequest(message="\u6dfb\u52a0\u9ad8\u6570"),
    )

    assert result["ready"] is True
    assert result["missing_fields"] == []
    assert result["draft"]["goal_name"] == "\u9ad8\u7b49\u6570\u5b66"
    assert result["draft"]["purpose"] == "\u8003\u8bd5/\u8003\u8bc1"
    assert result["draft"]["target_level"]
    assert result["questions"] == []


@pytest.mark.asyncio
async def test_add_data_structure_course_requirement_normalizes_topic(learning_home, monkeypatch):
    monkeypatch.setattr(api, "_call_learning_llm_json", lambda **kwargs: {})

    result = await api.analyze_learning_requirement(
        None,
        api.AnalyzeLearningRequirementRequest(
            message="\u6211\u8981\u52a0\u5165\u4e00\u95e8\u6570\u636e\u7ed3\u6784\u8bfe\u7a0b"
        ),
    )

    assert result["ready"] is True
    assert result["missing_fields"] == []
    assert result["draft"]["goal_name"] == "\u6570\u636e\u7ed3\u6784"
    assert result["draft"]["purpose"] == "\u8003\u8bd5/\u8003\u8bc1"


@pytest.mark.asyncio
async def test_finalize_reuses_similar_goal_without_creating_duplicate(learning_home):
    api._save_goals({"goal-java": _existing_goal()})

    result = await api.finalize_learning_requirement(
        None,
        api.FinalizeLearningRequirementRequest(draft=_complete_draft()),
    )

    goals = api._load_goals()
    assert result["goal_created"] is False
    assert result["duplicate_goal"] is True
    assert result["goal"]["id"] == "goal-java"
    assert list(goals) == ["goal-java"]
    assert api._load_plans() == {}
    assert api._load_tasks() == {}


@pytest.mark.asyncio
async def test_finalize_short_course_requirement_with_edited_timeframe(learning_home, monkeypatch):
    monkeypatch.setattr(
        api,
        "_call_learning_llm_json",
        lambda **kwargs: _plan_response(goal_name="\u9ad8\u7b49\u6570\u5b66"),
    )

    result = await _finalize_until_ready(
        {
            "current_level": "\u5165\u95e8",
            "daily_minutes": 60,
            "goal_name": "\u9ad8\u7b49\u6570\u5b66",
            "goal_type": "certification",
            "preference": "\u5237\u9898\u8bad\u7ec3",
            "purpose": "\u8003\u8bd5/\u8003\u8bc1",
            "tags": ["\u9ad8\u7b49\u6570\u5b66"],
            "target_level": "\u7cfb\u7edf\u638c\u63e1\u6838\u5fc3\u6982\u5ff5\u5e76\u80fd\u5b8c\u6210\u8bfe\u7a0b\u9898\u76ee",
            "timeframe": "45\u5929",
            "timeframe_days": 45,
        }
    )

    assert result["goal_created"] is True
    assert result["goal"]["name"] == "\u9ad8\u7b49\u6570\u5b66"
    assert result["goal"]["target_date"] is not None
    assert result["plans"]
    assert result["tasks"]


@pytest.mark.asyncio
async def test_finalize_accepts_requirement_without_timeframe(learning_home, monkeypatch):
    monkeypatch.setattr(
        api,
        "_call_learning_llm_json",
        lambda **kwargs: _plan_response(goal_name="\u9ad8\u7b49\u6570\u5b66"),
    )

    result = await _finalize_until_ready(
        {
            "current_level": "\u5165\u95e8",
            "daily_minutes": 60,
            "goal_name": "\u9ad8\u7b49\u6570\u5b66",
            "goal_type": "certification",
            "preference": "\u5237\u9898\u8bad\u7ec3",
            "purpose": "\u8003\u8bd5/\u8003\u8bc1",
            "target_level": "\u901a\u8fc7\u671f\u672b\u8003\u8bd5",
        }
    )

    assert result["plan_status"] == "ready"
    assert result["goal_created"] is True
    assert result["goal"]["name"] == "\u9ad8\u7b49\u6570\u5b66"


@pytest.mark.asyncio
async def test_finalize_data_structure_course_returns_generating_while_plan_llm_runs(
    learning_home, monkeypatch
):
    calls = []

    def slow_llm_json(*, system_message, payload, label, max_tokens=None):
        calls.append(label)
        time.sleep(0.08)
        return _plan_response(goal_name="\u6570\u636e\u7ed3\u6784")

    monkeypatch.setattr(api, "_call_learning_llm_json", slow_llm_json)

    started = time.monotonic()
    first = await api.finalize_learning_requirement(
        None,
        api.FinalizeLearningRequirementRequest(
            draft=_complete_draft(
                goal_name="\u6570\u636e\u7ed3\u6784",
                goal_type="certification",
                preference="\u5237\u9898\u8bad\u7ec3",
                purpose="\u8003\u8bd5/\u8003\u8bc1",
                tags=["\u6570\u636e\u7ed3\u6784"],
                target_level="\u7cfb\u7edf\u638c\u63e1\u6838\u5fc3\u6982\u5ff5\u5e76\u80fd\u5b8c\u6210\u8bfe\u7a0b\u9898\u76ee",
            )
        )
    )
    elapsed = time.monotonic() - started

    for _ in range(10):
        if calls:
            break
        await api.asyncio.sleep(0.01)

    assert calls == ["course-planner"]
    assert elapsed < 0.5
    assert first["plan_status"] == "generating"
    assert first["session_id"]
    assert api._load_goals() == {}
    assert api._load_plans() == {}
    assert api._load_tasks() == {}

    await api.asyncio.sleep(0.12)
    second = await api.finalize_learning_requirement(
        None,
        api.FinalizeLearningRequirementRequest(
            draft=_complete_draft(
                goal_name="\u6570\u636e\u7ed3\u6784",
                goal_type="certification",
                preference="\u5237\u9898\u8bad\u7ec3",
                purpose="\u8003\u8bd5/\u8003\u8bc1",
                tags=["\u6570\u636e\u7ed3\u6784"],
                target_level="\u7cfb\u7edf\u638c\u63e1\u6838\u5fc3\u6982\u5ff5\u5e76\u80fd\u5b8c\u6210\u8bfe\u7a0b\u9898\u76ee",
            ),
            session_id=first["session_id"],
        ),
    )

    assert calls == ["course-planner"]
    assert second["plan_status"] == "ready"
    assert second["goal_created"] is True
    assert second["goal"]["name"] == "\u6570\u636e\u7ed3\u6784"


@pytest.mark.asyncio
async def test_task_guidance_uses_llm_structured_response(learning_home, monkeypatch):
    api._save_goals({"goal-java": _existing_goal()})
    api._save_plans({"plan-1": _existing_plan()})
    api._save_tasks({"task-1": _existing_task()})

    def fake_llm_json(*, system_message, payload, label, max_tokens=None):
        assert label == "task-guidance"
        assert payload["task"]["title"] == "鍩虹璇婃柇娴嬭瘯"
        return {
            "summary": "鍏堝畾浣嶈杽寮辩偣锛屽啀鐢ㄤ竴閬撻楠岃瘉銆?",
            "steps": ["鍒楀嚭涓嶄細鐨勭珷鑺?", "瀹屾垚涓€缁勫熀纭€棰?"],
            "focus_points": ["閿欏洜褰掔被"],
            "pitfalls": ["涓嶈鍙湅绛旀"],
            "checklist": ["鐣欎笅钖勫急鐐规竻鍗?"],
        }

    monkeypatch.setattr(api, "_call_learning_llm_json", fake_llm_json)

    first = await api.get_task_guidance(None, "task-1")
    await api.asyncio.sleep(0.01)
    result = await api.get_task_guidance(None, "task-1")

    assert first["status"] == "generating"
    assert result["generated_by"] in {"learning-skill", "learning-skill-cache"}
    assert result["status"] == "ready"
    assert result["skill"] == "task-guidance"
    assert result["summary"] == "鍏堝畾浣嶈杽寮辩偣锛屽啀鐢ㄤ竴閬撻楠岃瘉銆?"
    assert result["steps"] == ["鍒楀嚭涓嶄細鐨勭珷鑺?", "瀹屾垚涓€缁勫熀纭€棰?"]


@pytest.mark.asyncio
async def test_task_guidance_reuses_cached_model_result(learning_home, monkeypatch):
    api._save_goals({"goal-java": _existing_goal()})
    api._save_plans({"plan-1": _existing_plan()})
    api._save_tasks({"task-1": _existing_task()})
    calls = []

    def fake_llm_json(*, system_message, payload, label, max_tokens=None):
        calls.append(label)
        assert "updated_at" not in payload["student_profile"]
        return {
            "summary": "鍏堝畾浣嶈杽寮辩偣锛屽啀瀹屾垚涓€缁勯獙璇侀銆?",
            "steps": ["鍒楀嚭涓嶄細鐨勭珷鑺?", "瀹屾垚涓€缁勫熀纭€棰?", "璁板綍閿欏洜"],
            "focus_points": ["閿欏洜褰掔被", "姒傚康杈圭晫"],
            "pitfalls": ["涓嶈鍙湅绛旀", "涓嶈璺宠繃楠岀畻"],
            "checklist": ["鏈夐敊鍥犳竻鍗?", "鏈変笅涓€姝ラ噸鐐?"],
        }

    monkeypatch.setattr(api, "_call_learning_llm_json", fake_llm_json)

    first = await api.get_task_guidance(None, "task-1")
    await api.asyncio.sleep(0.01)
    second = await api.get_task_guidance(None, "task-1")

    assert calls == ["task-guidance"]
    assert first["generated_by"] == "learning-skill-pending"
    assert first["status"] == "generating"
    assert second["generated_by"] == "learning-skill-cache"
    assert second["status"] == "ready"
    assert second["summary"] == "鍏堝畾浣嶈杽寮辩偣锛屽啀瀹屾垚涓€缁勯獙璇侀銆?"


@pytest.mark.asyncio
async def test_task_guidance_returns_failed_when_llm_returns_invalid_json(learning_home, monkeypatch):
    api._save_goals({"goal-java": _existing_goal(name="楂樼瓑鏁板")})
    api._save_plans({"plan-1": _existing_plan(name="鍑芥暟涓庢瀬闄?")})
    api._save_tasks({"task-1": _existing_task(title="鏋侀檺鍩虹缁冧範")})

    def slow_llm_json(*, system_message, payload, label, max_tokens=None):
        time.sleep(0.08)
        return {}

    monkeypatch.setattr(api, "_call_learning_llm_json", slow_llm_json)

    started = time.monotonic()
    first = await api.get_task_guidance(None, "task-1")
    elapsed = time.monotonic() - started
    await api.asyncio.sleep(0.12)
    second = await api.get_task_guidance(None, "task-1")

    assert elapsed < 0.5
    assert first["status"] == "generating"
    assert second["status"] == "failed"
    assert second["generated_by"] == "learning-skill-failed"
    assert "task-guidance" in second["error"]


@pytest.mark.asyncio
async def test_completion_feedback_creates_follow_up_task(learning_home, monkeypatch):
    api._save_goals({"goal-java": _existing_goal()})
    api._save_plans({"plan-1": _existing_plan()})
    api._save_tasks({"task-1": _existing_task()})

    def fake_llm_json(*, system_message, payload, label, max_tokens=None):
        assert label == "progress-feedback"
        assert payload["completion_feedback"]["mastery_level"] == "娌″お鎳?"
        return {
            "summary": "鍛介閫昏緫杞崲浠嶄笉绋筹紝闇€瑕佸厛琛ヤ竴涓煭澶嶄範鍧椼€?",
            "mastery_signal": "weak",
            "pace_signal": "behind",
            "risk_level": "high",
            "schedule_hint": "涓嬩竴娆″厛瀹夋帓鑼冨紡杞崲澶嶄範锛屽啀缁х画鏂板唴瀹广€?",
            "next_guidance_focus": ["鑼冨紡杞崲", "鐪熷€艰〃妫€鏌?"],
            "duration_multiplier": 1.2,
            "follow_up_task": {
                "needed": True,
                "title": "琛ョ粌鍛介閫昏緫鑼冨紡杞崲",
                "description": "閽堝鏈鍙嶉鐨勫崱鐐癸紝瀹屾垚鑼冨紡杞崲渚嬮骞惰褰曢敊鍥犮€?",
                "estimated_minutes": 45,
                "priority": 1,
            },
        }

    monkeypatch.setattr(api, "_call_learning_llm_json", fake_llm_json)

    result = await api.update_task_status(
        None,
        "task-1",
        api.UpdateTaskStatusRequest(
            actual_minutes=70,
            blockers=["鑼冨紡杞崲", "鐪熷€艰〃"],
            feedback="鍋氶鏃惰寖寮忚浆鎹㈡€绘槸婕忔潯浠躲€?",
            mastery_level="娌″お鎳?",
            next_focus="鍏堣ˉ鑼冨紡杞崲",
            status="completed",
        ),
    )

    tasks = api._load_tasks()
    follow_ups = [task for task in tasks.values() if task.get("source") == "progress-feedback"]

    assert result["status"] == "completed"
    assert result["learning_feedback"]["analysis"]["mastery_signal"] == "weak"
    assert len(follow_ups) == 1
    assert follow_ups[0]["title"] == "琛ョ粌鍛介閫昏緫鑼冨紡杞崲"
    assert api._load_plans()["plan-1"]["total_tasks"] == 2
    assert api._load_plans()["plan-1"]["completed_tasks"] == 1
    assert api._load_goals()["goal-java"]["progress"] == 50.0


def test_today_schedule_refresh_keeps_unscheduled_backlog_when_capacity_is_full(learning_home):
    profile = _complete_profile()
    profile["weekday_minutes"] = 60
    profile["weekend_minutes"] = 60
    tasks = {
        "task-1": _existing_task(
            created_at="2026-07-21T00:00:00+00:00",
            estimated_minutes=60,
            id="task-1",
            priority=1,
            title="楂樼瓑鏁板鍩虹缁冧範",
        ),
        "task-2": _existing_task(
            created_at="2026-07-21T00:01:00+00:00",
            estimated_minutes=60,
            id="task-2",
            priority=2,
            title="鏁版嵁缁撴瀯绾挎€ц〃缁冧範",
        ),
    }

    refreshed = api._refresh_today_task_schedule(
        tasks,
        {"goal-java": _existing_goal()},
        profile,
        datetime(2026, 7, 21, tzinfo=timezone.utc),
        force=True,
    )

    assert refreshed is True
    assert tasks["task-1"]["scheduled_date"] == "2026-07-21"
    assert tasks["task-2"]["scheduled_date"] == ""


def test_today_schedule_uses_deadline_pressure_over_profile_baseline(learning_home):
    profile = _complete_profile()
    profile["weekday_minutes"] = 60
    profile["weekend_minutes"] = 60
    tasks = {
        f"task-{index}": _existing_task(
            created_at=f"2026-07-21T00:0{index}:00+00:00",
            estimated_minutes=60,
            id=f"task-{index}",
            priority=index,
            scheduled_date="",
            title=f"deadline pressure task {index}",
        )
        for index in range(1, 4)
    }

    api._refresh_today_task_schedule(
        tasks,
        {"goal-java": _existing_goal(target_date="2026-07-22")},
        profile,
        datetime(2026, 7, 21, tzinfo=timezone.utc),
        force=True,
    )

    scheduled_ids = {
        task_id
        for task_id, task in tasks.items()
        if task["scheduled_date"] == "2026-07-21"
    }

    assert scheduled_ids == {"task-1", "task-2", "task-3"}


def test_today_schedule_refresh_rolls_over_unfinished_previous_day(learning_home):
    profile = _complete_profile()
    profile["weekday_minutes"] = 120
    tasks = {
        "task-1": _existing_task(
            estimated_minutes=60,
            id="task-1",
            scheduled_date="2026-07-20",
            title="鏄ㄥぉ鏈畬鎴?",
        ),
        "task-2": _existing_task(
            estimated_minutes=60,
            id="task-2",
            scheduled_date="",
            title="浠诲姟姹犲唴瀹?",
        ),
    }

    api._refresh_today_task_schedule(
        tasks,
        {"goal-java": _existing_goal()},
        profile,
        datetime(2026, 7, 21, tzinfo=timezone.utc),
        force=True,
    )

    assert tasks["task-1"]["scheduled_date"] == "2026-07-21"
    assert tasks["task-2"]["scheduled_date"] == "2026-07-21"


def test_local_date_uses_shanghai_day_boundary():
    local_date = api._local_date_from(datetime(2026, 7, 21, 16, 30, tzinfo=timezone.utc))

    assert local_date.isoformat() == "2026-07-22"


def test_requirement_estimate_uses_topic_and_profile_not_fixed_template():
    draft = _complete_draft(
        current_level="\u5165\u95e8",
        goal_name="\u9ad8\u7b49\u6570\u5b66",
        goal_type="knowledge",
        purpose="\u8003\u8bd5/\u8003\u8bc1",
        target_level="\u901a\u8fc7\u671f\u672b\u8003\u8bd5",
        timeframe="\u4e24\u4e2a\u6708",
        timeframe_days=60,
    )
    draft["student_context"] = {
        "major": "\u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f",
        "weaknesses": "\u6570\u5b66\u57fa\u7840\u8584\u5f31",
    }
    draft["time_constraints"] = {
        "weekday_minutes": 60,
        "weekend_minutes": 180,
        "weekly_minutes": 660,
    }

    estimate = api._estimate_requirement(draft)

    assert estimate["estimated_hours"] != 68.8
    assert estimate["estimated_hours"] >= 100
    assert estimate["available_days"] == 60


def test_today_schedule_interleaves_learning_goals(learning_home):
    profile = _complete_profile()
    profile["weekday_minutes"] = 120
    tasks = {
        "math-1": _existing_task(
            created_at="2026-07-21T00:00:00+00:00",
            estimated_minutes=60,
            goal_id="goal-math",
            id="math-1",
            plan_id="plan-math",
            position=0,
            priority=1,
            scheduled_date="",
            title="楂樼瓑鏁板鍩虹璇婃柇",
        ),
        "math-2": _existing_task(
            created_at="2026-07-21T00:01:00+00:00",
            estimated_minutes=60,
            goal_id="goal-math",
            id="math-2",
            plan_id="plan-math",
            position=1,
            priority=1,
            scheduled_date="",
            title="楂樼瓑鏁板钖勫急鐐规暣鐞?",
        ),
        "structure-1": _existing_task(
            created_at="2026-07-21T00:02:00+00:00",
            estimated_minutes=60,
            goal_id="goal-structure",
            id="structure-1",
            plan_id="plan-structure",
            position=0,
            priority=1,
            scheduled_date="",
            title="鏁版嵁缁撴瀯绾挎€ц〃澶嶄範",
        ),
        "structure-2": _existing_task(
            created_at="2026-07-21T00:03:00+00:00",
            estimated_minutes=60,
            goal_id="goal-structure",
            id="structure-2",
            plan_id="plan-structure",
            position=1,
            priority=1,
            scheduled_date="",
            title="鏁版嵁缁撴瀯鏍戜笌鍥惧涔?",
        ),
    }

    api._refresh_today_task_schedule(
        tasks,
        {
            "goal-math": _existing_goal(id="goal-math", name="楂樼瓑鏁板"),
            "goal-structure": _existing_goal(id="goal-structure", name="鏁版嵁缁撴瀯"),
        },
        profile,
        datetime(2026, 7, 22, tzinfo=timezone.utc),
        force=True,
    )

    scheduled_ids = {
        task_id
        for task_id, task in tasks.items()
        if task["scheduled_date"] == "2026-07-22"
    }

    assert scheduled_ids == {"math-1", "structure-1"}


def test_today_schedule_respects_plan_and_task_sequence(learning_home):
    profile = _complete_profile()
    profile["weekday_minutes"] = 120
    plans = {
        "plan-first": _existing_plan(
            id="plan-first",
            name="first plan",
            total_tasks=2,
        ),
        "plan-second": _existing_plan(
            id="plan-second",
            name="second plan",
            total_tasks=1,
        ),
    }
    tasks = {
        "later-stage": _existing_task(
            estimated_minutes=60,
            id="later-stage",
            plan_id="plan-second",
            position=0,
            priority=1,
            scheduled_date="",
            title="later stage should wait",
        ),
        "first-task": _existing_task(
            estimated_minutes=60,
            id="first-task",
            plan_id="plan-first",
            position=0,
            priority=5,
            scheduled_date="",
            title="first task should start",
        ),
        "second-task": _existing_task(
            estimated_minutes=60,
            id="second-task",
            plan_id="plan-first",
            position=1,
            priority=5,
            scheduled_date="",
            title="second task should follow",
        ),
    }

    api._refresh_today_task_schedule(
        tasks,
        {"goal-java": _existing_goal()},
        profile,
        datetime(2026, 7, 22, tzinfo=timezone.utc),
        force=True,
        plans=plans,
    )

    assert tasks["first-task"]["scheduled_date"] == "2026-07-22"
    assert tasks["second-task"]["scheduled_date"] == "2026-07-22"
    assert tasks["later-stage"]["scheduled_date"] == ""


@pytest.mark.asyncio
async def test_today_tasks_use_local_timetable_in_task_sequence(learning_home, monkeypatch):
    profile = _complete_profile()
    profile["weekday_minutes"] = 180
    profile["preferred_study_time"] = "晚上"
    api._save_learning_profile(profile)
    api._save_goals({"goal-java": _existing_goal()})
    api._save_plans({"plan-1": _existing_plan(total_tasks=2)})
    api._save_tasks(
        {
            "task-1": _existing_task(
                estimated_minutes=60,
                id="task-1",
                position=0,
                scheduled_date="2026-07-22",
                title="first task",
            ),
            "task-2": _existing_task(
                estimated_minutes=60,
                id="task-2",
                position=1,
                scheduled_date="2026-07-22",
                title="second task",
            ),
        }
    )
    monkeypatch.setattr(
        api,
        "_local_now",
        lambda: datetime(2026, 7, 22, 8, 0, tzinfo=api._LOCAL_TIMEZONE),
    )

    def fail_llm_json(**kwargs):
        raise AssertionError("local timetable should not call the daily scheduler model")

    monkeypatch.setattr(api, "_call_learning_llm_json", fail_llm_json)

    result = await _today_until_ready()

    assert result["generated_by"] == "local-timetable"
    assert result["skill"] is None
    assert result["timetable_status"] == "ready"
    assert [task["id"] for task in result["tasks"]] == ["task-1", "task-2"]
    assert result["tasks"][0]["recommended_start_time"] == "19:00"
    assert result["tasks"][1]["recommended_start_time"] == "21:00"


@pytest.mark.asyncio
async def test_today_tasks_local_timetable_respects_morning_preference(learning_home, monkeypatch):
    profile = _complete_profile()
    profile["preferred_study_time"] = "上午"
    api._save_learning_profile(profile)
    api._save_goals({"goal-java": _existing_goal()})
    api._save_plans({"plan-1": _existing_plan()})
    api._save_tasks({"task-1": _existing_task(scheduled_date="2026-07-22")})
    monkeypatch.setattr(
        api,
        "_local_now",
        lambda: datetime(2026, 7, 22, 8, 0, tzinfo=api._LOCAL_TIMEZONE),
    )

    result = await _today_until_ready()

    assert result["timetable_status"] == "ready"
    assert result["tasks"][0]["recommended_start_time"] == "08:00"


@pytest.mark.asyncio
async def test_today_tasks_return_ahead_recommendations_after_today_is_done(learning_home, monkeypatch):
    api._save_goals({"goal-java": _existing_goal(target_date="2026-07-25")})
    api._save_plans({"plan-1": _existing_plan(total_tasks=2, completed_tasks=1)})
    api._save_tasks(
        {
            "task-done": _existing_task(
                completed_at="2026-07-22T09:00:00+08:00",
                estimated_minutes=60,
                id="task-done",
                scheduled_date="2026-07-22",
                status="completed",
                title="today completed task",
            ),
            "task-next": _existing_task(
                estimated_minutes=60,
                id="task-next",
                scheduled_date="",
                title="tomorrow candidate task",
            ),
        }
    )
    monkeypatch.setattr(
        api,
        "_local_now",
        lambda: datetime(2026, 7, 22, 8, 0, tzinfo=api._LOCAL_TIMEZONE),
    )

    result = await api.get_today_tasks(None)

    assert result["tasks"][0]["id"] == "task-done"
    assert result["ahead_date"] == "2026-07-23"
    assert result["ahead_tasks"][0]["id"] == "task-next"
    assert result["ahead_tasks"][0]["ahead"] is True
    assert result["ahead_tasks"][0]["scheduled_date"] == "2026-07-23"


@pytest.mark.asyncio
async def test_today_timetable_ignores_scheduler_timeout_path(learning_home, monkeypatch):
    api._save_goals({"goal-java": _existing_goal()})
    api._save_plans({"plan-1": _existing_plan()})
    api._save_tasks({"task-1": _existing_task(scheduled_date="2026-07-22")})
    monkeypatch.setattr(
        api,
        "_local_now",
        lambda: datetime(2026, 7, 22, 8, 0, tzinfo=api._LOCAL_TIMEZONE),
    )

    async def timeout_scheduler(**kwargs):
        raise AssertionError("local timetable should not start the daily scheduler")

    monkeypatch.setattr(api, "_generate_today_timetable_with_llm", timeout_scheduler)

    result = await api.get_today_tasks(None)

    assert result["tasks"][0]["id"] == "task-1"
    assert result["tasks"][0]["recommended_start_time"] == "09:00"
    assert result["timetable_status"] == "ready"
    assert result["timetable_error"] is None

@pytest.mark.asyncio
async def test_analyze_requirement_uses_llm_structured_draft(learning_home, monkeypatch):
    def fake_llm_json(*, system_message, payload, label, max_tokens=None):
        assert label == "requirement-intake"
        return {
            "draft": {
                "current_level": "鍏ラ棬",
                "daily_minutes": 75,
                "goal_name": "Rust 寮傛鍚庣椤圭洰",
                "goal_type": "project",
                "preference": "椤圭洰瀹炴垬",
                "purpose": "鎵惧疄涔?",
                "tags": ["Rust", "Tokio", "鍚庣"],
                "target_level": "鑳界嫭绔嬪畬鎴愬悗绔湇鍔″苟璁叉竻骞跺彂妯″瀷",
                "timeframe": "8鍛?",
                "timeframe_days": 56,
            }
        }

    monkeypatch.setattr(api, "_call_learning_llm_json", fake_llm_json)

    result = await api.analyze_learning_requirement(
        None,
        api.AnalyzeLearningRequirementRequest(message="鎴戞兂鍋?Rust 鍚庣鎵惧疄涔?"),
    )

    assert result["ready"] is True
    assert result["draft"]["goal_name"] == "Rust 寮傛鍚庣椤圭洰"
    assert result["draft"]["goal_type"] == "project"
    assert result["draft"]["daily_minutes"] == round(result["estimate"]["estimated_hours"] * 60 / 56)
    assert result["draft"]["tags"] == ["Rust", "Tokio", "鍚庣"]


@pytest.mark.asyncio
async def test_finalize_requirement_uses_llm_generated_plan(learning_home, monkeypatch):
    calls = []

    def fake_llm_json(*, system_message, payload, label, max_tokens=None):
        calls.append(label)
        if label == "course-planner":
            return {
                "goal": {
                    "description": "闈㈠悜璁＄畻鏈哄ぇ涓夊鐢熺殑 Rust/Tokio 鍚庣椤圭洰璁″垝",
                    "estimated_hours": 72,
                    "goal_type": "project",
                    "name": "Rust 寮傛鍚庣椤圭洰鍐插埡",
                    "prerequisites": ["HTTP 鍩虹"],
                    "tags": ["Rust", "Tokio", "鏁版嵁搴?"],
                },
                "plans": [
                    {
                        "description": "鎵撶墷鎵€鏈夋潈銆侀敊璇鐞嗗拰 async/await 鍩虹",
                        "name": "Rust 鎵€鏈夋潈涓庡紓姝ュ熀纭€",
                        "tasks": [
                            {
                                "description": "璁板綍鎵€鏈夋潈杞Щ銆佸€熺敤鍜岀敓鍛藉懆鏈熷崱鐐?",
                                "difficulty": "medium",
                                "estimated_minutes": 70,
                                "priority": 1,
                                "scheduled_offset_days": 0,
                                "title": "鐢ㄦ墍鏈夋潈瑙勫垯閲嶅啓涓€涓枃浠惰В鏋愬皬绋嬪簭",
                            }
                        ],
                    }
                ],
            }
        return {}

    monkeypatch.setattr(api, "_call_learning_llm_json", fake_llm_json)

    result = await _finalize_until_ready(
        _complete_draft(
            goal_name="Rust 鍚庣鎵惧疄涔?",
            goal_type="project",
            tags=["Rust"],
        )
    )

    assert calls == ["course-planner"]
    assert result["goal"]["name"] == "Rust 寮傛鍚庣椤圭洰鍐插埡"
    assert result["goal"]["estimated_hours"] == 72
    assert result["plans"][0]["name"] == "Rust 鎵€鏈夋潈涓庡紓姝ュ熀纭€"
    assert result["tasks"][0]["title"] == "鐢ㄦ墍鏈夋潈瑙勫垯閲嶅啓涓€涓枃浠惰В鏋愬皬绋嬪簭"


@pytest.mark.asyncio
async def test_finalize_requirement_accepts_compact_course_planner_schema(learning_home, monkeypatch):
    calls = []

    def fake_llm_json(*, system_message, payload, label, max_tokens=None):
        calls.append(label)
        assert label == "course-planner"
        assert max_tokens == api._LEARNING_PLAN_MAX_OUTPUT_TOKENS
        assert payload["iface"] == "course_plan.v2"
        assert payload["req"]["n"] == "鏁版嵁缁撴瀯"
        assert "updated_at" not in payload["student"]
        assert "plans" not in payload["limits"]
        assert "tasks_per_plan" not in payload["limits"]
        return {
            "g": {
                "d": "闈㈠悜鏈熸湯澶嶄範鐨勬暟鎹粨鏋勮鍒?",
                "h": 48,
                "n": "鏁版嵁缁撴瀯鏈熸湯澶嶄範",
                "pre": ["C 璇█鍩虹"],
                "tags": ["鏁版嵁缁撴瀯", "鏈熸湯"],
                "type": "knowledge",
            },
            "p": [
                {
                    "d": "鍏堣ˉ绾挎€ц〃涓庡鏉傚害",
                    "n": "绾挎€х粨鏋?",
                    "t": [
                        {
                            "d": "鍋氶摼琛ㄦ彃鍒犳煡鏀归骞惰褰曢敊鍥?",
                            "diff": "medium",
                            "m": 60,
                            "p": 1,
                            "t": "閾捐〃鍩虹棰樿缁?",
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(api, "_call_learning_llm_json", fake_llm_json)

    result = await _finalize_until_ready(
        _complete_draft(
            goal_name="鏁版嵁缁撴瀯",
            goal_type="knowledge",
            tags=["鏁版嵁缁撴瀯"],
        )
    )

    assert calls == ["course-planner"]
    assert result["goal"]["name"] == "鏁版嵁缁撴瀯鏈熸湯澶嶄範"
    assert result["goal"]["estimated_hours"] == 48
    assert result["plans"][0]["name"] == "绾挎€х粨鏋?"
    assert result["tasks"][0]["title"] == "閾捐〃鍩虹棰樿缁?"



