# app/main.py
# ------------------------------------------------------------
# FastAPI 엔드포인트 
# - GET  /health
# - GET  /reports/list
# - POST /reports/run       : data/ 폴더 스캔 후 즉시 생성 & 반환
# - POST /reports/generate  : 요청 본문(단일 사용자 통합 스키마) 즉시 생성 & 반환
# ------------------------------------------------------------

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, date
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import os
import json
import copy

# generate_report.py에서 필요한 항목만 임포트
from api.generate_report import (
    minutes_filter_copy,
    compute_per_habit_top_failure_reasons,
    compute_overall_success_rate,
    consistency_level_from_rate,
    CONSISTENCY_THRESHOLDS,
    generate_monthly_summary,
)

INPUT_DIR = "data"
app = FastAPI(title="Unified Habit Report API", version="2.0.0")

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
    type: str = Field(..., pattern="^(WEEKLY|MONTHLY)$")  # WEEKLY / MONTHLY
    habits: List[Habit]
    class Config:
        extra = "allow"

class GenerateRunResponseItem(BaseModel):
    user_id: int
    nickname: str
    type: str
    start_date: str
    end_date: str
    top_failure_reasons: List[Dict[str, Any]] = []
    summary: Optional[Dict[str, Any]] = None
    consistency_index: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    class Config:
        extra = "allow"

class GenerateRunResponse(BaseModel):
    results: List[GenerateRunResponseItem]

# ===================== 내부 유틸 =====================

def _calc_period_by_type(report_type: str) -> (str, str, int):
    """WEEKLY → 7일, MONTHLY → 30일 기준으로 [start_date, end_date] 문자열 반환."""
    report_type = (report_type or "monthly").lower()
    days = 7 if report_type == "weekly" else 30
    end_date = datetime.today().date()
    start_date = end_date - timedelta(days=days)
    return str(start_date), str(end_date), days

def _generate_for_bundle(bundle: Dict[str, Any]) -> GenerateRunResponseItem:
    """신규 generate_report.py 규칙만 사용하여 즉시 결과 구성."""
    try:
        t = (bundle.get("type") or "monthly").lower()
        if t not in ("weekly", "monthly"):
            t = "monthly"

        user_id = bundle["user_id"]
        nickname = bundle.get("nickname", str(user_id))
        habits_all = bundle.get("habits", [])
        start_date, end_date, filter_days = _calc_period_by_type(t)

        # 최근 N일 로그 필터링 (신규 파일 로직과 동일)
        active_habits = [h for h in minutes_filter_copy(habits_all, filter_days) if h.get("habit_log")]
        if not active_habits:
            raise HTTPException(status_code=404, detail=f"{nickname}: 최근 {filter_days}일 데이터 없음")

        # 공통 필드
        parsed: Dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
        }

        # 실패 사유 상위 2개(습관별)
        top_fail = compute_per_habit_top_failure_reasons(active_habits, topk=2)
        parsed["top_failure_reasons"] = top_fail

        # 월간 전용: 꾸준함 지수 + 월간 요약
        if t == "monthly":
            rate = compute_overall_success_rate(active_habits)
            level = consistency_level_from_rate(rate)
            parsed["consistency_index"] = {
                "success_rate": round(rate, 1),
                "level": level,
                "thresholds": CONSISTENCY_THRESHOLDS,
                "display_message": f"꾸준함 지수: {level}" + (" 🔥" if level == "높음" else (" 🙂" if level == "보통" else " 🌧️")),
            }
            parsed["summary"] = generate_monthly_summary(nickname, active_habits, top_fail)

        return GenerateRunResponseItem(
            user_id=user_id,
            nickname=nickname,
            type=t,
            **parsed,
        )

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
    """data/ 폴더 내 JSON 입력 파일 목록을 나열 (저장 파일 없음)"""
    if not os.path.isdir(INPUT_DIR):
        raise HTTPException(status_code=404, detail="data/ 폴더가 없습니다.")
    files = [os.path.join(INPUT_DIR, fn) for fn in os.listdir(INPUT_DIR) if fn.endswith(".json")]
    return {"files": sorted(files)}

@app.post("/reports/run", response_model=GenerateRunResponse)
def run_from_data():
    """
    data/ 폴더 스캔 → 각 파일의 type(WEEKLY/MONTHLY) 기준(7일/30일)으로 즉시 생성 & 반환.
    파일 저장은 하지 않음.
    """
    if not os.path.isdir(INPUT_DIR):
        raise HTTPException(status_code=404, detail="data/ 폴더가 없습니다.")

    results: List[GenerateRunResponseItem] = []
    any_found = False

    for filename in os.listdir(INPUT_DIR):
        if not filename.endswith(".json"):
            continue
        any_found = True
        path = os.path.join(INPUT_DIR, filename)

        bundle = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                bundle = json.load(f)
            results.append(_generate_for_bundle(bundle))

        except HTTPException as he:
            # bundle 파싱 실패도 안전 처리
            uid = bundle.get("user_id", -1) if isinstance(bundle, dict) else -1
            nick = bundle.get("nickname", "unknown") if isinstance(bundle, dict) else "unknown"
            t = (bundle.get("type") or "monthly").lower() if isinstance(bundle, dict) else "monthly"
            results.append(GenerateRunResponseItem(
                user_id=uid, nickname=nick, type=t, start_date="", end_date="", error=he.detail
            ))
        except Exception as e:
            uid = bundle.get("user_id", -1) if isinstance(bundle, dict) else -1
            nick = bundle.get("nickname", "unknown") if isinstance(bundle, dict) else "unknown"
            t = (bundle.get("type") or "monthly").lower() if isinstance(bundle, dict) else "monthly"
            results.append(GenerateRunResponseItem(
                user_id=uid, nickname=nick, type=t, start_date="", end_date="", error=str(e)
            ))

    if not any_found:
        raise HTTPException(status_code=404, detail="data/ 폴더에 JSON 파일이 없습니다.")

    return GenerateRunResponse(results=results)

@app.post("/reports/generate", response_model=GenerateRunResponseItem)
def generate_from_body(payload: UserPayload):
    """
    요청 본문(단일 사용자 통합 스키마) 기반으로 즉시 생성 & 반환.
    - WEEKLY: 최근 7일
    - MONTHLY: 최근 30일
    """
    return _generate_for_bundle(payload.dict())