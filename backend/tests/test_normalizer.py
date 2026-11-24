import pytest
from app.utils.vietnamese_normalizer import (
    normalize_vietnamese_text,
    read_int,
    count_vietnamese_spoken_syllables,
    estimate_vietnamese_spoken_duration,
    compute_segment_timing_budget,
)

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
    assert normalize_vietnamese_text("ngày 24/11/2026") == "ngày hai mươi tư tháng mười một năm hai nghìn không trăm hai mươi sáu"
    assert normalize_vietnamese_text("Ngày 24-11-2026, hệ thống ra mắt.") == (
        "Ngày hai mươi tư tháng mười một năm hai nghìn không trăm hai mươi sáu, hệ thống ra mắt."
    )
    assert normalize_vietnamese_text(
        "Theo thống kê mới nhất ngày 24/11/2026, hơn 4.800 doanh nghiệp đã chuyển đổi."
    ) == (
        "Theo thống kê mới nhất ngày hai mươi tư tháng mười một năm hai nghìn không trăm hai mươi sáu, "
        "hơn bốn nghìn tám trăm doanh nghiệp đã chuyển đổi."
    )
    assert normalize_vietnamese_text("Sự kiện diễn ra ngày 30/4 và tháng 11/2026.") == (
        "Sự kiện diễn ra ngày ba mươi tháng tư và tháng mười một năm hai nghìn không trăm hai mươi sáu."
    )

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


def test_preserves_omnivoice_inline_control_tags():
    raw = "[laughter] Anh ấy chơi [B EY1 S] lúc 8h30 với giá 1.500đ [sigh]"
    res = normalize_vietnamese_text(raw)
    assert "[laughter]" in res
    assert "[B EY1 S]" in res
    assert "[sigh]" in res
    assert "tám giờ ba mươi phút" in res
    assert "một nghìn năm trăm đồng" in res


def test_technical_units_decimals_and_medical_acronyms_for_dubbing():
    assert normalize_vietnamese_text("Sử dụng implant đường kính 4,2 mm và chiều dài 13 mm.") == (
        "Sử dụng implant đường kính bốn phẩy hai mi li mét và chiều dài mười ba mi li mét."
    )
    assert normalize_vietnamese_text("Mô-men xoắn khuyến nghị cho implant MIS là 35 đến 60 Nm.") == (
        "Mô-men xoắn khuyến nghị cho implant em ai ét là ba mươi lăm đến sáu mươi niu tơn mét."
    )
    assert normalize_vietnamese_text("Video này sẽ hướng dẫn đặt implant MIS-C1XD.") == (
        "Video này sẽ hướng dẫn đặt implant em ai ét xi một ích đi."
    )
    assert normalize_vietnamese_text("Mũi khoan XD Pilot có đầu mũi 2mm.") == (
        "Mũi khoan ích đi pai lốt có đầu mũi hai mi li mét."
    )
    assert normalize_vietnamese_text("MIS. Đơn giản hóa.") == "Em ai ét. Đơn giản hóa."


def test_surgical_torque_rpm_and_degree_units():
    assert normalize_vietnamese_text("lực xoắn tối đa 10 Ncm.") == "lực xoắn tối đa mười niu tơn xen ti mét."
    assert normalize_vietnamese_text("lực siết 15N·cm và 30°") == "lực siết mười lăm niu tơn xen ti mét và ba mươi độ"
    assert normalize_vietnamese_text("tốc độ tối đa 200 RPM.") == "tốc độ tối đa hai trăm vòng một phút."
    assert normalize_vietnamese_text("tay khoan tối đa 200 vòng/phút.") == "tay khoan tối đa hai trăm vòng một phút."
    assert normalize_vietnamese_text("dung tích 5 mL và độ dày 50 µm") == "dung tích năm mi li lít và độ dày năm mươi mi crô mét"


def test_phonetic_syllable_count_and_duration_estimator():
    # '10 Ncm' -> 'mười niu tơn xen ti mét' (6 syllables)
    assert count_vietnamese_spoken_syllables("10 Ncm") == 6
    # '10mm' -> 'mười mi li mét' (4 syllables)
    assert count_vietnamese_spoken_syllables("10mm") == 4
    # 'implant' = 2, 'abutment' = 3, 'Multifix' = 3, 'MIS' = 3
    assert count_vietnamese_spoken_syllables("implant") == 2
    assert count_vietnamese_spoken_syllables("abutment") == 3
    assert count_vietnamese_spoken_syllables("Multifix") == 3
    assert count_vietnamese_spoken_syllables("MIS") == 3
    # Full sentence realistic duration check
    est = estimate_vietnamese_spoken_duration(
        "Multifix là phục hình cố định cho xương hàm teo.",
        speed=0.96,
    )
    assert 2.2 <= est <= 2.8

    budget = compute_segment_timing_budget(orig_dur=5.2, gap_after=9.7, speed=0.92)
    assert budget["safe_borrow"] == 0.8
    assert budget["allowed_dur"] == 6.0
    assert budget["min_syl"] < budget["ideal_syl"] < budget["max_syl"]


def test_science_and_tech_terms_vietnamese_pronunciation():
    assert normalize_vietnamese_text("Chỉnh sửa gen CRISPR-Cas9 cắt mạch ADN và ARN.") == (
        "Chỉnh sửa gen crít spơ cát chín cắt mạch a đê en và a rờ en."
    )
    assert normalize_vietnamese_text("CRISPR dùng đoạn RNA dẫn đường cho Cas9.") == (
        "Crít spơ dùng đoạn rờ en ây dẫn đường cho cát chín."
    )
    assert normalize_vietnamese_text("Mỗi GPU và CPU xử lý hàng tỷ phép tính.") == (
        "Mỗi gờ pê u và xê pê u xử lý hàng tỷ phép tính."
    )




