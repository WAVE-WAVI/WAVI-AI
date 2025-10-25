# -*- coding: utf-8 -*-
"""
통합 리포트 생성기 (주간/월간 리포트 통합)
- 꾸준함 지수 + 아이콘 + summary + recommendation (weekly/monthly 공통 구조)
- summary 4문장에 BJ Fogg 행동모델(B=MAP) 톤 반영 (spark/facilitator/signal)
"""

import os
import json
import re
from datetime import datetime, timedelta
from collections import Counter
from dotenv import load_dotenv

# ===== 환경 변수 / 경로 설정 =====
load_dotenv()
INPUT_DIR = "data"
OUTPUT_DIRS = {
    "weekly": "outputs/weekly_report",
    "monthly": "outputs/monthly_report",
}
for _d in OUTPUT_DIRS.values():
    os.makedirs(_d, exist_ok=True)

# ===== 꾸준함 지수 / 이모지 매핑 =====
CONSISTENCY_THRESHOLDS = {"high": 70, "medium": 40}
REASON_ICON_MAP = {
    "의지 부족": "😩",
    "건강 문제": "🤒",
    "과도한 목표 설정": "🎯",
    "시간 부족": "⏰",
    "일정 충돌": "📅",
    "기타 (직접 입력)": "💬",
}

# ===== 자유입력 이모지 추론 =====
def guess_emoji_from_text(text: str) -> str:
    t = (text or "").lower().strip()
    if re.search(r"(근육|통증|운동|피로|몸|스트레칭)", t): return "💪"
    if re.search(r"(비|rain|장마|우산)", t): return "🌧️"
    if re.search(r"(더움|hot|heat|폭염|덥)", t): return "☀️"
    if re.search(r"(추움|cold|snow|한파|춥)", t): return "🥶"
    if re.search(r"(피곤|졸|수면|컨디션|sleep|tired)", t): return "😴"
    if re.search(r"(공부|시험|숙제|과제|project|work)", t): return "📚"
    if re.search(r"(약속|친구|모임|행사|데이트|만남)", t): return "🧑‍🤝‍🧑"
    if re.search(r"(지각|시간|늦|출근|등교)", t): return "⏰"
    if re.search(r"(우울|기분|짜증|화|sad|depress)", t): return "😞"
    return "💬"

# ===== 시간 유틸 =====
def parse_hhmm(s: str):
    """
    입력: 'HH:MM' 또는 'HH:MM:SS' 모두 허용
    반환: 오늘 날짜의 datetime (시간/분만 사용)
    """
    t = (s or "").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            tm = datetime.strptime(t, fmt).time()
            # seconds는 버리고 분 단위까지만 사용
            tm = tm.replace(second=0, microsecond=0)
            return datetime.combine(datetime.today().date(), tm)
        except ValueError:
            continue

    # 여전히 실패하면 콜론 분해로 최후 시도
    try:
        parts = [int(p) for p in t.split(":")]
        if len(parts) >= 2:
            hh, mm = parts[0], parts[1]
            return datetime.combine(datetime.today().date(),
                                    datetime.strptime(f"{hh:02d}:{mm:02d}", "%H:%M").time())
    except Exception:
        pass
    raise ValueError(f"Invalid time format: {s!r} (expected HH:MM or HH:MM:SS)")

def normalize_hhmm(s: str) -> str:
    """'HH:MM' 또는 'HH:MM:SS' -> 항상 'HH:MM'"""
    dt = parse_hhmm(s)
    return dt.strftime("%H:%M")

def minutes_between(start_hhmm: str, end_hhmm: str) -> int:
    delta = parse_hhmm(end_hhmm) - parse_hhmm(start_hhmm)
    return max(int(delta.total_seconds() // 60), 0)

def add_minutes(hhmm: str, delta: int) -> str:
    return (parse_hhmm(hhmm) + timedelta(minutes=delta)).strftime("%H:%M")

def extract_all_logs(logs):
    """Return all logs without date filtering"""
    return logs

def minutes_filter_copy(habits):
    """Copy habits with all logs, no date filtering"""
    out = []
    for h in habits:
        out.append({
            "habit_id": h.get("habit_id"),
            "name": h.get("name"),
            "day_of_week": h.get("day_of_week", []),
            "start_time": h.get("start_time"),
            "end_time": h.get("end_time"),
            "habit_log": extract_all_logs(h.get("habit_log", [])),
        })
    return out

# ===== 실패 사유 정규화 =====
CATEGORY_RULES = [
    (r"(의욕|동기|하기\s*싫|미루|귀찮|의지\s*부족)", "의지 부족"),
    (r"(피곤|수면|졸림|늦잠|알람|컨디션|감기|통증|과로)", "건강 문제"),
    (r"(과도|무리|버겁|부담|강도\s*높|시간\s*길|빡세)", "과도한 목표 설정"),
    (r"(시간\s*부족|바쁨|업무|과제|시험|마감|출근|등교)", "시간 부족"),
    (r"(일정\s*충돌|외출|약속|모임|여행|주말|공휴일)", "일정 충돌"),
    (r"(날씨|더움|추움|비|폭염|폭우|한파|우울|기분|짜증|화)", "기타 (직접 입력)"),
]
def normalize_reason_category(text: str) -> str:
    t = (text or "").lower().strip()
    for pat, label in CATEGORY_RULES:
        if re.search(pat, t):
            return label
    return "기타 (직접 입력)"

# ===== 지수 계산 =====
def compute_overall_success_rate(habits):
    total, success = 0, 0
    for h in habits:
        logs = h.get("habit_log", [])
        total += len(logs)
        success += sum(1 for l in logs if l.get("completed"))
    return (success / total * 100) if total else 0.0

def consistency_level_from_rate(rate):
    if rate >= CONSISTENCY_THRESHOLDS["high"]: return "높음"
    if rate >= CONSISTENCY_THRESHOLDS["medium"]: return "보통"
    return "낮음"

# ===== 실패 사유 집계 =====
def compute_per_habit_top_failure_reasons(active_habits, topk=2):
    result = []
    for h in active_habits:
        hid = h.get("habit_id")
        name = h.get("name") or ""
        logs = h.get("habit_log", [])
        counter, user_texts = Counter(), []
        for log in logs:
            if log.get("completed"): continue
            for raw in (log.get("failure_reason") or []):
                label = normalize_reason_category(raw)
                counter[label] += 1
                if label == "기타 (직접 입력)": user_texts.append(raw.strip())
        top_labels = [lbl for lbl, _ in counter.most_common(topk)]
        final_reasons = []
        if "기타 (직접 입력)" in top_labels and user_texts:
            user_texts = [t for t, _ in Counter(user_texts).most_common(topk)]
            for lbl in top_labels:
                if lbl == "기타 (직접 입력)": final_reasons.extend(user_texts)
                else: final_reasons.append(lbl)
        else:
            final_reasons = top_labels[:topk]
        reasons = []
        for r in final_reasons:
            norm = normalize_reason_category(r)
            icon = guess_emoji_from_text(r) if norm == "기타 (직접 입력)" else REASON_ICON_MAP.get(norm, "💬")
            reasons.append({"reason": r, "icon": icon})
        result.append({"habit_id": hid, "name": name, "reasons": reasons})
    return result

# ===== MAP 진단 유틸 (요약 카피용, 최소 변경) =====
def _collect_fail_labels_from_habits(habits):
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
    rate = (success / total) if total else 0.0
    return labels, rate

def infer_overall_map_state(habits, overall_rate: float):
    """
    habits와 전체 성공률로 동기/능력 상태를 간단히 추정해
    요약 카피 톤을 결정(spark/facilitator/signal)하는 휴리스틱.
    """
    labels, _ = _collect_fail_labels_from_habits(habits)
    c = Counter(labels)

    # Ability 낮음 신호: 시간/일정/과도 목표
    ability_low = c["시간 부족"] + c["일정 충돌"] + c["과도한 목표 설정"]
    # Motivation 낮음 신호: 의지 부족
    motivation_low = c["의지 부족"]

    ability = "low" if ability_low >= 2 else ("medium" if ability_low == 1 else "high")
    # 성공률이 많이 낮으면 동기 저하로 가정
    motivation = "low" if motivation_low >= 2 else ("medium" if motivation_low == 1 else ("low" if overall_rate < 30 else "high"))

    if motivation == "low":
        prompt = "spark"         # 의지 불붙이기
    elif ability == "low":
        prompt = "facilitator"   # 난이도/복잡도 낮추기
    else:
        prompt = "signal"        # 조용한 '지금 시작' 신호

    return {"motivation": motivation, "ability": ability, "prompt_type": prompt}

# ===== summary 생성 (B=MAP 카피 반영) =====
def flatten_reasons_from_top_fail(failure_data):
    out = []
    for h in (failure_data or []):
        for r in h.get("reasons", []):
            if isinstance(r, list) and r: out.append(r[0])
    return out

def generate_summary(nickname, habits, failure_data, rate):
    """
    주간/월간 공통 summary 생성
    - success_rate(전체 꾸준함 지수) + B=MAP(프롬프트 톤) 반영 카피
    - 출력: {consistency, failure_reasons, daily_pattern, courage}
    """
    # MAP 상태 추정(요약 카피 톤 결정)
    map_state = infer_overall_map_state(habits, rate)  # {'motivation','ability','prompt_type'}
    pt = map_state["prompt_type"]

    # 1️⃣ 꾸준함 문장 (+ 프롬프트 톤 꼬리문장)
    consistency = (
        f"바쁜 기간 속에서 {rate:.1f}%나 해냈다는 건 {nickname}님의 꾸준함이 돋보입니다."
        if rate > 40 else
        "이번 기간은 새로운 시작을 위한 준비 기간이었어요."
    )

    # 2️⃣ 주요 실패 원인 (필요 시 Tiny 제안 한 줄)
    all_reasons = flatten_reasons_from_top_fail(failure_data)
    if all_reasons:
        most_common, _ = Counter(all_reasons).most_common(1)[0]
        norm = normalize_reason_category(most_common)
        if norm == "기타 (직접 입력)":
            icon = guess_emoji_from_text(most_common)
            failure_reasons = f"{icon} 직접 입력된 사유가 많았어요. 예: \"{most_common}\""
        else:
            icon = REASON_ICON_MAP.get(norm, "💬")
            failure_reasons = f"가장 자주 등장한 방해 요인은 {icon} '{most_common}'이에요."
        # Ability 낮음(퍼실리테이터)일 땐 '가벼운 대안'을 짧게 제안
        if pt == "facilitator":
            failure_reasons += " → 이번 주는 '5분만/한 단계만'으로 가볍게 시작해봐요."
    else:
        failure_reasons = "이번 기간은 큰 방해 없이 잘 이어졌어요."

    # 3️⃣ 요일 패턴 (그대로)
    weekday_success, weekday_total = Counter(), Counter()
    for h in habits:
        for log in h.get("habit_log", []):
            try: day = datetime.strptime(log["date"], "%Y-%m-%d").weekday()
            except Exception: continue
            weekday_total[day] += 1
            if log.get("completed"): weekday_success[day] += 1

    if weekday_total:
        rate_by_day = {d: weekday_success[d] / weekday_total[d] for d in weekday_total}
        best_day = max(rate_by_day, key=rate_by_day.get)
        worst_day = min(rate_by_day, key=rate_by_day.get)
        days = ["월", "화", "수", "목", "금", "토", "일"]
        daily_pattern = f"{days[best_day]}요일엔 리듬이 좋고, {days[worst_day]}요일엔 약간 느슨했어요."
    else:
        daily_pattern = "요일별 패턴을 확인할 데이터가 부족했어요."

    # 4️⃣ 응원 문장(프롬프트 톤별 카피)
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

# ===== recommendation 생성 (원본 유지) =====
def generate_recommendations(habits):
    recs = []
    for h in habits:
        logs = h.get("habit_log", [])
        total = len(logs)
        fails = sum(1 for l in logs if not l.get("completed"))
        rate = (total - fails) / total * 100 if total else 0
        start, end = h.get("start_time") or "07:00", h.get("end_time") or "07:30"
        name = h.get("name") or "습관"
        if rate < 50:
            new_end = add_minutes(start, max(15, minutes_between(start, end) - 15))
            name = name + " (가벼운 버전)"
        elif rate >= 80:
            new_end = end
            name = name + " (유지)"
        else:
            new_end = end
        recs.append({
            "habit_id": h.get("habit_id"),
            "name": name,
            "start_time": start,
            "end_time": new_end,
            "day_of_week": h.get("day_of_week", [1,2,3,4,5])
        })
    return recs

# ===== 메인 =====
def main():
    for filename in os.listdir(INPUT_DIR):
        if not filename.endswith(".json"): continue
        path = os.path.join(INPUT_DIR, filename)
        data = json.load(open(path, "r", encoding="utf-8"))
        user_id, nickname = data["user_id"], data.get("nickname", str(data["user_id"]))
        report_type = (data.get("type") or "monthly").lower()
        if report_type not in ("weekly", "monthly"): report_type = "monthly"
        output_dir = OUTPUT_DIRS[report_type]
        habits_all = data.get("habits", [])
        active_habits = [h for h in minutes_filter_copy(habits_all) if h.get("habit_log")]
        if not active_habits: continue
        parsed = {}

        # 공통 구성요소 (weekly/monthly 동일)
        top_fail = compute_per_habit_top_failure_reasons(active_habits, 2)
        parsed["top_failure_reasons"] = top_fail

        rate = compute_overall_success_rate(active_habits)
        level = consistency_level_from_rate(rate)
        parsed["consistency_index"] = {
            "success_rate": round(rate, 1),
            # "level": level,
            # "thresholds": CONSISTENCY_THRESHOLDS,
            "display_message": f"꾸준함 지수: {level}" + (" 🔥" if level=="높음" else (" 🙂" if level=="보통" else " 🌧️"))
        }

        parsed["summary"] = generate_summary(nickname, active_habits, top_fail, rate)
        parsed["recommendation"] = generate_recommendations(active_habits)

        os.makedirs(output_dir, exist_ok=True)
        json.dump(parsed, open(os.path.join(output_dir, f"user_{user_id}_{nickname}_{report_type}_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"✅ {nickname} {report_type} 리포트 저장 완료")

if __name__ == "__main__":
    main()