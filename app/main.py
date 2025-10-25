# app/main.py
# ------------------------------------------------------------
# FastAPI 엔드포인트
# - GET  /health
# - GET  /reports/list
# - POST /reports/run       : data/ 폴더 스캔 후 즉시 생성 & 반환
# - POST /reports/generate  : 요청 본문(단일 사용자 통합 스키마) 즉시 생성 & 반환
# ------------------------------------------------------------

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import os, json
from collections import Counter

# ===== generate_report.py에서 필요한 함수 =====
from api.generate_report import (
    minutes_filter_copy,
    compute_per_habit_top_failure_reasons,
    compute_overall_success_rate,
    consistency_level_from_rate,
    generate_recommendations,
    normalize_reason_category,
    REASON_ICON_MAP,
    guess_emoji_from_text
)

INPUT_DIR = "data"
app = FastAPI(title="Unified Habit Report API", version="3.1.0")

# ===================== Pydantic 모델 =====================
class HabitLog(BaseModel):
    date: str
    completed: bool
    failure_reason: Optional[List[str]] = None
    class Config:
        extra = "allow"

class Habit(BaseModel):
    habit_id: int
    name: str
    day_of_week: List[int]
    start_time: str
    end_time: str
    habit_log: List[HabitLog]
    class Config:
        extra = "allow"

class UserPayload(BaseModel):
    user_id: int
    nickname: str
    birth_year: Optional[int] = None
    gender: Optional[str] = None
    job: Optional[str] = None
    type: str = Field(..., pattern="^(WEEKLY|MONTHLY)$")
    habits: List[Habit]
    class Config:
        extra = "allow"

class GenerateRunResponseItem(BaseModel):
    user_id: int
    nickname: str
    type: str
    top_failure_reasons: List[Dict[str, Any]]
    consistency_index: Dict[str, Any]
    summary: Dict[str, Any]
    recommendation: List[Dict[str, Any]]
    error: Optional[str] = None
    class Config:
        extra = "allow"

class GenerateRunResponse(BaseModel):
    results: List[GenerateRunResponseItem]


# ===================== 내부 유틸 =====================
def _calc_period_by_type(report_type: str):
    """No longer used for date filtering, kept for compatibility"""
    report_type = (report_type or "monthly").lower()
    return "", "", 0

def _normalize_times_in_habits(habits: List[dict]) -> List[dict]:
    normed = []
    for h in habits or []:
        h2 = dict(h)
        for key in ("start_time", "end_time"):
            val = h2.get(key)
            if isinstance(val, str):
                t = val.strip()
                for fmt in ("%H:%M", "%H:%M:%S"):
                    try:
                        dt = datetime.strptime(t, fmt)
                        h2[key] = dt.strftime("%H:%M")
                        break
                    except ValueError:
                        continue
        normed.append(h2)
    return normed


# ===== 간단한 MAP 상태 추정 (generate_summary 대체) =====
def _infer_overall_map_state(habits, rate: float):
    labels = []
    total, success = 0, 0
    for h in habits:
        logs = h.get("habit_log", [])
        total += len(logs)
        success += sum(1 for l in logs if l.get("completed"))
        for l in logs:
            if l.get("completed"):
                continue
            for raw in (l.get("failure_reason") or []):
                labels.append(normalize_reason_category(raw))
    c = Counter(labels)
    ability_low = c["시간 부족"] + c["일정 충돌"] + c["과도한 목표 설정"]
    motivation_low = c["의지 부족"]
    ability = "low" if ability_low >= 2 else ("medium" if ability_low == 1 else "high")
    motivation = "low" if motivation_low >= 2 else ("medium" if motivation_low == 1 else ("low" if rate < 30 else "high"))
    if motivation == "low":
        prompt = "spark"
    elif ability == "low":
        prompt = "facilitator"
    else:
        prompt = "signal"
    return prompt


def _generate_summary_bmap(nickname, habits, failure_data, rate):
    """generate_report.py의 B=MAP 버전 summary 간략 이식"""
    pt = _infer_overall_map_state(habits, rate)

    consistency = (
        f"바쁜 기간 속에서 {rate:.1f}%나 해냈다는 건 {nickname}님의 꾸준함이 돋보입니다."
        if rate > 40 else
        "이번 기간은 새로운 시작을 위한 준비 기간이었어요."
    )

    # 주요 실패 원인
    all_reasons = []
    for h in (failure_data or []):
        for r in h.get("reasons", []):
            if isinstance(r, dict) and r.get("reason"):
                all_reasons.append(r["reason"])
    if all_reasons:
        most_common, _ = Counter(all_reasons).most_common(1)[0]
        norm = normalize_reason_category(most_common)
        if norm == "기타 (직접 입력)":
            icon = guess_emoji_from_text(most_common)
            failure_reasons = f"{icon} 직접 입력된 사유가 많았어요. 예: \"{most_common}\""
        else:
            icon = REASON_ICON_MAP.get(norm, "💬")
            failure_reasons = f"가장 자주 등장한 방해 요인은 {icon} '{most_common}'이에요."
        if pt == "facilitator":
            failure_reasons += " → 이번 주는 '5분만/한 단계만'으로 가볍게 시작해봐요."
    else:
        failure_reasons = "이번 기간은 큰 방해 없이 잘 이어졌어요."

    # 요일 패턴
    weekday_success, weekday_total = Counter(), Counter()
    for h in habits:
        for log in h.get("habit_log", []):
            try:
                day = datetime.strptime(log["date"], "%Y-%m-%d").weekday()
            except Exception:
                continue
            weekday_total[day] += 1
            if log.get("completed"):
                weekday_success[day] += 1
    if weekday_total:
        rate_by_day = {d: weekday_success[d] / weekday_total[d] for d in weekday_total}
        best_day = max(rate_by_day, key=rate_by_day.get)
        worst_day = min(rate_by_day, key=rate_by_day.get)
        days = ["월", "화", "수", "목", "금", "토", "일"]
        daily_pattern = f"{days[best_day]}요일엔 리듬이 좋고, {days[worst_day]}요일엔 약간 느슨했어요."
    else:
        daily_pattern = "요일별 패턴을 확인할 데이터가 부족했어요."

    # 프롬프트 톤별 카피
    courage = {
        "spark": "작은 행동에도 마음이 움직입니다. 오늘의 1분이 내일의 루틴으로 이어질 거예요.",
        "facilitator": "시작은 언제나 작을수록 좋아요. 부담 없이 한 걸음만 내딛어봐요.",
        "signal": "지금 흐름이 아주 좋아요. 이 느낌 그대로 이어가면 충분합니다."
    }[pt]

    return {
        "consistency": consistency,
        "failure_reasons": failure_reasons,
        "daily_pattern": daily_pattern,
        "courage": courage
    }


# ===================== 핵심 리포트 생성 함수 =====================
def _generate_for_bundle(bundle: Dict[str, Any]) -> GenerateRunResponseItem:
    """generate_report.py 로직 기반으로 즉시 리포트 생성 + B=MAP 반영"""
    try:
        t = (bundle.get("type") or "monthly").lower()
        if t not in ("weekly", "monthly"):
            t = "monthly"

        user_id = bundle["user_id"]
        nickname = bundle.get("nickname", str(user_id))
        habits_all = _normalize_times_in_habits(bundle.get("habits", []))

        active_habits = [h for h in minutes_filter_copy(habits_all) if h.get("habit_log")]
        if not active_habits:
            raise HTTPException(status_code=404, detail=f"{nickname}: 데이터 없음")

        parsed = {}

        top_fail = compute_per_habit_top_failure_reasons(active_habits, topk=2)
        rate = compute_overall_success_rate(active_habits)
        level = consistency_level_from_rate(rate)
        parsed["consistency_index"] = {
            "success_rate": round(rate, 1),
            "display_message": f"꾸준함 지수: {level}" +
                               (" 🔥" if level == "높음" else (" 🙂" if level == "보통" else " 🌧️"))
        }

        parsed["top_failure_reasons"] = top_fail
        parsed["summary"] = _generate_summary_bmap(nickname, active_habits, top_fail, rate)
        parsed["recommendation"] = generate_recommendations(active_habits)

        return GenerateRunResponseItem(user_id=user_id, nickname=nickname, type=t, **parsed)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"리포트 생성 실패: {e}")


# ===================== 엔드포인트 =====================
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}

@app.get("/reports/list")
def list_reports():
    if not os.path.isdir(INPUT_DIR):
        raise HTTPException(status_code=404, detail="data/ 폴더가 없습니다.")
    files = [os.path.join(INPUT_DIR, f) for f in os.listdir(INPUT_DIR) if f.endswith(".json")]
    return {"files": sorted(files)}

@app.post("/reports/run", response_model=GenerateRunResponse)
def run_from_data():
    if not os.path.isdir(INPUT_DIR):
        raise HTTPException(status_code=404, detail="data/ 폴더가 없습니다.")
    results: List[GenerateRunResponseItem] = []
    any_found = False
    for filename in os.listdir(INPUT_DIR):
        if not filename.endswith(".json"): continue
        any_found = True
        path = os.path.join(INPUT_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                bundle = json.load(f)
            results.append(_generate_for_bundle(bundle))
        except Exception as e:
            results.append(GenerateRunResponseItem(
                user_id=-1, nickname=filename, type="unknown",
                top_failure_reasons=[], consistency_index={},
                summary={}, recommendation=[], error=str(e)
            ))
    if not any_found:
        raise HTTPException(status_code=404, detail="data/ 폴더에 JSON 파일이 없습니다.")
    return GenerateRunResponse(results=results)

@app.post("/reports/generate", response_model=GenerateRunResponseItem)
def generate_from_body(payload: UserPayload):
    return _generate_for_bundle(payload.dict())