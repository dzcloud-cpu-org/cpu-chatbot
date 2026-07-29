from guardrail.output_guardrail import check_output

CONTEXT = '[{"title": "성수 캐릭터 팝업", "location": "성수", "source_url": "https://example.com/popup/1"}]'


def test_clean_answer_untouched():
    answer = "성수 캐릭터 팝업\n위치: 성수\n상세페이지: https://example.com/popup/1"
    result = check_output(answer, CONTEXT)
    assert result.blocked is False
    assert result.answer == answer
    assert result.flags == []


def test_profanity_blocks_entire_answer():
    result = check_output("이 팝업은 씨발 좋아요", CONTEXT)
    assert result.blocked is True
    assert "profanity" in result.flags


def test_ungrounded_url_is_stripped():
    answer = "상세페이지: https://malicious-example.com/phishing"
    result = check_output(answer, CONTEXT)
    assert result.blocked is False
    assert "malicious-example.com" not in result.answer
    assert "ungrounded_url_removed" in result.flags


def test_grounded_url_is_kept():
    answer = "상세페이지: https://example.com/popup/1"
    result = check_output(answer, CONTEXT)
    assert "https://example.com/popup/1" in result.answer
    assert "ungrounded_url_removed" not in result.flags


def test_ungrounded_phone_number_is_masked():
    answer = "문의 전화번호는 010-1234-5678 입니다."
    result = check_output(answer, CONTEXT)
    assert "010-1234-5678" not in result.answer
    assert "pii_masked" in result.flags
