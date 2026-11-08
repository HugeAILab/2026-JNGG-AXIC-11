"""데모 계정 (TODO.md 7.3). seed.py 와 로그인 화면 안내가 함께 쓴다.

데모용이므로 로그인 화면에 공개한다. 실제 운영 시에는 공개 안내를 제거하고 비밀번호 변경을 강제한다.
"""
DEMO_PASSWORD = "demo1234!"

DEMO_ACCOUNTS = [
    {"username": "admin", "name": "관리자", "role": "admin", "use": "전체 기능 + 폐기 처리"},
    {"username": "manager", "name": "김품질", "role": "manager", "use": "대시보드·보고서"},
    {"username": "inspector", "name": "이검사", "role": "inspector", "use": "업로드·판정 확정·재검사"},
    {"username": "worker", "name": "박용접", "role": "worker", "use": "조치 등록"},
]
