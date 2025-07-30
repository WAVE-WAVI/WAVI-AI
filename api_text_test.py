# text 출력 테스트

import requests
import json
from dotenv import load_dotenv
load_dotenv()

API_KEY = "AIzaSyBJvVjKytId_1XgU4RqugiSWnHij-KUU7k"  
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={API_KEY}"

PROMPT = '''WAVI 앱 - 실패 기반 습관 관리 LLM 명세서

페르소나: WAVI 앱의 핵심 AI 기능을 담당하는 개발팀의 일원으로서, 사용자의 습관 실패 데이터를 적극적으로 활용해 회복, 동기 부여, 맞춤 추천을 제공하는 LLM의 기능 명세서를 작성합니다.

1. 실패 친화형 스케줄 추천
- 설명: 사용자의 습관 실패 이력을 분석하여, 실패가 잦은 시간대를 피하고 회복 가능성이 높은 시간대를 강조해 추천합니다.
- 예시: "금요일 밤에 실패가 많으니, 주말 오전에 시도해보세요."
- 입력 데이터: user_id, habit_id, name, habit_log(각 기록에 date, day_of_week, start_time, completed 등), habit_report
- 출력 예시(JSON):
{
  "recommendation_type": "schedule_recommendation",
  "habit_id": 1,
  "name": "운동",
  "recommended_time_slot": {"day_of_week": 0, "start_time": "10:00"},
  "reasoning": "금요일 밤에 실패가 많으니, 주말 오전에 시도해보세요."
}

2. 실패 원인별 회복 미션 제안
- 설명: 반복되는 실패 원인을 분석해, 환경/행동 기반의 구체적인 극복 미션을 제시합니다.
- 예시: "의지 부족 → 전날 요가매트 깔기"
- 입력 데이터: user_id, habit_id, name, habit_log(실패 시 failure_reason 포함)
- 출력 예시(JSON):
{
  "mission_type": "recovery_mission",
  "habit_id": 1,
  "name": "아침 운동",
  "failure_reason": ["의지 부족"],
  "suggested_mission": "전날 밤에 요가매트를 미리 깔아두세요.",
  "reasoning": "의지 부족으로 실패가 반복되어, 환경을 미리 준비하는 미션을 추천합니다."
}

3. 공감 기반 동기 메시지 생성
- 설명: 회복탄력성 이론을 반영해, 실패 상황에 공감하고 작은 실천을 독려하는 동기 메시지를 생성합니다.
- 예시: "감정 기복 → 단 한 줄 일기라도 써보기"
- 입력 데이터: user_id, habit_id, name, habit_log(실패 시 failure_reason 포함)
- 출력 예시(JSON):
{
  "message_type": "empathy_motivation",
  "habit_id": 1,
  "name": "저녁 일기",
  "failure_reason": ["감정 기복"],
  "motivation_message": "감정 기복이 심한 날엔, 단 한 줄이라도 일기를 써보는 건 어떨까요? 작은 실천이 회복의 시작입니다!"
}

4. 실패 기반 푸시 리마인더
- 설명: 실패 전력이 많은 시간대에 맞춰, "다시 도전해요"와 같은 리마인드 알림을 보냅니다.
- 예시: "20:30에 저녁 산책 리마인드"
- 입력 데이터: user_id, habit_id, name, habit_log(실패 시간대 포함)
- 출력 예시(JSON):
{
  "reminder_type": "failure_based_reminder",
  "habit_id": 1,
  "name": "저녁 산책",
  "reminder_time": "20:30",
  "reminder_message": "20:30에 자주 놓쳤던 저녁 산책, 오늘은 꼭 도전해보세요!"
}
'''

# 입력 데이터 필드 구조 (예시)
example_input_fields = {
    "user_id": "사용자 고유 ID (bigint)",
    "habit_id": "습관 ID (bigint)",
    "name": "습관 이름 (varchar)",
    "habit_log": [
        {
            "date": "기록 날짜 (YYYY-MM-DD)",
            "day_of_week": "요일 (0~6, 0=일요일)",
            "start_time": "시작 시간 (HH:MM)",
            "end_time": "종료 시간 (HH:MM, 선택 사항)",
            "completed": "성공/실패 (bool)",
            "difficulty_rating": "난이도 (1-5)",
            "satisfaction_rating": "만족도 (1-5)",
            "failure_reason": "실패 원인 (배열, 실패 시)"
        }
    ]
}

# 실제 예시 입력 데이터
example_input = '''
user_id: 1
habit_id: 1
name: 운동
habit_log: [
  {"date": "2024-03-01", "day_of_week": 5, "start_time": "07:00", "completed": true, "difficulty_rating": 3, "satisfaction_rating": 4},
  {"date": "2024-03-03", "day_of_week": 0, "start_time": "10:00", "completed": true, "difficulty_rating": 2, "satisfaction_rating": 5},
  {"date": "2024-03-08", "day_of_week": 5, "start_time": "21:00", "completed": false, "difficulty_rating": 4, "satisfaction_rating": 2, "failure_reason": ["시간 부족"]}
]
이 데이터를 참고해 출력 예시와 같은 JSON을 생성해 주세요.'''

def call_gemini(prompt):
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [
            {"parts": [
                {"text": prompt}
            ]}
        ]
    }
    response = requests.post(API_URL, headers=headers, json=data)
    if response.status_code == 200:
        print("Success!")
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    else:
        print("Error:", response.status_code)
        print(response.text)

if __name__ == "__main__":
    call_gemini(PROMPT + "\n" + example_input)
