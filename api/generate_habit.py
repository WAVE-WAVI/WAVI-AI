import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"

def call_gemini(prompt):
    """Gemini API 호출 함수"""
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

def build_habit_prompt(history, currentPrompt):
    """습관 등록을 위한 프롬프트 생성"""
    # history는 리스트 혹은 문자열일 수 있음
    if isinstance(history, list):
        history_text = "\n".join([f"- {item}" for item in history]) if history else "(없음)"
    else:
        history_text = str(history) if history else "(없음)"

    current_text = str(currentPrompt) if currentPrompt is not None else ""

    return f"""
다음의 대화 history와 현재 발화(currentPrompt)를 모두 함께 고려하여 습관을 등록하세요. 필요한 정보가 부족하면 한 번에 모두 물어보도록 `ask`를 구성하세요.

당신은 습관 등록 전문가입니다. 사용자가 입력한 자연어 메시지를 분석하여 습관 정보를 구조화된 JSON 형태로 변환해주세요.

**대화 History:**
{history_text}

**현재 발화(currentPrompt):**
{current_text}

**출력 형식 (JSON):**
{{
    "icon": "습관에 맞는 아이콘 (예: 💻, 🏃, 📚, 🎵, 🍎, 💪, 🧘, ☕, 🚶, 🎨)",
    "name": "습관 이름 (어떤 습관을 몇분/몇회 하겠다)",
    "start_time": 수행 가능 시작 시간 (HH:MM:SS 형식),
    "end_time": 수행 가능 종료 시간 (HH:MM:SS 형식),
    "day_of_week": [요일 배열 (1=월, 2=화, 3=수, 4=목, 5=금, 6=토, 7=일)]
}}

**분석 가이드라인:**
1. **icon**: 습관의 성격에 맞는 이모지 선택
   - 코딩/프로그래밍: 💻
   - 운동/헬스: 💪, 🏃, 🚶
   - 독서/학습: 📚
   - 음악: 🎵
   - 건강/식단: 🍎
   - 명상/요가: 🧘
   - 커피/음료: ☕
   - 예술/창작: 🎨

2. **name**: 구체적이고 명확한 습관명
   - "운동 30분" (시간 기반)
   - "팔굽혀펴기 30개" (횟수 기반)
   - "책 읽기 1시간" (시간 기반)

3. **start_time**: 습관 수행 가능 시작 시간 (HH:MM:SS 형식)
   - 09:00:00 (이 시간부터 습관 수행 가능)

4. **end_time**: 습관 수행 가능 종료 시간 (HH:MM:SS 형식)
   - 11:00:00 (이 시간까지 습관 수행 가능)

5. **day_of_week**: 요일 배열
   - [1, 3, 5] (월, 수, 금)
   - [1, 2, 3, 4, 5] (평일)
   - [6, 7] (주말)

**예시:**
- "매일 아침 9시에 코딩 1시간씩 하고 싶어"
  → {{"icon": "💻", "name": "코딩 1시간", "start_time": "09:00:00", "end_time": "10:00:00", "day_of_week": [1, 2, 3, 4, 5, 6, 7]}}

- "오전 9시~11시 사이에 코딩 1시간"
  → {{"icon": "💻", "name": "코딩 1시간", "start_time": "09:00:00", "end_time": "11:00:00", "day_of_week": [1, 2, 3, 4, 5, 6, 7]}}

- "월수금 저녁 7시~9시 사이에 운동 30분"
  → {{"icon": "💪", "name": "운동 30분", "start_time": "19:00:00", "end_time": "21:00:00", "day_of_week": [1, 3, 5]}}

**중요사항:**
- 반드시 유효한 JSON 형식으로 출력
- 시간은 24시간 형식 (HH:MM:SS)
- 요일은 숫자로 표현 (1=월요일, 7=일요일)
- 사용자가 명시하지 않은 정보는 임의로 추정하지 마세요
- start_time과 end_time은 습관을 수행할 수 있는 시간 범위를 나타냅니다 (예: 9시~11시 사이에 언제든 1시간 코딩)
- 필수 필드 중 하나라도 확실히 채울 수 없으면 다음 규칙을 따르세요:
  1) 모든 필수 키(`icon`, `name`, `start_time`, `end_time`, `day_of_week`)는 반드시 포함하되, 알 수 없는 값은 null 로 설정
  2) 다음 보조 키를 함께 포함: `need_more_info`: true, `ask`: "누락된 모든 정보를 한 번에 요청하는 한국어 한 문장"
  3) `ask`에는 구체적으로 어떤 항목이 필요한지 함께 명시 (예: "수행 가능한 시간 범위(시작~종료 시간)와 요일을 알려주세요.")
- 모든 정보가 충분하면 `need_more_info`는 false 로 설정하거나 생략해도 됩니다
- 오직 JSON만 출력하고 다른 설명은 포함하지 마세요

**부족 정보 처리 예시:**
- "코딩 1시간씩 하고 싶어"
  → {{"icon": "💻", "name": "코딩 1시간", "start_time": null, "end_time": null, "day_of_week": null, "need_more_info": true, "ask": "수행 가능한 시간 범위(시작~종료 시간)와 요일을 알려주세요."}}
"""

def generate_habit_from_message(user_message):
    """사용자 메시지로부터 습관 정보 생성"""
    try:
        # 입력 유연성: 문자열 또는 {history, currentPrompt}
        if isinstance(user_message, dict):
            history = user_message.get("history", [])
            current_prompt = user_message.get("currentPrompt", "")
        else:
            history = []
            current_prompt = user_message

        prompt = build_habit_prompt(history, current_prompt)
        response = call_gemini(prompt)
        
        # API 키 확인
        if not API_KEY:
            return {"error": "GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인해주세요."}
        
        # 응답이 오류인지 확인
        if response.startswith("[API 오류]") or response.startswith("[파싱 오류]"):
            return {"error": f"API 호출 실패: {response}"}
        
        # JSON 파싱 시도
        try:
            # 응답에서 JSON 부분만 추출
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            else:
                json_str = response.strip()
            
            # JSON 파싱
            habit_data = json.loads(json_str)
            
            # 필수 필드 검증
            required_fields = ["icon", "name", "start_time", "end_time", "day_of_week"]
            for field in required_fields:
                if field not in habit_data:
                    return {"error": f"필수 필드 '{field}'가 누락되었습니다."}
            
            return habit_data
            
        except json.JSONDecodeError as e:
            return {"error": f"JSON 파싱 오류: {e}", "raw_response": response}
            
    except Exception as e:
        return {"error": f"습관 생성 중 오류가 발생했습니다: {str(e)}"}

def main():
    """테스트용 메인 함수"""
    # 테스트 입력들 (history + currentPrompt 형식)
    test_messages = [
        {
            "currentPrompt": "오전 9시~11시 사이에",
            "history": [
                "User: 코딩 1시간 하고 싶어",
                "AI: 언제 하실 건가요? 수행 가능한 시간 범위와 요일을 알려주세요."
            ]
        },
        {
            "currentPrompt": "월수금 저녁 7시~9시 사이에",
            "history": [
                "User: 운동 30분씩 할래",
                "AI: 요일과 수행 가능한 시간 범위를 알려주세요."
            ]
        },
        {
            "currentPrompt": "평일 오후 2시~4시 사이에",
            "history": [
                "User: 책 읽기 30분",
                "AI: 수행 가능한 시간 범위와 요일이 어떻게 되나요?"
            ]
        },
        {
            "currentPrompt": "주말 아침 8시~10시 사이에",
            "history": [
                "User: 요가 1시간",
                "AI: 어떤 요일에 진행할까요? 수행 가능한 시간 범위도 알려주세요."
            ]
        },
        {
            "currentPrompt": "매일 밤 10시~11시 사이에 일기 쓰기 15분",
            "history": []
        }
    ]
    
    print("🧪 습관 등록 테스트 시작...\n")
    
    for i, message in enumerate(test_messages, 1):
        print(f"테스트 {i}: {json.dumps(message, ensure_ascii=False)}")
        result = generate_habit_from_message(message)
        
        if "error" in result:
            print(f"❌ 오류: {result['error']}")
        else:
            print(f"✅ 성공:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        
        print("-" * 50)

if __name__ == "__main__":
    main()