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

# generate_report.py에서 필요한 함수 가져오기
from api.generate_report import (
    minutes_filter_copy,
    compute_per_habit_top_failure_reasons,
    compute_overall_success_rate,
    consistency_level_from_rate,
    CONSISTENCY_THRESHOLDS,
    generate_summary,
    generate_recommendations,
)

INPUT_DIR = "data"
app = FastAPI(title="Unified Habit Report API", version="3.0.0")

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
    """리포트 타입별 기간 계산"""
    report_type = (report_type or "monthly").lower()
    days = 7 if report_type == "weekly" else 30
    end_date = datetime.today().date()
    start_date = end_date - timedelta(days=days)
    return str(start_date), str(end_date), days


def _normalize_times_in_habits(habits: List[dict]) -> List[dict]:
    """
    각 habit의 start_time/end_time을 'HH:MM'로 정규화.
    'HH:MM:SS'가 들어와도 문제 없도록 통일.
    실패 시 원본을 그대로 둠(보수적).
    """
    normed = []
    for h in habits or []:
        h2 = dict(h)  # 얕은 복사
        for key in ("start_time", "end_time"):
            val = h2.get(key)
            if isinstance(val, str):
                t = val.strip()
                # 시도1: HH:MM
                try:
                    dt = datetime.strptime(t, "%H:%M")
                    h2[key] = dt.strftime("%H:%M")
                    continue
                except ValueError:
                    pass
                # 시도2: HH:MM:SS
                try:
                    dt = datetime.strptime(t, "%H:%M:%S")
                    h2[key] = dt.strftime("%H:%M")  # 분까지만
                    continue
                except ValueError:
                    pass
                # 시도3: 콜론 분해(예: '21:5:00' → '21:05')
                try:
                    parts = [int(p) for p in t.split(":")]
                    if len(parts) >= 2:
                        hh, mm = parts[0], parts[1]
                        h2[key] = f"{hh:02d}:{mm:02d}"
                except Exception:
                    # 그대로 둠
                    pass
        normed.append(h2)
    return normed

def _generate_for_bundle(bundle: Dict[str, Any]) -> GenerateRunResponseItem:
    """generate_report.py 로직 기반으로 즉시 리포트 생성"""
    try:
        t = (bundle.get("type") or "monthly").lower()
        if t not in ("weekly", "monthly"):
            t = "monthly"

        user_id = bundle["user_id"]
        nickname = bundle.get("nickname", str(user_id))
        habits_all = bundle.get("habits", [])
        habits_all = _normalize_times_in_habits(habits_all)
        start_date, end_date, filter_days = _calc_period_by_type(t)

        # 로그 필터링
        active_habits = [h for h in minutes_filter_copy(habits_all, filter_days) if h.get("habit_log")]
        if not active_habits:
            raise HTTPException(status_code=404, detail=f"{nickname}: 최근 {filter_days}일 데이터 없음")

        # 공통 항목
        parsed = {
            "start_date": start_date,
            "end_date": end_date,
        }

        # 실패 사유 상위 2개
        top_fail = compute_per_habit_top_failure_reasons(active_habits, topk=2)
        parsed["top_failure_reasons"] = top_fail

        # 꾸준함 지수 계산
        rate = compute_overall_success_rate(active_habits)
        level = consistency_level_from_rate(rate)
        parsed["consistency_index"] = {
            "success_rate": round(rate, 1),
            "level": level,
            "thresholds": CONSISTENCY_THRESHOLDS,
            "display_message": f"꾸준함 지수: {level}" +
                               (" 🔥" if level == "높음" else (" 🙂" if level == "보통" else " 🌧️"))
        }

        # summary 생성
        parsed["summary"] = generate_summary(nickname, active_habits, top_fail, rate)

        # recommendation 생성
        parsed["recommendation"] = generate_recommendations(active_habits)

        return GenerateRunResponseItem(
            user_id=user_id,
            nickname=nickname,
            type=t,
            **parsed
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
    if not os.path.isdir(INPUT_DIR):
        raise HTTPException(status_code=404, detail="data/ 폴더가 없습니다.")
    files = [os.path.join(INPUT_DIR, f) for f in os.listdir(INPUT_DIR) if f.endswith(".json")]
    return {"files": sorted(files)}

@app.post("/reports/run", response_model=GenerateRunResponse)
def run_from_data():
    """data 폴더 내 JSON 스캔 후 리포트 생성"""
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
                start_date="", end_date="", top_failure_reasons=[], consistency_index={},
                summary={}, recommendation=[], error=str(e)
            ))
    if not any_found:
        raise HTTPException(status_code=404, detail="data/ 폴더에 JSON 파일이 없습니다.")
    return GenerateRunResponse(results=results)

@app.post("/reports/generate", response_model=GenerateRunResponseItem)
def generate_from_body(payload: UserPayload):
    """요청 본문으로 리포트 생성"""
    return _generate_for_bundle(payload.dict())