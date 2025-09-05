import os
import json
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

INPUT_DIR = "data/sample_users"
MONTHLY_OUTPUT_DIR = "outputs/monthly_report"

# 디렉토리 생성
os.makedirs(MONTHLY_OUTPUT_DIR, exist_ok=True)

def extract_last_30_days_logs(logs):
    today = datetime.today().date()
    thirty_days_ago = today - timedelta(days=30)
    return [
        log for log in logs
        if thirty_days_ago <= datetime.strptime(log["date"], "%Y-%m-%d").date() <= today
    ]

def group_users_by_id():
    """사용자 ID별로 모든 습관 데이터를 그룹화"""
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
        
        # 습관 정보 추가
        habit_info = {
            "habit_id": habit_data["habit_id"],
            "name": habit_data["name"],
            "description": habit_data.get("description", ""),
            "schedule": habit_data.get("schedule", ""),
            "habit_log": extract_last_30_days_logs(habit_data["habit_log"])
        }
        users[user_id]["habits"].append(habit_info)
    
    return users

def build_monthly_prompt(user_info, habits_data):
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
    
    return f"""
당신은 사용자 맞춤형 습관 코치입니다. 아래는 {nickname}의 최근 30일간의 모든 습관 기록입니다.
이 데이터를 바탕으로 다음 내용을 포함한 **친근하고 따뜻한 말투**로 종합 월간 리포트를 작성해주세요:

**[요청 항목]**
1. {nickname}의 이번 달 전체 습관 수행 요약 (전체 성공률: {overall_success_rate:.1f}%)
2. 각 습관별 성공률과 주요 성과 분석
3. 습관 간 상관관계와 패턴 분석
4. 주차별 전체 습관 성공/실패 패턴
5. 장기적인 습관 형성 과정에서의 진전 상황
6. 다음 달을 위한 현실적이고 응원하는 제안
7. 월간 하이라이트와 특별한 성취

> 출력은 자연스러운 단락 형식(문장 중심)으로 작성해주세요. 너무 딱딱한 분석 톤보다,  
> 감정이 담긴 AI 코치처럼 말해주세요 (예: ~하셨어요, ~해보는 건 어때요?, ~라서 아쉽지만 괜찮아요!).

<사용자 정보>
이름: {nickname}
나이: {age}세
직업: {occupation}
특징: {', '.join(characteristics) if characteristics else '특별한 특징 없음'}

<습관별 요약>
{chr(10).join(habit_summaries)}

<최근 30일간 모든 습관 기록>
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

def generate_monthly_report_for_user(user_data):
    """API 호출을 위한 월간 리포트 생성 함수"""
    try:
        # 사용자 데이터에서 습관 정보 추출
        user_info = {
            "user_id": user_data["user_id"],
            "nickname": user_data["nickname"],
            "age": user_data.get("age"),
            "occupation": user_data.get("occupation"),
            "characteristics": user_data.get("characteristics", [])
        }
        
        habits_data = user_data["habits"]
        
        # 최근 30일간 기록이 있는 습관만 필터링
        active_habits = [habit for habit in habits_data if habit.get("habit_log")]
        
        if active_habits:
            monthly_prompt = build_monthly_prompt(user_info, active_habits)
            return call_gemini(monthly_prompt)
        else:
            return f"{user_info['nickname']}님의 최근 30일간 습관 기록이 없어 월간 리포트를 생성할 수 없습니다."
    except Exception as e:
        return f"월간 리포트 생성 중 오류가 발생했습니다: {str(e)}"

def main():
    # 사용자별로 습관 데이터 그룹화
    users = group_users_by_id()
    
    for user_id, user_data in users.items():
        # 최근 30일간 기록이 있는 습관만 필터링
        active_habits = [habit for habit in user_data["habits"] if habit["habit_log"]]
        
        if active_habits:
            monthly_prompt = build_monthly_prompt(user_data["user_info"], active_habits)
            nickname = user_data["user_info"]["nickname"]
            print(f"📡 {nickname}의 종합 월간 리포트 LLM 호출 중...")
            monthly_response = call_gemini(monthly_prompt)
            
            monthly_output_path = os.path.join(MONTHLY_OUTPUT_DIR, f"user_{user_id}_{nickname}_monthly_report.md")
            with open(monthly_output_path, "w", encoding="utf-8") as f:
                f.write(monthly_response)
            print(f"✅ {nickname}의 종합 월간 리포트 저장 완료: {monthly_output_path}")
        else:
            nickname = user_data["user_info"]["nickname"]
            print(f"⚠️ {nickname}: 최근 30일간 기록 없음 (월간 리포트 건너뜀)")

if __name__ == "__main__":
    main() 