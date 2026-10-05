import pytest
from app.utils.vietnamese_normalizer import normalize_vietnamese_text, read_int

def test_vietnamese_integers():
    assert read_int(0) == "không"
    assert read_int(5) == "năm"
    assert read_int(10) == "mười"
    assert read_int(15) == "mười lăm"
    assert read_int(21) == "hai mươi mốt"
    assert read_int(105) == "một trăm lẻ năm"
    assert read_int(125) == "một trăm hai mươi lăm"
    assert read_int(1000) == "một nghìn"
    assert read_int(1600) == "một nghìn sáu trăm"
    assert read_int(1000000) == "một triệu"
    assert read_int(1600000) == "một triệu sáu trăm nghìn"

def test_vietnamese_decimals_and_punctuation():
    # Dot decimal (1.6)
    assert normalize_vietnamese_text("1.6") == "một chấm sáu"
    
    # Thousands separator (1.600)
    assert normalize_vietnamese_text("1.600") == "một nghìn sáu trăm"
    
    # Comma decimal (1,3456)
    assert normalize_vietnamese_text("1,3456") == "một phẩy ba bốn năm sáu"
    
    # Combined user query sentence
    user_input = "1.6 1.600 1,3456"
    expected = "một chấm sáu một nghìn sáu trăm một phẩy ba bốn năm sáu"
    assert normalize_vietnamese_text(user_input) == expected

def test_vietnamese_percentages_and_currency():
    # Percentages
    assert normalize_vietnamese_text("1,3456%") == "một phẩy ba bốn năm sáu phần trăm"
    assert normalize_vietnamese_text("1.6%") == "một chấm sáu phần trăm"
    assert normalize_vietnamese_text("50%") == "năm mươi phần trăm"

    # Currency
    assert normalize_vietnamese_text("1.600đ") == "một nghìn sáu trăm đồng"
    assert normalize_vietnamese_text("1.600 VNĐ") == "một nghìn sáu trăm đồng"
    assert normalize_vietnamese_text("$1.6") == "một chấm sáu đô la"

def test_vietnamese_dates_and_times():
    assert normalize_vietnamese_text("8h30") == "tám giờ ba mươi phút"
    assert normalize_vietnamese_text("20/11/2026") == "ngày hai mươi tháng mười một năm hai nghìn không trăm hai mươi sáu"

def test_vietnamese_ranges_and_multiplications():
    assert normalize_vietnamese_text("1-2") == "một đến hai"
    assert normalize_vietnamese_text("2x3") == "hai nhân ba"

def test_full_complex_sentence():
    sentence = "Phiên bản 1.6 với 1.600 người dùng và tỷ lệ tăng trưởng 1,3456% có giá 1.600đ"
    normalized = normalize_vietnamese_text(sentence)
    assert "phiên bản một chấm sáu" in normalized.lower()
    assert "một nghìn sáu trăm người dùng" in normalized
    assert "một phẩy ba bốn năm sáu phần trăm" in normalized
    assert "một nghìn sáu trăm đồng" in normalized
