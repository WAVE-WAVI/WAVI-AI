import os
import json
import argparse
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

INPUT_DIR = "data/sample_users"
OUTPUT_DIRS = {
    "weekly": "outputs/weekly_report",
    "monthly": "outputs/monthly_report",
}

# 디렉토리 생성
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
    users = defaultdict(lambda: {"habits": [], "user_info": {}})

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
    intro = (
        f"당신은 사용자 맞춤형 습관 코치입니다. 아래는 {nickname}의 {period_label}간 모든 습관 기록입니다."
    )

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


def call_gemini(prompt):
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    response = requests.post(API_URL, headers=headers, json=data)

    if response.status_code == 200:
        try:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            return f"[파싱 오류]: {e}"
    else:
        return f"[API 오류]: {response.status_code} - {response.text}"


def generate_report_for_user(user_data, report_type, start_date, end_date):
    """API 호출을 위한 통합 리포트 생성 함수

    report_type: "weekly" | "monthly"
    """
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
            return call_gemini(prompt)
        else:
            label = "최근 7일간" if report_type == "weekly" else "최근 30일간"
            return f"{user_info['nickname']}님의 {label} 습관 기록이 없어 리포트를 생성할 수 없습니다."
    except Exception as e:
        return f"리포트 생성 중 오류가 발생했습니다: {str(e)}"


def main():
    parser = argparse.ArgumentParser(description="Generate unified weekly/monthly reports")
    parser.add_argument("--type", choices=["weekly", "monthly"], required=True, help="Report type")
    args = parser.parse_args()

    report_type = args.type
    filter_days = 7 if report_type == "weekly" else 30
    output_dir = OUTPUT_DIRS[report_type]

    users = group_users_by_id(filter_days)

    for user_id, user_data in users.items():
        active_habits = [habit for habit in user_data["habits"] if habit["habit_log"]]

        nickname = user_data["user_info"]["nickname"]
        if active_habits:
            # 기간 계산 (포함 범위)
            end_date = datetime.today().date()
            start_date = end_date - timedelta(days=filter_days)

            prompt = build_prompt(report_type, user_data["user_info"], active_habits, str(start_date), str(end_date))
            print(f"📡 {nickname}의 종합 {report_type} 리포트 LLM 호출 중...")
            response = call_gemini(prompt)

            # 모델 응답을 JSON으로 파싱 (백틱 코드블록 처리 포함)
            json_text = response
            if "```json" in json_text:
                s = json_text.find("```json") + 7
                e = json_text.find("```", s)
                json_text = json_text[s:e].strip()
            elif "```" in json_text:
                s = json_text.find("```") + 3
                e = json_text.find("```", s)
                json_text = json_text[s:e].strip()

            try:
                parsed = json.loads(json_text)
                parsed.setdefault("start_date", str(start_date))
                parsed.setdefault("end_date", str(end_date))
                json_name = f"user_{user_id}_{nickname}_{report_type}_report.json"
                json_path = os.path.join(output_dir, json_name)
                with open(json_path, "w", encoding="utf-8") as jf:
                    json.dump(parsed, jf, ensure_ascii=False, indent=2)
                print(f"✅ JSON 저장 완료: {json_path}")
            except Exception:
                # 파싱 실패 시에도 JSON만 저장 (원문 포함)
                fallback = {
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                    "error": "JSON 파싱 실패",
                    "raw_response": response,
                }
                json_name = f"user_{user_id}_{nickname}_{report_type}_report.json"
                json_path = os.path.join(output_dir, json_name)
                with open(json_path, "w", encoding="utf-8") as jf:
                    json.dump(fallback, jf, ensure_ascii=False, indent=2)
                print(f"⚠️ 모델 응답 파싱 실패. 원문을 포함한 JSON으로 저장: {json_path}")
        else:
            print(f"⚠️ {nickname}: 최근 데이터 없음 ({report_type} 리포트 건너뜀)")


if __name__ == "__main__":
    main()


