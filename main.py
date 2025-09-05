from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uvicorn
import os
from dotenv import load_dotenv

# 기존 AI 모듈들 import
from api.generate_monthly_report import generate_monthly_report_for_user
from api.generate_weekly_report import generate_weekly_report_for_user
from api.generate_recommendation import generate_recommendation_for_user

load_dotenv()

app = FastAPI(
    title="WAVI AI Service",
    description="습관 관리 AI 서비스 API",
    version="1.0.0"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 프로덕션에서는 특정 도메인만 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class UserData(BaseModel):
    user_id: int
    nickname: str
    age: Optional[int] = None
    occupation: Optional[str] = None
    characteristics: List[str] = []
    habits: List[Dict[str, Any]]

@app.post("/generate-monthly-report")
async def generate_monthly_report(user_data: UserData):
    """월간 리포트 생성"""
    try:
        report = generate_monthly_report_for_user(user_data.dict())
        return {"report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"월간 리포트 생성 실패: {str(e)}")

@app.post("/generate-weekly-report")
async def generate_weekly_report(user_data: UserData):
    """주간 리포트 생성"""
    try:
        report = generate_weekly_report_for_user(user_data.dict())
        return {"report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"주간 리포트 생성 실패: {str(e)}")

@app.post("/generate-recommendation")
async def generate_recommendation(user_data: UserData):
    """개인화된 추천 생성"""
    try:
        recommendation = generate_recommendation_for_user(user_data.dict())
        return {"recommendation": recommendation}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"추천 생성 실패: {str(e)}")

@app.get("/health")
async def health_check():
    """헬스 체크"""
    return {"status": "healthy", "service": "WAVI AI Service"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
