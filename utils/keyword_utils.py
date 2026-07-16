from utils.stopwords import CHATBOT_STOPWORDS
import re


def extract_keywords(user_question: str):
    """
    사용자 질문에서 불용어를 제거하고
    MongoDB 검색에 사용할 핵심 키워드를 추출한다.
    """

    text = user_question


    # =====================================================
    # 불용어 제거
    # =====================================================

    for word in CHATBOT_STOPWORDS:
        text = text.replace(word, " ")


    # =====================================================
    # 특수문자 제거
    # =====================================================

    text = re.sub(
        r"[^가-힣a-zA-Z0-9 ]",
        " ",
        text
    )


    # =====================================================
    # 공백 기준 키워드 추출
    # =====================================================

    keywords = text.split()


    return keywords