# -*- coding: utf-8 -*-
"""
통합 리포트 생성기 (주간/월간 리포트 통합)

- 입력 데이터는 '단일 사용자 + habits 배열' 스키마만 지원합니다.
- 각 JSON 파일에 포함된 'type' 값(weekly/monthly)을 그대로 사용합니다.
- Gemini API에 JSON만 반환하도록 요청하고, 혹시 섞여오면 안전 파서로 JSON만 추출합니다.
- summary는 하나의 문자열이며, 줄바꿈(\\n)으로 4가지 섹션을 연결합니다.
- recommendation은 입력 습관 '각 항목당 1개'씩, 동일한 habit_id/입력 순서를 보장합니다.
"""

import os
import json
import re
from datetime import datetime, timedelta
import requests
from collections import Counter
from dotenv import load_dotenv

# ===== 환경 변수 / 경로 설정 =====
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

INPUT_DIR = "data"  # 통합 스키마 JSON 파일들이 위치한 폴더
OUTPUT_DIRS = {
    "weekly": "outputs/weekly_report",
    "monthly": "outputs/monthly_report",
}
for _d in OUTPUT_DIRS.values():
    os.makedirs(_d, exist_ok=True)


# ===== 시간 계산 유틸 =====
def parse_hhmm(s: str) -> datetime:
    """'HH:MM' -> 오늘 날짜 기준 datetime으로 파싱"""
    return datetime.combine(datetime.today().date(), datetime.strptime(s, "%H:%M").time())


def minutes_between(start_hhmm: str, end_hhmm: str) -> int:
    """HH:MM ~ HH:MM 사이 분(minute) 반환 (자정 넘김 미고려)"""
    delta = parse_hhmm(end_hhmm) - parse_hhmm(start_hhmm)
    return max(int(delta.total_seconds() // 60), 0)


def add_minutes(hhmm: str, delta_minutes: int) -> str:
    """HH:MM 에 분 단위 가감 적용"""
    return (parse_hhmm(hhmm) + timedelta(minutes=delta_minutes)).strftime("%H:%M")


def extract_last_days_logs(logs, days: int):
    """최근 N일 로그만 필터링"""
    today = datetime.today().date()
    from_date = today - timedelta(days=days)
    return [
        log for log in logs
        if from_date <= datetime.strptime(log["date"], "%Y-%m-%d").date() <= today
    ]


def minutes_filter_copy(habits, days: int):
    """습관 리스트를 days 기준으로 로그만 필터링하여 복사본 반환"""
    out = []
    for h in habits:
        out.append({
            "habit_id": h.get("habit_id"),
            "name": h.get("name"),
            "day_of_week": h.get("day_of_week", []),
            "start_time": h.get("start_time"),
            "end_time": h.get("end_time"),
            "habit_log": extract_last_days_logs(h.get("habit_log", []), days),
        })
    return out

# ===== 실패 사유 집계 유틸 =====
# 카테고리 라벨 
CATEGORY_LABELS = [
    "의지 부족",
    "건강 문제",
    "과도한 목표 설정",
    "시간 부족",
    "일정 충돌",
    "기타 (직접 입력)",
]

# 자유 텍스트 실패 사유 → 위 6개 중 하나로 매핑
CATEGORY_RULES = [
    # 의지 부족: 하기 싫음/미룸/동기 저하/귀찮음 등
    (r"(의욕\s*저하|동기\s*저하|하기\s*싫|미루|귀찮|의지\s*부족|싫어서|노트북\s*펴기\s*싫)", "의지 부족"),

    # 건강 문제: 피곤/수면/컨디션/통증/질병/생리 등
    (r"(피곤|피로|수면\s*부족|졸림|늦잠|알람\s*(못\s*들음|실패)|컨디션\s*저하|감기|두통|생리|근육통|통증|부상|과로|몸\s*상태\s*안좋)", "건강 문제"),

    # 과도한 목표 설정: 강도/시간/빈도 과함, 무리, 부담
    (r"(과도|무리|버겁|부담|강도\s*높|시간\s*너무\s*길|빈도\s*너무\s*잦|목표\s*크|빡세)", "과도한 목표 설정"),

    # 시간 부족: 바쁨/업무/숙제/알바/마감/준비/출근/등교/가사 일 등
    (r"(시간\s*부족|바쁨|바빠|업무|회사|과제|숙제|시험\s*공부|준비\s*하느라|마감|알바|출근|등교|가사|집안일)", "시간 부족"),

    # 일정 충돌: 외출 약속/행사/여행/스케줄 겹침/주말 루틴 붕괴
    (r"(일정\s*충돌|외출\s*일정|약속|행사|모임|여행|스케줄\s*겹|주말|공휴일)", "일정 충돌"),

    # 날씨 등 기타 사유는 '기타 (직접 입력)'로
    (r"(날씨|더움|추움|폭염|폭우|눈\s*옴|한파|비\s*옴)", "기타 (직접 입력)"),
]

def normalize_reason_category(text: str) -> str:
    """자유 텍스트 실패 사유를 6개 카테고리 중 하나로 정규화."""
    t = (text or "").strip().lower()
    if not t:
        return "기타 (직접 입력)"
    t = re.sub(r"\s+", " ", t)
    for pat, label in CATEGORY_RULES:
        if re.search(pat, t):
            return label
    return "기타 (직접 입력)"

def compute_per_habit_top_failure_reasons(active_habits, topk: int = 2):
    """
    각 습관별 실패 사유를 6개 카테고리로 정규화하여 집계 후, 상위 topk만 reasons로 반환.
    출력 형식:
    [
      { "habit_id": 2, "name": "평일 오전 운동 1시간", "reasons": ["수면 부족", "날씨 더움"] }  # ← 기존 포맷 유지
    ]
    주의: 이제 reasons에는 '카테고리 라벨'이 들어갑니다 (예: '의지 부족', '건강 문제', ...).
    """
    result = []
    for habit in active_habits:
        hid = habit.get("habit_id")
        name = habit.get("name") or ""
        logs = habit.get("habit_log", [])

        counter = Counter()
        for log in logs:
            if log.get("completed") is True:
                continue
            for raw in (log.get("failure_reason") or []):
                if not isinstance(raw, str):
                    continue
                label = normalize_reason_category(raw)
                counter[label] += 1

        # 상위 topk 카테고리만
        top_labels = [lbl for lbl, _ in counter.most_common(topk)]
        result.append({
            "habit_id": hid,
            "name": name,
            "reasons": top_labels
        })
    return result

# ===== LLM 프롬프트 구성 =====
def build_prompt(report_type: str, user_info: dict, habits_data: list, start_date: str, end_date: str) -> str:
    """
    Gemini에 전달할 프롬프트 문자열 생성
    - summary는 하나의 문자열(줄바꿈 \\n)
    - recommendation은 입력 습관 각 항목당 1개, 동일 habit_id/순서 유지
    """
    nickname = user_info.get("nickname") or f"{user_info.get('user_id')}님"

    # 요약 집계
    total_attempts = 0
    total_successes = 0
    habit_summaries = []
    habit_specs = []

    for habit in habits_data:
        logs = habit.get("habit_log", [])
        attempts = len(logs)
        successes = sum(1 for log in logs if log.get("completed"))
        total_attempts += attempts
        total_successes += successes
        success_rate = (successes / attempts * 100) if attempts else 0.0

        sched_bits = []
        if habit.get("start_time") and habit.get("end_time"):
            sched_bits.append(f"{habit['start_time']}~{habit['end_time']}")
        if habit.get("day_of_week"):
            sched_bits.append(f"DOW={habit['day_of_week']}")
        sched_str = f" ({', '.join(sched_bits)})" if sched_bits else ""

        habit_summaries.append(
            f"- [{habit['habit_id']}] {habit['name']}{sched_str}: {successes}/{attempts} ({success_rate:.1f}%)"
        )
        habit_specs.append({
            "habit_id": habit.get("habit_id"),
            "name": habit.get("name"),
            "start_time": habit.get("start_time"),
            "end_time": habit.get("end_time"),
            "day_of_week": habit.get("day_of_week", []),
        })

    overall_success_rate = (total_successes / total_attempts * 100) if total_attempts else 0.0
    period_label = "최근 7일" if report_type == "weekly" else "최근 30일"

    intro = f"당신은 사용자 맞춤형 습관 코치입니다. 아래는 {nickname}의 {period_label}간 모든 습관 기록입니다."

    # 프롬프트 본문 (total_time 관련 요소 제거)
    return f"""
{intro}
아래 JSON 스키마에 정확히 맞춰, **친근하고 따뜻한 말투**로 결과를 생성하세요. 요약 문장은 한국어 자연문으로 작성하되, 전체 출력은 하나의 JSON만 포함하세요.

반영 기간: {start_date} ~ {end_date}
전체 성공률(참고): {overall_success_rate:.1f}%

<입력 습관 목록(순서 유지, 각 항목당 추천 1개 필수)>
{json.dumps(habit_specs, ensure_ascii=False, indent=2)}

스키마:
{{
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  // top_failure_reasons 내부의 "reasons"는 입력에 존재하는 사유를 바탕으로 원인을 작성하세요.
  "top_failure_reasons": [
    {{
        "habit_id": {habit_specs[0]['habit_id'] if habit_specs else 0},
        "name": "간결한 습관명 (예: 실내 스트레칭 15분)",
        "reasons": ["원인1", "원인2"]
        }}
  ],
  // summary는 하나의 문자열이며, 줄바꿈(\\n)으로 아래 4개의 내용을 순서대로 이어붙여 주세요.
  // 1) 각 습관별 성공률(퍼센트, 수행 횟수/목표 횟수)과 주요 성과 분석
  // 2) 자주 실패한 원인과 습관 간 상관관계 분석
  // 3) 요일별 전체 습관 성공/실패 패턴
  // 4) 공감과 위로의 메세지
  "summary": "각 습관별 성공률과 주요 성과 분석\\n자주 실패한 원인과 습관 간 상관관계 분석\\n요일별 전체 습관 성공/실패 패턴\\n공감과 위로의 메세지",

  // ⚠️ recommendation은 입력 습관 목록의 '각 항목'에 대해 '정확히 1개씩' 생성하세요.
  // ⚠️ 각 항목의 habit_id는 반드시 해당 입력 습관의 habit_id와 '동일'해야 합니다.
  // ⚠️ recommendation 배열의 항목 순서는 입력 습관 목록의 순서를 그대로 따라야 합니다.
  // ⚠️ 기존 습관을 수정할 필요가 없는 경우 recommendation에서 습관명이나 시간대를 변경하지 마세요.
  "recommendation": [
    {{
      "habit_id": {habit_specs[0]['habit_id'] if habit_specs else 0},
      "name": "간결한 습관명 (예: 실내 스트레칭 15분)",
      "start_time": "HH:MM",
      "end_time": "HH:MM",
      "day_of_week": [1,2,3]
    }}
    // 나머지 항목도 동일한 형식으로, 입력 목록 길이에 맞춰 총 {len(habit_specs)}개 생성
  ]
}}

지침:
- 'top_failure_reasons'는 습관별로 가장 빈도가 높은 상위 2개를 선택하고, 입력에 존재하는 사유를 바탕으로 원인을 작성하세요.
- 'summary'는 하나의 문자열이어야 하며, 4개의 내용을 줄바꿈(\\n)으로 구분해 주세요.
- 'recommendation'은 실패 요인을 반영해 요일/시간/난이도를 조절하세요.
- 추천의 name에는 사용자 이름/호칭을 포함하지 말고, "행동 + 시간/횟수"만 간결히 작성하세요.
- **recommendation은 입력 습관의 개수와 동일한 개수로 생성하고, 각 항목의 habit_id는 해당 입력 습관의 habit_id와 일치시키세요.**
- **recommendation 배열의 항목 순서는 반드시 입력 습관 배열의 순서를 따르세요.**

<사용자 정보>
이름: {nickname}
출생연도: {user_info.get('birth_year')}
성별: {user_info.get('gender')}
직업: {user_info.get('job')}

<습관별 요약>
{chr(10).join(habit_summaries)}
"""


# ===== LLM 호출 / JSON 안전 추출 =====
def call_gemini(prompt: str):
    """Gemini API 호출 - JSON 응답 강제"""
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    try:
        resp = requests.post(API_URL, headers=headers, json=data, timeout=60)
    except requests.exceptions.RequestException as e:
        return f"[API 요청 오류]: {e}"

    if resp.status_code != 200:
        return f"[API 오류]: {resp.status_code} - {resp.text}"

    # 대부분 candidates[0].content.parts[0].text에 JSON string이 담김
    try:
        j = resp.json()
        if "candidates" in j and j["candidates"]:
            parts = j["candidates"][0].get("content", {}).get("parts", [])
            if parts and "text" in parts[0]:
                return parts[0]["text"]
        # 혹시 모델이 JSON 오브젝트를 직접 루트로 준 경우 문자열화
        return json.dumps(j, ensure_ascii=False)
    except Exception as e:
        return f"[파싱 오류]: {e}\n[raw]: {resp.text[:2000]}"


def extract_json_safely(text: str) -> str:
    """
    모델 응답에서 JSON만 안전 추출:
    - ```json ... ``` 코드블록
    - ``` ... ``` 일반 코드블록
    - 첫 '{'부터 중괄호 밸런싱으로 끝 '}' 추출
    실패 시 원문 그대로 반환
    """
    if not isinstance(text, str):
        return text

    # ```json 블록
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # ``` 일반 블록
    m = re.search(r"```\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # 중괄호 밸런싱
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1].strip()

    return text.strip()


# ===== 메인 로직 =====
def main():
    # data/ 폴더의 각 파일(통합 스키마) 처리
    any_output = False
    for filename in os.listdir(INPUT_DIR):
        if not filename.endswith(".json"):
            continue

        path = os.path.join(INPUT_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"⚠️ {filename}: JSON 로드 실패 - {e}")
            continue

        # 통합 스키마 전제
        try:
            user_id = data["user_id"]
            nickname = data.get("nickname", str(user_id))
            report_type = (data.get("type") or "monthly").lower()
            if report_type not in ("weekly", "monthly"):
                print(f"⚠️ {filename}: 알 수 없는 type='{report_type}', monthly로 대체")
                report_type = "monthly"

            filter_days = 7 if report_type == "weekly" else 30
            output_dir = OUTPUT_DIRS[report_type]

            # 기간 필터링 복사본 생성
            habits_all = data.get("habits", [])
            active_habits = [h for h in minutes_filter_copy(habits_all, filter_days) if h.get("habit_log")]
            if not active_habits:
                print(f"⚠️ {nickname}: 최근 데이터 없음 ({report_type} 리포트 건너뜀)")
                continue

            end_date = datetime.today().date()
            start_date = end_date - timedelta(days=filter_days)

            user_info = {
                "user_id": user_id,
                "nickname": nickname,
                "birth_year": data.get("birth_year"),
                "gender": data.get("gender"),
                "job": data.get("job"),
            }

            # 프롬프트 생성 및 호출
            prompt = build_prompt(report_type, user_info, active_habits, str(start_date), str(end_date))
            print(f"📡 {nickname}의 종합 {report_type} 리포트 LLM 호출 중...")
            response = call_gemini(prompt)

            # JSON 파싱
            json_text = extract_json_safely(response)
            try:
                parsed = json.loads(json_text)
                parsed.setdefault("start_date", str(start_date))
                parsed.setdefault("end_date", str(end_date))

                # ===== 추천 검증/보정: 각 입력 습관당 1개 보장 + habit_id 일치 + 순서 동일 =====
                valid_ids = [h.get("habit_id") for h in active_habits]  # 입력 순서 보존
                valid_id_set = set(valid_ids)
                name_by_id = {h.get("habit_id"): h.get("name") for h in active_habits}

                recs = parsed.get("recommendation", [])
                if not isinstance(recs, list):
                    recs = []

                # 1) 잘못된 habit_id 자동 보정 (이름 정확 일치 시 맵핑, 아니면 첫 id)
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

                # 2) 각 입력 습관에 대해 최소 1개 추천 존재 보장 (누락분 생성)
                existing_by_id = {}
                for rec in recs:
                    rid = rec.get("habit_id")
                    if rid in valid_id_set and rid not in existing_by_id:
                        existing_by_id[rid] = rec

                for hid in valid_ids:
                    if hid in existing_by_id:
                        continue
                    # 기본 개선안: 종료 15분 단축(최소 10분)
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

                # 3) 입력 순서대로 정렬(동일 habit_id 다수면 첫 번째만)
                recs_sorted = []
                for hid in valid_ids:
                    first = next((r for r in recs if r.get("habit_id") == hid), None)
                    if first:
                        recs_sorted.append(first)
                parsed["recommendation"] = recs_sorted
                # ===========================================================

                # 저장
                os.makedirs(output_dir, exist_ok=True)
                json_name = f"user_{user_id}_{nickname}_{report_type}_report.json"
                json_path = os.path.join(output_dir, json_name)
                with open(json_path, "w", encoding="utf-8") as jf:
                    json.dump(parsed, jf, ensure_ascii=False, indent=2)
                print(f"✅ JSON 저장 완료: {json_path}")
                any_output = True

            except Exception as e:
                # 파싱 실패 시 raw 저장
                os.makedirs(output_dir, exist_ok=True)
                raw_dump = os.path.join(output_dir, f"_raw_{user_id}_{nickname}_{report_type}.txt")
                with open(raw_dump, "w", encoding="utf-8") as rf:
                    rf.write(response if isinstance(response, str) else str(response))
                fallback = {
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                    "error": f"JSON 파싱 실패: {e}",
                    "raw_response_path": raw_dump,
                }
                json_name = f"user_{user_id}_{nickname}_{report_type}_report.json"
                json_path = os.path.join(output_dir, json_name)
                with open(json_path, "w", encoding="utf-8") as jf:
                    json.dump(fallback, jf, ensure_ascii=False, indent=2)
                print(f"⚠️ 모델 응답 파싱 실패. 원문 경로: {raw_dump}\n⚠️ Fallback JSON 저장: {json_path}")
                any_output = True

        except KeyError as e:
            print(f"⚠️ {filename}: 필수 키 누락 - {e}")

    if not any_output:
        print("⚠️ 생성 가능한 사용자 데이터가 없습니다.")


if __name__ == "__main__":
    main()