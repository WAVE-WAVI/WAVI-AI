import os
import json
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

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

def build_monthly_prompt(user_data, monthly_logs):
    nickname = user_data.get("nickname", f"{user_data['user_id']}님")
    return f"""
당신은 사용자 맞춤형 습관 코치입니다. 아래는 최근 30일간의 습관 기록입니다.
이 데이터를 바탕으로 다음 내용을 포함한 **친근하고 따뜻한 말투**로 월간 리포트를 작성해주세요:

**[요청 항목]**
1. {nickname}의 이번 달 습관 수행 전체 요약  
2. 월간 성공률과 주요 성과  
3. 주차별 습관 성공/실패 패턴 분석  
4. 장기적인 습관 형성 과정에서의 진전 상황  
5. 다음 달을 위한 현실적이고 응원하는 제안  
6. 월간 하이라이트와 특별한 성취  

> 출력은 자연스러운 단락 형식(문장 중심)으로 작성해주세요. 너무 딱딱한 분석 톤보다,  
> 감정이 담긴 AI 코치처럼 말해주세요 (예: ~하셨어요, ~해보는 건 어때요?, ~라서 아쉽지만 괜찮아요!).

<사용자 정보>
이름: {nickname}
습관: {user_data['name']}

<최근 30일간 기록>
{json.dumps(monthly_logs, ensure_ascii=False, indent=2)}
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

        # 월간 리포트 생성 (최근 30일)
        monthly_logs = extract_last_30_days_logs(user_data["habit_log"])
        if monthly_logs:
            monthly_prompt = build_monthly_prompt(user_data, monthly_logs)
            print(f"📡 월간 리포트 LLM 호출 중: {filename}")
            monthly_response = call_gemini(monthly_prompt)
            
            monthly_output_path = os.path.join(MONTHLY_OUTPUT_DIR, filename.replace(".json", "_monthly_report.md"))
            with open(monthly_output_path, "w", encoding="utf-8") as f:
                f.write(monthly_response)
            print(f"✅ 월간 리포트 저장 완료: {monthly_output_path}")
        else:
            print(f"⚠️ {filename}: 최근 30일간 기록 없음 (월간 리포트 건너뜀)")

if __name__ == "__main__":
    main() 