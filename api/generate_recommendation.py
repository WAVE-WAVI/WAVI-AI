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

def main():
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(DATA_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            user_data = json.load(f)

        prompt = PROMPT_TEMPLATE.format(
            user_id=user_data["user_id"],
            habit_id=user_data["habit_id"],
            name=user_data["name"],
            habit_log=json.dumps(user_data["habit_log"], ensure_ascii=False)
        )

        print(f"⏳ {filename} 호출 중...")
        response_text = call_gemini(prompt)

        output_path = os.path.join(OUTPUT_DIR, filename.replace(".json", "_recommendation.json"))
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(response_text)

        print(f"✅ {filename} → 완료")

if __name__ == "__main__":
    main()