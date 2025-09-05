import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

DATA_DIR = "data/sample_users"
OUTPUT_DIR = "outputs/recommendations"
os.makedirs(OUTPUT_DIR, exist_ok=True)

PROMPT_TEMPLATE = """
다음은 습관 실패 로그입니다. 사용자의 데이터를 바탕으로 실패 시간대를 피한 추천 루틴(시간대)을 생성해주세요. 출력은 JSON 형식으로 해주세요.

<입력 예시>
user_id: {user_id}
habit_id: {habit_id}
name: {name}
habit_log: {habit_log}
"""

def call_gemini(prompt):
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    response = requests.post(API_URL, headers=headers, json=data)

    if response.status_code == 200:
        reply = response.json()
        try:
            model_output = reply['candidates'][0]['content']['parts'][0]['text']
            return model_output
        except Exception as e:
            return f"[파싱 오류] {e} / 원본 응답: {reply}"
    else:
        return f"[API 오류] {response.status_code}: {response.text}"

def generate_recommendation_for_user(user_data):
    """API 호출을 위한 추천 생성 함수"""
    try:
        # 사용자 데이터에서 습관 정보 추출
        user_id = user_data["user_id"]
        nickname = user_data.get("nickname", f"사용자{user_id}")
        
        # 실패 로그가 있는 습관들 찾기
        failed_habits = []
        for habit in user_data["habits"]:
            habit_logs = habit.get("habit_log", [])
            failed_logs = [log for log in habit_logs if not log.get("completed", False)]
            if failed_logs:
                failed_habits.append({
                    "habit_id": habit["habit_id"],
                    "name": habit["name"],
                    "habit_log": failed_logs
                })
        
        if failed_habits:
            # 실패 패턴을 분석하여 추천 생성
            prompt = f"""
당신은 습관 형성 전문가입니다. {nickname}님의 습관 실패 패턴을 분석하여 
개인화된 습관 개선 방안을 제시해주세요.

<사용자 정보>
이름: {nickname}
나이: {user_data.get('age', '정보 없음')}세
직업: {user_data.get('occupation', '정보 없음')}

<실패한 습관들>
{json.dumps(failed_habits, ensure_ascii=False, indent=2)}

위 데이터를 바탕으로 다음을 포함한 친근하고 구체적인 추천을 제공해주세요:
1. 실패 패턴 분석
2. 개선을 위한 구체적인 전략
3. 새로운 습관 형성 방법
4. 동기부여 메시지

출력은 자연스러운 한국어로 작성해주세요.
"""
            return call_gemini(prompt)
        else:
            return f"{nickname}님은 최근 습관 실패 기록이 없어서 특별한 추천이 필요하지 않습니다. 현재 잘 하고 계세요!"
    except Exception as e:
        return f"추천 생성 중 오류가 발생했습니다: {str(e)}"

def main():
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(DATA_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            user_data = json.load(f)

        print(f"⏳ {filename} 호출 중...")
        response_text = generate_recommendation_for_user(user_data)

        output_path = os.path.join(OUTPUT_DIR, filename.replace(".json", "_recommendation.json"))
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(response_text)

        print(f"✅ {filename} → 완료")

if __name__ == "__main__":
    main()