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

def build_habit_prompt(user_message):
    """습관 등록을 위한 프롬프트 생성"""
    return f"""
당신은 습관 등록 전문가입니다. 사용자가 입력한 자연어 메시지를 분석하여 습관 정보를 구조화된 JSON 형태로 변환해주세요.

**입력된 사용자 메시지:**
{user_message}

**출력 형식 (JSON):**
{{
    "icon": "습관에 맞는 아이콘 (예: 💻, 🏃, 📚, 🎵, 🍎, 💪, 🧘, ☕, 🚶, 🎨)",
    "name": "습관 이름 (어떤 습관을 몇분/몇회 하겠다)",
    "start_time": [시작 시간 배열],
    "end_time": [종료 시간 배열],
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

3. **start_time**: 습관 시작 시간 (24시간 형식)
   - ["09:00"] (단일 시간)
   - ["09:00", "21:00"] (여러 시간)

4. **end_time**: 습관 종료 시간 (24시간 형식)
   - ["10:00"] (단일 시간)
   - ["10:00", "22:00"] (여러 시간)

5. **day_of_week**: 요일 배열
   - [1, 3, 5] (월, 수, 금)
   - [1, 2, 3, 4, 5] (평일)
   - [6, 7] (주말)

**예시:**
- "매일 아침 9시에 코딩 1시간씩 하고 싶어"
  → {{"icon": "💻", "name": "코딩 1시간", "start_time": ["09:00"], "end_time": ["10:00"], "day_of_week": [1, 2, 3, 4, 5, 6, 7]}}

- "월수금 저녁 7시에 운동 30분씩 할래"
  → {{"icon": "💪", "name": "운동 30분", "start_time": ["19:00"], "end_time": ["19:30"], "day_of_week": [1, 3, 5]}}

**중요사항:**
- 반드시 유효한 JSON 형식으로 출력
- 시간은 24시간 형식 (HH:MM)
- 요일은 숫자로 표현 (1=월요일, 7=일요일)
- 사용자가 명시하지 않은 정보는 추가로 요청하세요
- 오직 JSON만 출력하고 다른 설명은 포함하지 마세요
"""

def generate_habit_from_message(user_message):
    """사용자 메시지로부터 습관 정보 생성"""
    try:
        prompt = build_habit_prompt(user_message)
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
    # 테스트 메시지들
    test_messages = [
        "매일 아침 9시에 코딩 1시간씩 하고 싶어",
        "월수금 저녁 7시에 운동 30분씩 할래",
        "평일 오후 2시에 책 읽기 30분",
        "주말 아침 8시에 요가 1시간",
        "매일 밤 10시에 일기 쓰기 15분"
    ]
    
    print("🧪 습관 등록 테스트 시작...\n")
    
    for i, message in enumerate(test_messages, 1):
        print(f"테스트 {i}: {message}")
        result = generate_habit_from_message(message)
        
        if "error" in result:
            print(f"❌ 오류: {result['error']}")
        else:
            print(f"✅ 성공:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        
        print("-" * 50)

if __name__ == "__main__":
    main()
