# app/main.py
# ------------------------------------------------------------
# FastAPI 엔드포인트: 통합 리포트 생성 (파일 스캔 / 단건 생성)
# - GET  /health          : 상태 체크
# - GET  /reports/list    : data/ 폴더 내 JSON 파일 목록
# - POST /reports/run     : data/ 폴더 스캔해서 각 파일의 type(weekly/monthly)로 리포트 생성 후 바로 반환
# - POST /reports/generate: 요청 본문(단일 사용자 통합 스키마)으로 즉시 생성 후 바로 반환
# ------------------------------------------------------------

from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta, date
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import os
import json
import copy
import calendar

# api/generate_report.py에서 '존재하는' 항목만 임포트
from api.generate_report import (
    build_prompt,
    call_gemini,
    minutes_between,
    add_minutes,
    extract_json_safely,
    # minutes_filter_copy,   # ✅ 더 이상 사용하지 않음 (상대일 제거)
)

# 입력 디렉토리 (로컬 data 폴더)
INPUT_DIR = "data"

app = FastAPI(title="Unified Habit Report API", version="1.3.0")

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
    type: str = Field(..., pattern="^(weekly|monthly)$")  # 요청 본문의 type
    # ✅ 사용자가 명시하면 그대로 사용
    start_date: Optional[str] = None  # "YYYY-MM-DD"
    end_date: Optional[str] = None    # "YYYY-MM-DD"
    habits: List[Habit]


class GenerateRunResponseItem(BaseModel):
    # 공통 메타
    user_id: int
    nickname: str
    type: str

    # 리포트 내용(루트에 직접 노출)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    summary: Optional[Union[str, Dict[str, Any]]] = None
    top_failure_reasons: Optional[List[Dict[str, Any]]] = None
    recommendation: Optional[List[Dict[str, Any]]] = None

    # 오류시 루트에 배치
    error: Optional[str] = None

    class Config:
        extra = "allow"  # parsed에 예기치 않은 키가 들어와도 허용


class GenerateRunResponse(BaseModel):
    results: List[GenerateRunResponseItem]


# ===================== 날짜 유틸 (전 주/전 월 계산) =====================

def _prev_week_period(today: date) -> (date, date):
    """
    전 주(월~일) 기간을 반환.
    예) 오늘이 2025-09-16(화) -> 전 주: 2025-09-08(월) ~ 2025-09-14(일)
    """
    # 이번 주 월요일
    this_monday = today - timedelta(days=today.isoweekday() - 1)  # 월=1
    prev_monday = this_monday - timedelta(days=7)
    prev_sunday = prev_monday + timedelta(days=6)
    return prev_monday, prev_sunday


def _prev_month_period(today: date) -> (date, date):
    """
    전 월(1일~말일) 기간을 반환.
    예) 오늘이 2025-09-16 -> 전 월: 2025-08-01 ~ 2025-08-31
    """
    year = today.year
    month = today.month
    if month == 1:
        year -= 1
        month = 12
    else:
        month -= 1
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    return start, end


def _normalize_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _decide_period(bundle: Dict[str, Any]) -> (str, str):
    """
    1) bundle에 start_date/end_date가 있으면 그대로 사용
    2) 없으면 type 기준으로 '전 주' 또는 '전 월'을 계산해서 사용
       - 주간: 월~일
       - 월간: 1일~말일
    """
    type_ = (bundle.get("type") or "monthly").lower()
    sd = _normalize_date(bundle.get("start_date"))
    ed = _normalize_date(bundle.get("end_date"))

    if sd and ed:
        return str(sd), str(ed)

    today = datetime.today().date()
    if type_ == "weekly":
        start_d, end_d = _prev_week_period(today)
    else:
        start_d, end_d = _prev_month_period(today)

    return str(start_d), str(end_d)


# ===================== 로그 필터링 (기간 기반) =====================

def _filter_habits_by_date_range(habits: List[Dict[str, Any]], start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """
    습관 로그에서 날짜가 [start_date, end_date] (inclusive) 에 속하는 항목만 남겨 복사본 반환.
    """
    sd = datetime.strptime(start_date, "%Y-%m-%d").date()
    ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    out = []
    for h in habits:
        h_copy = copy.deepcopy(h)
        logs = h_copy.get("habit_log", [])
        filtered = []
        for log in logs:
            try:
                d = datetime.strptime(log.get("date", ""), "%Y-%m-%d").date()
                if sd <= d <= ed:
                    filtered.append(log)
            except Exception:
                # 날짜 파싱 실패 로그는 제외
                continue
        h_copy["habit_log"] = filtered
        out.append(h_copy)
    return [h for h in out if h.get("habit_log")]  # 로그 없는 항목 제거


# ===================== 보정 로직 =====================

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

    # (1) 잘못된 habit_id 보정 / 이름 매칭
    for rec in recs:
        rid = rec.get("habit_id")
        if rid not in valid_id_set:
            rname = (rec.get("name") or "").strip().lower()
            matched = None
            for hid, nm in name_by_id.items():
                if rname and rname == (nm or "").strip().lower():
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


def _attach_habit_ids_to_failures(parsed: Dict[str, Any], active_habits: List[Dict[str, Any]]) -> None:
    """
    top_failure_reasons 각 항목에 habit_id를 채워 넣는다.
    - LLM이 'top_failure_reason' (단수)로 줄 경우도 자동 변환
    - 이름으로 먼저 매칭(대소문자/공백 무시), 실패 시 첫번째 habit_id로 폴백
    """
    # 키 노멀라이즈: 단수로 오면 복수로 승격
    if "top_failure_reasons" not in parsed and "top_failure_reason" in parsed:
        tfr = parsed.get("top_failure_reason")
        parsed["top_failure_reasons"] = tfr if isinstance(tfr, list) else [tfr]
        parsed.pop("top_failure_reason", None)

    items = parsed.get("top_failure_reasons")
    if not isinstance(items, list):
        return

    valid_ids = [h.get("habit_id") for h in active_habits]
    name_by_id = {h.get("habit_id"): (h.get("name") or "") for h in active_habits}
    id_by_name_norm = { (nm or "").strip().lower(): hid for hid, nm in name_by_id.items() if nm }

    for it in items:
        if not isinstance(it, dict):
            continue
        if "habit_id" in it and it["habit_id"] in valid_ids:
            continue  # 이미 정상
        name = (it.get("habit") or it.get("name") or "").strip().lower()
        hid = id_by_name_norm.get(name)
        if hid is None:
            for nm_norm, candidate_hid in id_by_name_norm.items():
                if name and (name in nm_norm or nm_norm in name):
                    hid = candidate_hid
                    break
        it["habit_id"] = hid if hid is not None else valid_ids[0]

def _normalize_parsed_fields(parsed: Dict[str, Any]) -> None:
    # summary가 str이면 dict로 감싸기
    if isinstance(parsed.get("summary"), str):
        parsed["summary"] = {"text": parsed["summary"]}

    # top_failure_reasons가 dict 단일이면 리스트로 승격
    if isinstance(parsed.get("top_failure_reasons"), dict):
        parsed["top_failure_reasons"] = [parsed["top_failure_reasons"]]

    # recommendation이 dict 단일이면 리스트로 승격
    if isinstance(parsed.get("recommendation"), dict):
        parsed["recommendation"] = [parsed["recommendation"]]

# ===================== 리포트 생성 =====================

def _generate_for_user_bundle(bundle: Dict[str, Any]) -> GenerateRunResponseItem:
    """
    단일 사용자 통합 스키마(bundle)로 리포트 생성 후 '바로 반환'
    - bundle['type']를 그대로 사용
    - 기간은 (1) bundle에 start/end가 있으면 그대로, (2) 없으면 전 주/전 월
    - 파일 저장 없음
    """
    try:
        type_ = (bundle.get("type") or "monthly").lower()
        if type_ not in ("weekly", "monthly"):
            type_ = "monthly"

        # ✅ 기간 결정 (요청이 월/주 고정 스케줄로 트리거되었다고 가정)
        start_date, end_date = _decide_period(bundle)

        user_id = bundle["user_id"]
        nickname = bundle.get("nickname", str(user_id))
        habits_all = bundle.get("habits", [])

        # 기간으로 로그 필터링
        active_habits = _filter_habits_by_date_range(habits_all, start_date, end_date)
        if not active_habits:
            raise HTTPException(status_code=404, detail=f"{nickname}: 지정 기간 내 데이터 없음")

        user_info = {
            "user_id": user_id,
            "nickname": nickname,
            "birth_year": bundle.get("birth_year"),
            "gender": bundle.get("gender"),
            "job": bundle.get("job"),
        }

        # 프롬프트 생성 및 LLM 호출
        prompt = build_prompt(type_, user_info, active_habits, start_date, end_date)
        response = call_gemini(prompt)

        # JSON 안전 추출/파싱
        json_text = extract_json_safely(response)
        parsed = json.loads(json_text)
        parsed.setdefault("start_date", start_date)
        parsed.setdefault("end_date", end_date)

        # 보정 로직들
        _normalize_parsed_fields(parsed)
        _postprocess_recommendations(parsed, active_habits)
        _attach_habit_ids_to_failures(parsed, active_habits)

        # 루트에 바로 병합해서 반환
        result = {
            "user_id": user_id,
            "nickname": nickname,
            "type": type_,
            **parsed,
        }
        return GenerateRunResponseItem(**result)

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
    data/ 폴더를 스캔하여 각 파일의 'type' 값(weekly/monthly)에 따라 리포트 생성 후 바로 반환.
    - 파일 스키마는 단일 사용자 통합 스키마만 지원.
    - 기간은 bundle의 start/end가 우선, 없으면 전 주/전 월
    - 파일 저장 없음
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
            results.append(_generate_for_user_bundle(bundle))
        except HTTPException as he:
            results.append(GenerateRunResponseItem(
                user_id=bundle.get("user_id", -1) if isinstance(bundle, dict) else -1,
                nickname=bundle.get("nickname", "unknown") if isinstance(bundle, dict) else "unknown",
                type=(bundle.get("type") or "monthly") if isinstance(bundle, dict) else "monthly",
                error=he.detail
            ))
        except Exception as e:
            results.append(GenerateRunResponseItem(
                user_id=bundle.get("user_id", -1) if isinstance(bundle, dict) else -1,
                nickname=bundle.get("nickname", "unknown") if isinstance(bundle, dict) else "unknown",
                type=(bundle.get("type") or "monthly") if isinstance(bundle, dict) else "monthly",
                error=str(e)
            ))

    if not any_found:
        raise HTTPException(status_code=404, detail="data/ 폴더에 JSON 파일이 없습니다.")
    return GenerateRunResponse(results=results)


@app.post("/reports/generate", response_model=GenerateRunResponseItem)
def generate_from_body(payload: UserPayload):
    """
    요청 본문(단일 사용자 통합 스키마)으로 리포트를 즉시 생성해서 반환.
    - payload.type의 값(weekly/monthly)을 그대로 사용
    - start_date/end_date가 오면 그대로 사용, 없으면 전 주/전 월을 자동 적용
    - 파일 저장 없음
    """
    bundle = payload.dict()
    return _generate_for_user_bundle(bundle)