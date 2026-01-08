# 파일: src/agents/llm_config.py

import os
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

load_dotenv()

def get_gemini_llm():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY가 .env 파일에 없습니다.")

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite", 
        verbose=True,
        temperature=0.3,
        google_api_key=api_key
    )
    return llm