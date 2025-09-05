#!/bin/bash

echo "🚀 WAVI AI 서비스 시작 중..."

# 가상환경 활성화 (있는 경우)
if [ -d "venv" ]; then
    echo "📦 가상환경 활성화..."
    source venv/bin/activate
fi

# 의존성 설치 확인
echo "📋 의존성 확인 중..."
pip install -r requirements.txt

# 환경 변수 파일 확인
if [ ! -f ".env" ]; then
    echo "⚠️  .env 파일이 없습니다. env.example을 복사하여 설정해주세요."
    echo "cp env.example .env"
    exit 1
fi

# 서버 시작
echo "🌐 FastAPI 서버 시작..."
python main.py
