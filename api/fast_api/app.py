import os
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

# -----------------------------
# 기존 상수/설정
# -----------------------------
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY 가 .env 에 설정되어야 합니다.")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

INPUT_DIR = "data/sample_users"
OUTPUT_DIRS = {
    "weekly": "outputs/weekly_report",
    "monthly": "outputs/monthly_report",
}
for _dir in OUTPUT_DIRS.values():
    os.makedirs(_dir, exist_ok=True)


def extract_last_days_logs(logs, days):
    today = datetime.today().date()
    from_date = today - timedelta(days=days)
    return [
        log for log in logs
        if from_date <= datetime.strptime(log["date"], "%Y-%m-%d").date() <= today
    ]


def group_users_by_id(filter_days):
    """사용자 ID별로 모든 습관 데이터를 그룹화 (최근 N일 필터링)"""
    from collections import defaultdict
    users = defaultdict(lambda: {"habits": [], "user_info": {}})

    if not os.path.isdir(INPUT_DIR):
        return users

    for filename in os.listdir(INPUT_DIR):
        if not filename.endswith(".json"):
            continue

        with open(os.path.join(INPUT_DIR, filename), "r", encoding="utf-8") as f:
            habit_data = json.load(f)

        user_id = habit_data["user_id"]

        # 사용자 기본 정보 저장 (첫 번째 습관에서)
        if not users[user_id]["user_info"]:
            users[user_id]["user_info"] = {
                "user_id": habit_data["user_id"],
                "nickname": habit_data["nickname"],
                "age": habit_data.get("age"),
                "occupation": habit_data.get("occupation"),
                "characteristics": habit_data.get("characteristics", [])
            }

        # 습관 정보 추가 (최근 N일로 필터링)
        habit_info = {
            "habit_id": habit_data["habit_id"],
            "name": habit_data["name"],
            "description": habit_data.get("description", ""),
            "schedule": habit_data.get("schedule", ""),
            "habit_log": extract_last_days_logs(habit_data["habit_log"], filter_days)
        }
        users[user_id]["habits"].append(habit_info)

    return users


def build_prompt(report_type, user_info, habits_data, start_date, end_date):
    nickname = user_info.get("nickname", f"{user_info['user_id']}님")
    age = user_info.get("age", "")
    occupation = user_info.get("occupation", "")
    characteristics = user_info.get("characteristics", [])

    # 전체 습관 성공률 계산
    total_attempts = 0
    total_successes = 0
    habit_summaries = []

    for habit in habits_data:
        habit_logs = habit["habit_log"]
        if habit_logs:
            attempts = len(habit_logs)
            successes = sum(1 for log in habit_logs if log.get("completed", False))
            success_rate = (successes / attempts * 100) if attempts > 0 else 0

            total_attempts += attempts
            total_successes += successes

            habit_summaries.append(f"- {habit['name']}: {successes}/{attempts} ({success_rate:.1f}%)")

    overall_success_rate = (total_successes / total_attempts * 100) if total_attempts > 0 else 0
    period_label = "최근 7일" if report_type == "weekly" else "최근 30일"
    intro = f"당신은 사용자 맞춤형 습관 코치입니다. 아래는 {nickname}의 {period_label}간 모든 습관 기록입니다."

    return f"""
{intro}
아래 JSON 스키마에 정확히 맞춰, **친근하고 따뜻한 말투**로 결과를 생성하세요. 요약 문장은 한국어 자연문으로 작성하되, 전체 출력은 하나의 JSON만 포함하세요.

반영 기간: {start_date} ~ {end_date}
전체 성공률(참고): {overall_success_rate:.1f}%

스키마:
{{
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "total_time": "총 수행시간(예: 8시간 30분)",
  "top_failure_reasons": [
    {{"habit": "습관명", "reasons": ["원인1", "원인2"]}}
  ],
  "summary": {{
    "per_habit_analysis": "각 습관별 성공률과 주요 성과 분석",
    "correlation_analysis": "자주 실패한 원인과 습관 간 상관관계 분석",
    "weekday_patterns": "요일별 전체 습관 성공/실패 패턴",
    "empathetic_message": "공감과 위로의 메세지"
  }},
  "recommendation": [
    {{
      "habit_id": 0,
      "name": "간결한 습관명 (사용자명 포함 금지, 예: 실내 스트레칭 15분)",
      "start_time": "HH:MM",
      "end_time": "HH:MM",
      "day_of_week": [1,2,3]
    }}
  ]
}}

지침:
- 총 수행시간은 습관 로그에 기반하여 추정해 요약하세요. 명시적 지속 시간이 없으면, 반복 빈도와 패턴을 근거로 보수적으로 표현하세요 (예: "약 6~8시간").
- 실패 원인은 습관별로 가장 빈도가 높은 상위 2개를 선택하세요.
- 추천은 실패 요인을 반영해 요일/시간/난이도를 조절해 주세요.
 - 추천의 name에는 사용자 이름/호칭을 포함하지 마세요. 간결하게 "행동 + 시간/횟수"만 작성하세요. (예: "팔굽혀펴기 10개", "실내 스트레칭 15분").

<사용자 정보>
이름: {nickname}
나이: {age}세
직업: {occupation}
특징: {', '.join(characteristics) if characteristics else '특별한 특징 없음'}

<습관별 요약>
{chr(10).join(habit_summaries)}

<{period_label}간 모든 습관 기록>
{json.dumps(habits_data, ensure_ascii=False, indent=2)}
"""


def _strip_code_fences(text: str) -> str:
    """```json ... ``` 같은 코드블록에 싸여오면 본문만 추출"""
    if "```json" in text:
        s = text.find("```json") + 7
        e = text.find("```", s)
        return text[s:e].strip() if e != -1 else text
    if "```" in text:
        s = text.find("```") + 3
        e = text.find("```", s)
        return text[s:e].strip() if e != -1 else text
    return text


def call_gemini(prompt):
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        resp = requests.post(API_URL, headers=headers, json=data, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    try:
        return resp.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini 응답 파싱 오류: {e}")


def generate_report_for_user(user_data, report_type, start_date, end_date):
    """LLM 호출 후 JSON 문자열 반환(네 원본 로직과 동일하게 JSON만 저장/반환)"""
    try:
        user_info = {
            "user_id": user_data["user_id"],
            "nickname": user_data["nickname"],
            "age": user_data.get("age"),
            "occupation": user_data.get("occupation"),
            "characteristics": user_data.get("characteristics", [])
        }

        habits_data = user_data["habits"]
        active_habits = [habit for habit in habits_data if habit.get("habit_log")]

        if active_habits:
            prompt = build_prompt(report_type, user_info, active_habits, start_date, end_date)
            return call_gemini(prompt)  # 모델 원문(보통 JSON)을 그대로 반환
        else:
            label = "최근 7일간" if report_type == "weekly" else "최근 30일간"
            return json.dumps({
                "start_date": start_date,
                "end_date": end_date,
                "error": f"{user_info['nickname']}님의 {label} 습관 기록이 없어 리포트를 생성할 수 없습니다."
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "start_date": start_date,
            "end_date": end_date,
            "error": f"리포트 생성 중 오류가 발생했습니다: {str(e)}"
        }, ensure_ascii=False)


# -----------------------------
# Pydantic 스키마
# -----------------------------
class HabitLog(BaseModel):
    date: str  # "YYYY-MM-DD"
    completed: bool
    note: Optional[str] = None

    @field_validator("date")
    def _validate_date(cls, v):
        datetime.strptime(v, "%Y-%m-%d")
        return v


class Habit(BaseModel):
    habit_id: str
    name: str
    description: Optional[str] = ""
    schedule: Optional[str] = ""
    habit_log: List[HabitLog] = Field(default_factory=list)


class UserData(BaseModel):
    user_id: str
    nickname: str
    age: Optional[int] = None
    occupation: Optional[str] = None
    characteristics: List[str] = Field(default_factory=list)
    habits: List[Habit]


class ReportRequest(BaseModel):
    report_type: str = Field(..., pattern="^(weekly|monthly)$")
    user: UserData
    start_date: Optional[str] = None  # "YYYY-MM-DD"
    end_date: Optional[str] = None    # "YYYY-MM-DD"


# -----------------------------
# FastAPI 앱
# -----------------------------
app = FastAPI(title="Habit Reports API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 필요 시 제한
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/report", summary="단일 사용자 리포트(JSON) 생성 및 반환")
def create_report(payload: ReportRequest):
    """
    - report_type: weekly|monthly
    - start_date/end_date 미지정 시, report_type 기준으로 자동 계산(오늘 포함 7/30일)
    - 반환: JSON 문자열(모델 원문). 서버가 파싱/정제하지 않고 그대로 반환.
    """
    report_type = payload.report_type

    # 날짜 자동 보정
    if payload.start_date and payload.end_date:
        start_date = payload.start_date
        end_date = payload.end_date
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    else:
        days = 7 if report_type == "weekly" else 30
        end = datetime.today().date()
        start = end - timedelta(days=days)
        start_date, end_date = str(start), str(end)

    # 생성
    raw = generate_report_for_user(
        user_data=payload.user.dict(),
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
    )

    # 모델이 코드블록에 감싸서 주는 경우 대비
    json_text = _strip_code_fences(raw)

    # 저장 (report_type 폴더에 user별 JSON 파일로 저장)
    out_dir = OUTPUT_DIRS[report_type]
    filename = f"user_{payload.user.user_id}_{payload.user.nickname}_{report_type}_report.json"
    path = os.path.join(out_dir, filename)

    # 가능한 경우 파싱하여 start/end 기본값 채우기
    try:
        obj = json.loads(json_text)
        obj.setdefault("start_date", start_date)
        obj.setdefault("end_date", end_date)
        with open(path, "w", encoding="utf-8") as jf:
            json.dump(obj, jf, ensure_ascii=False, indent=2)
        saved = True
        saved_body = obj
    except Exception:
        # 파싱 실패 시 원문 그대로 저장(에러/원문 포함)
        fallback = {
            "start_date": start_date,
            "end_date": end_date,
            "raw_response": raw,
            "note": "모델 응답 JSON 파싱 실패. raw_response에 원문 포함."
        }
        with open(path, "w", encoding="utf-8") as jf:
            json.dump(fallback, jf, ensure_ascii=False, indent=2)
        saved = False
        saved_body = fallback

    return {
        "report_type": report_type,
        "nickname": payload.user.nickname,
        "start_date": start_date,
        "end_date": end_date,
        "saved_path": path,
        "parsed_success": saved,
        "body": saved_body,
    }


@app.post("/reports/{report_type}/batch", summary="INPUT_DIR 기반 배치 생성 및 저장")
def batch_reports(
    report_type: str,
    background: bool = Query(False, description="True면 백그라운드 실행"),
    background_tasks: BackgroundTasks = None,
):
    """
    - INPUT_DIR의 *.json을 읽어 사용자별 최근 7/30일 데이터로 LLM 호출
    - 결과 JSON 파일을 outputs/{weekly|monthly}_report/에 저장
    """
    if report_type not in ("weekly", "monthly"):
        raise HTTPException(status_code=400, detail="report_type 은 weekly|monthly 중 하나여야 합니다.")

    def _run_batch():
        days = 7 if report_type == "weekly" else 30
        end = datetime.today().date()
        start = end - timedelta(days=days)
        start_date, end_date = str(start), str(end)

        users = group_users_by_id(days)
        out_dir = OUTPUT_DIRS[report_type]
        results = []

        for user_id, user_data in users.items():
            nickname = user_data["user_info"].get("nickname", str(user_id))
            active = [h for h in user_data["habits"] if h.get("habit_log")]

            filename = f"user_{user_id}_{nickname}_{report_type}_report.json"
            path = os.path.join(out_dir, filename)

            if not active:
                fallback = {
                    "start_date": start_date,
                    "end_date": end_date,
                    "error": "최근 데이터 없음",
                    "user_id": user_id,
                    "nickname": nickname,
                }
                with open(path, "w", encoding="utf-8") as jf:
                    json.dump(fallback, jf, ensure_ascii=False, indent=2)
                results.append({"user_id": user_id, "nickname": nickname, "saved_path": path, "parsed_success": True})
                continue

            try:
                raw = generate_report_for_user(
                    user_data={
                        "user_id": user_id,
                        "nickname": nickname,
                        "age": user_data["user_info"].get("age"),
                        "occupation": user_data["user_info"].get("occupation"),
                        "characteristics": user_data["user_info"].get("characteristics", []),
                        "habits": active,
                    },
                    report_type=report_type,
                    start_date=start_date,
                    end_date=end_date,
                )
                json_text = _strip_code_fences(raw)
                try:
                    obj = json.loads(json_text)
                    obj.setdefault("start_date", start_date)
                    obj.setdefault("end_date", end_date)
                    with open(path, "w", encoding="utf-8") as jf:
                        json.dump(obj, jf, ensure_ascii=False, indent=2)
                    results.append({"user_id": user_id, "nickname": nickname, "saved_path": path, "parsed_success": True})
                except Exception:
                    fallback = {
                        "start_date": start_date,
                        "end_date": end_date,
                        "raw_response": raw,
                        "note": "모델 응답 JSON 파싱 실패. raw_response에 원문 포함."
                    }
                    with open(path, "w", encoding="utf-8") as jf:
                        json.dump(fallback, jf, ensure_ascii=False, indent=2)
                    results.append({"user_id": user_id, "nickname": nickname, "saved_path": path, "parsed_success": False})
            except Exception as e:
                results.append({"user_id": user_id, "nickname": nickname, "error": str(e)})

        return {
            "status": "done",
            "report_type": report_type,
            "start_date": start_date,
            "end_date": end_date,
            "results": results
        }

    if background and background_tasks is not None:
        background_tasks.add_task(_run_batch)
        return {"status": "started", "report_type": report_type}
    else:
        return _run_batch()