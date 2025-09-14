# app/main.py
# ------------------------------------------------------------
# FastAPI 엔드포인트: 통합 리포트 생성 (파일 스캔 / 단건 생성)
# - GET  /health          : 상태 체크
# - GET  /reports/list    : 생성된 리포트 파일 목록
# - POST /reports/run     : data/ 폴더 스캔해서 각 파일의 type(weekly/monthly)로 리포트 생성
# - POST /reports/generate: 요청 본문(단일 사용자 통합 스키마)으로 즉시 생성
# ------------------------------------------------------------

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import os
import json

# api/generate_report.py에서 '존재하는' 항목만 임포트
from api.generate_report import (
    build_prompt,
    call_gemini,
    minutes_between,
    add_minutes,
    extract_json_safely,
    minutes_filter_copy,
    OUTPUT_DIRS,
)

# 입력/출력 디렉토리 (현재는 로컬의 data 폴더 사용) 
INPUT_DIR = "data"

app = FastAPI(title="Unified Habit Report API", version="1.1.1")

# 출력 디렉토리 보장
for _dir in OUTPUT_DIRS.values():
    os.makedirs(_dir, exist_ok=True)


# ===================== Pydantic 모델 =====================

class HabitLog(BaseModel):
    date: str
    completed: bool
    failure_reason: Optional[List[str]] = None


class Habit(BaseModel):
    habit_id: int
    name: str
    day_of_week: List[int]
    start_time: str
    end_time: str
    habit_log: List[HabitLog]


class UserPayload(BaseModel):
    user_id: int
    nickname: str
    birth_year: Optional[int] = None
    gender: Optional[str] = None
    job: Optional[str] = None
    type: str = Field(..., pattern="^(weekly|monthly)$")  # 요청 본문의 type을 그대로 사용
    habits: List[Habit]


class GenerateRunResponseItem(BaseModel):
    user_id: int
    nickname: str
    report_type: str
    json_path: str
    report_json: Optional[Dict[str, Any]] = None


class GenerateRunResponse(BaseModel):
    results: List[GenerateRunResponseItem]


# ===================== 내부 유틸 =====================
# recommendationa 후보정 과정
def _postprocess_recommendations(parsed: Dict[str, Any], active_habits: List[Dict[str, Any]]) -> None:
    """
    recommendation 보정:
    - 모든 입력 습관에 대해 최소 1개 추천 보장
    - 잘못된 habit_id 보정
    - 입력 습관 순서와 동일하게 정렬
    """
    valid_ids = [h.get("habit_id") for h in active_habits]  # 순서 유지
    valid_id_set = set(valid_ids)
    name_by_id = {h.get("habit_id"): h.get("name") for h in active_habits}

    recs = parsed.get("recommendation", [])
    if not isinstance(recs, list):
        recs = []

    # (1) 잘못된 habit_id 보정
    for rec in recs:
        rid = rec.get("habit_id")
        if rid not in valid_id_set:
            rname = (rec.get("name") or "").lower()
            matched = None
            for hid, nm in name_by_id.items():
                if rname and rname == (nm or "").lower():
                    matched = hid
                    break
            rec["habit_id"] = matched if matched is not None else valid_ids[0]

    # (2) 누락된 habit_id에 기본 추천 생성
    existing_by_id = {}
    for rec in recs:
        rid = rec.get("habit_id")
        if rid in valid_id_set and rid not in existing_by_id:
            existing_by_id[rid] = rec

    for hid in valid_ids:
        if hid in existing_by_id:
            continue
        src = next(h for h in active_habits if h.get("habit_id") == hid)
        st = src.get("start_time") or "00:00"
        et = src.get("end_time") or "00:30"
        try:
            session_minutes = max(10, minutes_between(st, et) - 15)
            new_end = add_minutes(st, session_minutes)
        except Exception:
            new_end = et
        recs.append({
            "habit_id": hid,
            "name": f"{(src.get('name') or '습관')} (가벼운 버전)",
            "start_time": st,
            "end_time": new_end,
            "day_of_week": src.get("day_of_week", [1, 2, 3, 4, 5]),
        })

    # (3) 입력 순서대로 정렬
    parsed["recommendation"] = [next((r for r in recs if r.get("habit_id") == hid), None)
                                for hid in valid_ids if next((r for r in recs if r.get("habit_id") == hid), None)]


def _filter_days_by_type(report_type: str) -> int:
    return 7 if report_type == "weekly" else 30


def _period_by_filter_days(filter_days: int):
    end_date = datetime.today().date()
    start_date = end_date - timedelta(days=filter_days)
    return str(start_date), str(end_date)


def _ensure_and_save(report_type: str, user_id: int, nickname: str, parsed: Dict[str, Any]) -> str:
    out_dir = OUTPUT_DIRS[report_type]
    os.makedirs(out_dir, exist_ok=True)
    json_name = f"user_{user_id}_{nickname}_{report_type}_report.json"
    json_path = os.path.join(out_dir, json_name)
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(parsed, jf, ensure_ascii=False, indent=2)
    return json_path


def _generate_for_user_bundle(bundle: Dict[str, Any], return_json: bool = False) -> GenerateRunResponseItem:
    """
    단일 사용자 통합 스키마(bundle)로 리포트 생성 후 저장/반환
    - bundle['type']를 그대로 사용
    - 마지막 N일 로그만 고려 (weekly=7, monthly=30)
    """
    try:
        report_type = (bundle.get("type") or "monthly").lower()
        if report_type not in ("weekly", "monthly"):
            report_type = "monthly"

        filter_days = _filter_days_by_type(report_type)
        start_date, end_date = _period_by_filter_days(filter_days)

        user_id = bundle["user_id"]
        nickname = bundle.get("nickname", str(user_id))
        habits_all = bundle.get("habits", [])

        # 최근 N일 로그만 보존한 복사본 생성
        active_habits = [h for h in minutes_filter_copy(habits_all, filter_days) if h.get("habit_log")]
        if not active_habits:
            raise HTTPException(status_code=404, detail=f"{nickname}: 최근 데이터 없음")

        user_info = {
            "user_id": user_id,
            "nickname": nickname,
            "birth_year": bundle.get("birth_year"),
            "gender": bundle.get("gender"),
            "job": bundle.get("job"),
        }

        # 프롬프트 생성 및 LLM 호출
        prompt = build_prompt(report_type, user_info, active_habits, start_date, end_date)
        response = call_gemini(prompt)

        # JSON 안전 추출/파싱
        json_text = extract_json_safely(response)
        parsed = json.loads(json_text)
        parsed.setdefault("start_date", start_date)
        parsed.setdefault("end_date", end_date)

        # 추천 보정
        _postprocess_recommendations(parsed, active_habits)

        # 저장
        json_path = _ensure_and_save(report_type, user_id, nickname, parsed)

        return GenerateRunResponseItem(
            user_id=user_id,
            nickname=nickname,
            report_type=report_type,
            json_path=json_path,
            report_json=parsed if return_json else None,
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
    """생성된 리포트 파일 경로를 모두 나열"""
    out = []
    for typ, d in OUTPUT_DIRS.items():
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".json"):
                out.append(os.path.join(d, fn))
    return {"files": sorted(out)}


@app.post("/reports/run", response_model=GenerateRunResponse)
def run_from_data(return_json: bool = False):
    """
    data/ 폴더를 스캔하여 각 파일의 'type' 값(weekly/monthly)에 따라 리포트 생성.
    - 파일 스키마는 단일 사용자 통합 스키마만 지원.
    """
    results: List[GenerateRunResponseItem] = []
    any_found = False

    if not os.path.isdir(INPUT_DIR):
        raise HTTPException(status_code=404, detail="data/ 폴더가 없습니다.")

    for filename in os.listdir(INPUT_DIR):
        if not filename.endswith(".json"):
            continue
        any_found = True
        path = os.path.join(INPUT_DIR, filename)
        bundle = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                bundle = json.load(f)
            results.append(_generate_for_user_bundle(bundle, return_json=return_json))
        except HTTPException as he:
            results.append(GenerateRunResponseItem(
                user_id=bundle.get("user_id", -1) if isinstance(bundle, dict) else -1,
                nickname=bundle.get("nickname", "unknown") if isinstance(bundle, dict) else "unknown",
                report_type=(bundle.get("type") or "monthly") if isinstance(bundle, dict) else "monthly",
                json_path="",
                report_json={"error": he.detail} if return_json else None
            ))
        except Exception as e:
            results.append(GenerateRunResponseItem(
                user_id=bundle.get("user_id", -1) if isinstance(bundle, dict) else -1,
                nickname=bundle.get("nickname", "unknown") if isinstance(bundle, dict) else "unknown",
                report_type=(bundle.get("type") or "monthly") if isinstance(bundle, dict) else "monthly",
                json_path="",
                report_json={"error": str(e)} if return_json else None
            ))

    if not any_found:
        raise HTTPException(status_code=404, detail="data/ 폴더에 JSON 파일이 없습니다.")
    return GenerateRunResponse(results=results)


@app.post("/reports/generate", response_model=GenerateRunResponseItem)
def generate_from_body(payload: UserPayload, return_json: bool = False):
    """
    요청 본문(단일 사용자 통합 스키마)으로 리포트를 즉시 생성.
    - payload.type의 값(weekly/monthly)을 그대로 사용
    """
    bundle = payload.dict()
    return _generate_for_user_bundle(bundle, return_json=return_json)