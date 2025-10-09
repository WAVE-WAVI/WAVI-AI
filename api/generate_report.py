# -*- coding: utf-8 -*-
"""
통합 리포트 생성기 (주간/월간 리포트 통합)
- 꾸준함 지수 + 아이콘 + 월간 구조화 summary
- 각 실패 사유별 {reason, icon} 구조 출력
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

INPUT_DIR = "data"
OUTPUT_DIRS = {
    "weekly": "outputs/weekly_report",
    "monthly": "outputs/monthly_report",
}
for _d in OUTPUT_DIRS.values():
    os.makedirs(_d, exist_ok=True)

# ===== 꾸준함 지수 및 이모지 매핑 =====
CONSISTENCY_THRESHOLDS = {"high": 70, "medium": 40}
REASON_ICON_MAP = {
    "의지 부족": "😩",
    "건강 문제": "🤒",
    "과도한 목표 설정": "🎯",
    "시간 부족": "⏰",
    "일정 충돌": "📅",
    "기타 (직접 입력)": "💬",
}

# ===== 자유 입력 → 이모지 추론 =====
def guess_emoji_from_text(text: str) -> str:
    t = (text or "").lower().strip()
    if re.search(r"(근육|통증|운동|아파|피로|몸|스트레칭)", t): return "💪"
    if re.search(r"(비|rain|장마|우산)", t): return "🌧️"
    if re.search(r"(더움|hot|heat|폭염|덥)", t): return "☀️"
    if re.search(r"(추움|cold|snow|한파|춥)", t): return "🥶"
    if re.search(r"(피곤|졸|수면|컨디션|sleep|tired)", t): return "😴"
    if re.search(r"(공부|시험|숙제|과제|project|work)", t): return "📚"
    if re.search(r"(약속|친구|모임|행사|파티|데이트|만남)", t): return "🧑‍🤝‍🧑"
    if re.search(r"(지각|시간|스케줄|늦|출근|등교)", t): return "⏰"
    if re.search(r"(우울|기분|짜증|화|sad|depress)", t): return "😞"
    return "💬"

# ===== 시간 유틸 =====
def parse_hhmm(s: str):
    return datetime.combine(datetime.today().date(), datetime.strptime(s, "%H:%M").time())

def minutes_between(start_hhmm: str, end_hhmm: str) -> int:
    delta = parse_hhmm(end_hhmm) - parse_hhmm(start_hhmm)
    return max(int(delta.total_seconds() // 60), 0)

def extract_last_days_logs(logs, days: int):
    today = datetime.today().date()
    from_date = today - timedelta(days=days)
    return [log for log in logs if from_date <= datetime.strptime(log["date"], "%Y-%m-%d").date() <= today]

def minutes_filter_copy(habits, days: int):
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

# ===== 실패 사유 정규화 =====
CATEGORY_RULES = [
    (r"(의욕|동기\s*저하|하기\s*싫|미루|귀찮|의지\s*부족)", "의지 부족"),
    (r"(피곤|수면\s*부족|졸림|늦잠|알람|컨디션|감기|두통|통증|과로)", "건강 문제"),
    (r"(과도|무리|버겁|부담|강도\s*높|시간\s*너무\s*길|빈도\s*잦|목표\s*크)", "과도한 목표 설정"),
    (r"(시간\s*부족|바쁨|업무|회사|과제|시험|마감|알바|출근|등교|집안일)", "시간 부족"),
    (r"(일정\s*충돌|외출|약속|행사|모임|여행|주말|공휴일)", "일정 충돌"),
    (r"(날씨|더움|추움|폭염|폭우|눈|한파|비)", "기타 (직접 입력)"),
]

def normalize_reason_category(text: str) -> str:
    t = (text or "").strip().lower()
    for pat, label in CATEGORY_RULES:
        if re.search(pat, t):
            return label
    return "기타 (직접 입력)"

# ===== 꾸준함 지수 계산 =====
def compute_overall_success_rate(habits):
    attempts, successes = 0, 0
    for h in habits:
        logs = h.get("habit_log", [])
        attempts += len(logs)
        successes += sum(1 for log in logs if log.get("completed"))
    return (successes / attempts * 100) if attempts else 0.0

def consistency_level_from_rate(rate: float) -> str:
    if rate >= CONSISTENCY_THRESHOLDS["high"]:
        return "높음"
    if rate >= CONSISTENCY_THRESHOLDS["medium"]:
        return "보통"
    return "낮음"

# ===== 실패 사유 집계 =====
def compute_per_habit_top_failure_reasons(active_habits, topk: int = 2):
    result = []
    for habit in active_habits:
        hid = habit.get("habit_id")
        name = habit.get("name") or ""
        logs = habit.get("habit_log", [])
        counter = Counter()
        user_texts_for_etc = []

        # 사유 수집
        for log in logs:
            if log.get("completed"):
                continue
            for raw in (log.get("failure_reason") or []):
                if not isinstance(raw, str) or not raw.strip():
                    continue
                label = normalize_reason_category(raw)
                counter[label] += 1
                if label == "기타 (직접 입력)":
                    user_texts_for_etc.append(raw.strip())

        top_labels = [lbl for lbl, _ in counter.most_common(topk)]

        # 기타 직접입력 → 원문 치환
        final_reasons = []
        if "기타 (직접 입력)" in top_labels and user_texts_for_etc:
            most_common_texts = [t for t, _ in Counter(user_texts_for_etc).most_common(topk)]
            for lbl in top_labels:
                if lbl == "기타 (직접 입력)":
                    final_reasons.extend(most_common_texts)
                else:
                    final_reasons.append(lbl)
        else:
            final_reasons = top_labels

        final_reasons = final_reasons[:topk]

        # 각 이유별 이모지
        reasons_with_icon = []
        for reason in final_reasons:
            normalized = normalize_reason_category(reason)
            icon = guess_emoji_from_text(reason) if normalized == "기타 (직접 입력)" else REASON_ICON_MAP.get(normalized, "💬")
            reasons_with_icon.append({"reason": reason, "icon": icon})

        result.append({
            "habit_id": hid,
            "name": name,
            "reasons": reasons_with_icon,
        })

    return result

# ===== 상위 실패 사유 평탄화 =====
def flatten_reasons_from_top_fail(failure_data):
    out = []
    for h in (failure_data or []):
        for r in h.get("reasons", []):
            if isinstance(r, dict) and "reason" in r:
                val = (r.get("reason") or "").strip()
                if val:
                    out.append(val)
    return out

# ===== 월간 Summary 생성 =====
def generate_monthly_summary(nickname, habits, failure_data):
    # 1) 주요 성과
    success_rates = []
    for h in habits:
        logs = h.get("habit_log", []) or []
        if not logs:
            continue
        success_rate = sum(1 for l in logs if l.get("completed")) / len(logs) * 100
        success_rates.append((h.get("name") or "습관", success_rate))
    if success_rates:
        best_habit, best_rate = max(success_rates, key=lambda x: x[1])
        consistency = f"{best_habit}도 바쁜 한 달 속에서 {best_rate:.0f}%나 해냈다는 건 {nickname}님의 꾸준함이 돋보입니다."
    else:
        consistency = "이번 달은 새로운 시작을 위한 준비 기간이었어요."

    # 2) 주요 실패 원인
    all_reasons = flatten_reasons_from_top_fail(failure_data)
    if all_reasons:
        most_common_reason, _ = Counter(all_reasons).most_common(1)[0]
        normalized = normalize_reason_category(most_common_reason)
        if normalized == "기타 (직접 입력)":
            top_texts = [txt for txt, _ in Counter(all_reasons).most_common(2)]
            icon = guess_emoji_from_text(top_texts[0]) if top_texts else "💬"
            if len(top_texts) == 1:
                failure_reasons = f"이번 달엔 {icon} 직접 입력된 사유가 많았어요. 예: \"{top_texts[0]}\""
            else:
                failure_reasons = f"이번 달엔 {icon} 직접 입력된 사유가 많았어요. 예: \"{top_texts[0]}\" / \"{top_texts[1]}\""
        else:
            icon = REASON_ICON_MAP.get(normalized, "💬")
            failure_reasons = f"이번 달 가장 자주 등장한 방해 요인은 {icon} '{most_common_reason}'이에요."
    else:
        failure_reasons = "이번 달은 큰 방해 없이 잘 이어졌어요."

    # 3) 요일 패턴
    weekday_success, weekday_total = Counter(), Counter()
    for h in habits:
        for log in h.get("habit_log", []) or []:
            try:
                day = datetime.strptime(log["date"], "%Y-%m-%d").weekday()
            except Exception:
                continue
            weekday_total[day] += 1
            if log.get("completed"):
                weekday_success[day] += 1
    if weekday_total:
        rate_by_day = {d: (weekday_success[d] / weekday_total[d]) for d in weekday_total}
        best_day = max(rate_by_day, key=rate_by_day.get)
        worst_day = min(rate_by_day, key=rate_by_day.get)
        day_map = ["월", "화", "수", "목", "금", "토", "일"]
        daily_pattern = f"{day_map[best_day]}요일엔 리듬이 좋고, {day_map[worst_day]}요일엔 약간 느슨했어요."
    else:
        daily_pattern = "요일별 패턴을 확인할 데이터가 부족했어요."

    courage = "작은 꾸준함이 쌓여 결국 큰 변화를 만들어냅니다. 다음 달에도 응원할게요!"

    return {
        "consistency": consistency,
        "failure_reasons": failure_reasons,
        "daily_pattern": daily_pattern,
        "courage": courage
    }

# ===== 메인 로직 =====
def main():
    any_output = False
    for filename in os.listdir(INPUT_DIR):
        if not filename.endswith(".json"):
            continue

        path = os.path.join(INPUT_DIR, filename)
        try:
            data = json.load(open(path, "r", encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ JSON 로드 실패: {filename} - {e}")
            continue

        user_id = data["user_id"]
        nickname = data.get("nickname", str(user_id))
        report_type = (data.get("type") or "monthly").lower()
        if report_type not in ("weekly", "monthly"):
            report_type = "monthly"

        filter_days = 7 if report_type == "weekly" else 30
        output_dir = OUTPUT_DIRS[report_type]
        habits_all = data.get("habits", [])
        active_habits = [h for h in minutes_filter_copy(habits_all, filter_days) if h.get("habit_log")]
        if not active_habits:
            continue

        end_date = datetime.today().date()
        start_date = end_date - timedelta(days=filter_days)
        parsed = {"start_date": str(start_date), "end_date": str(end_date)}

        # 실패 사유 (상위 2개)
        top_fail = compute_per_habit_top_failure_reasons(active_habits, topk=2)
        parsed["top_failure_reasons"] = top_fail

        # 월간 summary + 꾸준함 지수
        if report_type == "monthly":
            rate = compute_overall_success_rate(active_habits)
            level = consistency_level_from_rate(rate)
            parsed["consistency_index"] = {
                "success_rate": round(rate, 1),
                "level": level,
                "thresholds": CONSISTENCY_THRESHOLDS,
                "display_message": f"꾸준함 지수: {level}" + (" 🔥" if level == "높음" else (" 🙂" if level == "보통" else " 🌧️"))
            }
            parsed["summary"] = generate_monthly_summary(nickname, active_habits, top_fail)

        # 저장
        os.makedirs(output_dir, exist_ok=True)
        json_path = os.path.join(output_dir, f"user_{user_id}_{nickname}_{report_type}_report.json")
        json.dump(parsed, open(json_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"✅ 저장 완료: {json_path}")
        any_output = True

    if not any_output:
        print("⚠️ 생성 가능한 데이터가 없습니다.")

if __name__ == "__main__":
    main()