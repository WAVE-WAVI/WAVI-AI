import os
import json
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

INPUT_DIR = "data/sample_users"
WEEKLY_OUTPUT_DIR = "outputs/weekly_report"

# 디렉토리 생성
os.makedirs(WEEKLY_OUTPUT_DIR, exist_ok=True)

def extract_last_7_days_logs(logs):
    today = datetime.today().date()
    seven_days_ago = today - timedelta(days=7)
    return [
        log for log in logs
        if seven_days_ago <= datetime.strptime(log["date"], "%Y-%m-%d").date() <= today
    ]

def build_weekly_prompt(user_data, weekly_logs):
    nickname = user_data.get("nickname", f"{user_data['user_id']}님")
    return f"""
당신은 사용자 맞춤형 습관 코치입니다. 아래는 최근 7일간의 습관 기록입니다.
이 데이터를 바탕으로 다음 내용을 포함한 **친근하고 따뜻한 말투**로 주간 리포트를 작성해주세요:

**[요청 항목]**
1. {nickname}의 이번 주 습관 수행 전체 요약  
2. 자주 실패한 원인과 분석  
3. 요일별 습관 성공/실패 패턴  
4. 공감과 위로의 메시지  
5. 다음 주를 위한 현실적이고 응원하는 제안  

> 출력은 자연스러운 단락 형식(문장 중심)으로 작성해주세요. 너무 딱딱한 분석 톤보다,  
> 감정이 담긴 AI 코치처럼 말해주세요 (예: ~하셨어요, ~해보는 건 어때요?, ~라서 아쉽지만 괜찮아요!).

<사용자 정보>
이름: {nickname}
습관: {user_data['name']}

<최근 7일간 기록>
{json.dumps(weekly_logs, ensure_ascii=False, indent=2)}
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

def main():
    for filename in os.listdir(INPUT_DIR):
        if not filename.endswith(".json"):
            continue

        with open(os.path.join(INPUT_DIR, filename), "r", encoding="utf-8") as f:
            user_data = json.load(f)

        # 주간 리포트 생성 (최근 7일)
        weekly_logs = extract_last_7_days_logs(user_data["habit_log"])
        if weekly_logs:
            weekly_prompt = build_weekly_prompt(user_data, weekly_logs)
            print(f"📡 주간 리포트 LLM 호출 중: {filename}")
            weekly_response = call_gemini(weekly_prompt)
            
            weekly_output_path = os.path.join(WEEKLY_OUTPUT_DIR, filename.replace(".json", "_weekly_report.md"))
            with open(weekly_output_path, "w", encoding="utf-8") as f:
                f.write(weekly_response)
            print(f"✅ 주간 리포트 저장 완료: {weekly_output_path}")
        else:
            print(f"⚠️ {filename}: 최근 7일간 기록 없음 (주간 리포트 건너뜀)")

if __name__ == "__main__":
    main() 