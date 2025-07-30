# api 연결만 테스트 (Success / Error)

import requests
import os
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("API_KEY")  
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={API_KEY}"

def call_gemini(prompt):
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    response = requests.post(API_URL, headers=headers, json=data)
    if response.status_code == 200:
        print("Success!")
        print(response.json())
    else:
        print("Error:", response.status_code)
        print(response.text)

if __name__ == "__main__":
    call_gemini("Hello, Gemini 2.0 Flash!")
