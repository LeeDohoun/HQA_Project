# 파일: test_setup.py

import google.generativeai as genai

# 1. 여기에 API 키를 직접 입력하세요
my_key = ""

try:
    print(f"🔑 키 확인 중: {my_key[:10]}...")
    genai.configure(api_key=my_key)

    print("\n📋 내 키로 사용 가능한 모델 목록 조회 중...")
    available_models = []
    
    # 구글 서버에 "나한테 허용된 모델 다 보여줘"라고 요청
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f" - 발견됨: {m.name}")
            available_models.append(m.name)

    if not available_models:
        print("\n❌ [404 원인 발견] 사용 가능한 모델이 하나도 없습니다!")
        print("-> Google AI Studio에서 약관 동의가 안 되었거나, 프로젝트 설정이 덜 되었습니다.")
    else:
        print(f"\n✅ 조회 성공! 총 {len(available_models)}개 모델 사용 가능.")
        
        # 테스트: 첫 번째 모델로 인사해보기
        target_model = available_models[0] # 리스트의 첫 번째 놈을 잡음
        print(f"\n🤖 '{target_model}' 모델로 테스트 대화 시도...")
        
        model = genai.GenerativeModel(target_model)
        response = model.generate_content("Hello")
        print(f"✅ 응답 성공: {response.text}")

except Exception as e:
    print(f"\n❌ 에러 발생: {e}")