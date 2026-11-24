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


def test_mixed_thousands_decimals_and_signed_numbers():
    # Digit-by-digit decimals combined with thousands separators
    assert normalize_vietnamese_text("1.284,65 điểm") == "một nghìn hai trăm tám mươi tư phẩy sáu năm điểm"
    assert normalize_vietnamese_text("18.450,75 tỷ đồng") == "mười tám nghìn bốn trăm năm mươi phẩy bảy năm tỷ đồng"
    assert normalize_vietnamese_text("1,234.56 USD") == "một nghìn hai trăm ba mươi tư chấm năm sáu đô la Mỹ"
    assert normalize_vietnamese_text("1,200,000 lượt xem") == "một triệu hai trăm nghìn lượt xem"
    assert normalize_vietnamese_text("1 500 000 người") == "một triệu năm trăm nghìn người"
    assert normalize_vietnamese_text("1.000.050.000") == "một tỷ không trăm năm mươi nghìn"

    # Negative / positive signs without duplicate 'âm âm'
    assert normalize_vietnamese_text("Nhiệt độ -5°C đến 22,5°C") == (
        "Nhiệt độ âm năm độ xê đến hai mươi hai phẩy năm độ xê"
    )
    assert normalize_vietnamese_text("Lợi nhuận âm -125 tỷ") == "Lợi nhuận âm một trăm hai mươi lăm tỷ"
    assert normalize_vietnamese_text("Tăng +0,45% và lạm phát -0,15%") == (
        "Tăng cộng không phẩy bốn năm phần trăm và lạm phát âm không phẩy một năm phần trăm"
    )
    assert normalize_vietnamese_text("Lãi suất từ 4,8% - 6,25%/năm") == (
        "Lãi suất từ bốn phẩy tám phần trăm đến sáu phẩy hai năm phần trăm một năm"
    )


def test_phones_otp_cccd_and_leading_zeros():
    assert normalize_vietnamese_text("Gọi 0901234567") == "Gọi không chín không một, hai ba bốn, năm sáu bảy"
    assert normalize_vietnamese_text("Liên hệ 0987.654.321") == "Liên hệ không chín tám bảy, sáu năm bốn, ba hai một"
    assert normalize_vietnamese_text("Hotline 1900 1080 hoặc 028 3822 5678") == (
        "Hotline một chín không không, một không tám không hoặc không hai tám, ba tám hai hai, năm sáu bảy tám"
    )
    assert normalize_vietnamese_text("Số +84 912 345 678") == (
        "Số cộng tám tư, chín một hai, ba bốn năm, sáu bảy tám"
    )
    assert normalize_vietnamese_text("Mã OTP của bạn là 048291") == (
        "Mã ô ti pi của bạn là không bốn tám hai chín một"
    )
    assert normalize_vietnamese_text("Điệp viên 007") == "Điệp viên không không bảy"
    assert normalize_vietnamese_text("Căn cước 079095001234") == (
        "Căn cước không bảy chín không, chín năm không không, một hai ba bốn"
    )


def test_ecommerce_currencies_units_versions_ips_roman_and_bilingual():
    assert normalize_vietnamese_text("Chỉ 199k hoặc mẫu 2tr5 và 100$") == (
        "Chỉ một trăm chín mươi chín ca hoặc mẫu hai triệu rưỡi và một trăm đô la"
    )
    assert normalize_vietnamese_text("Căn hộ 120 m² và 50m2 sân vườn") == (
        "Căn hộ một trăm hai mươi mét vuông và năm mươi mét vuông sân vườn"
    )
    assert normalize_vietnamese_text("Video 4K 60fps hoặc 1080p qua cổng USB-C") == (
        "Video bốn ca sáu mươi ép pê ét hoặc một không tám không pê qua cổng iu ét bi xi"
    )
    assert normalize_vietnamese_text("Phiên bản v2.4.1 tại IP 192.168.1.1 tối ưu 2/3 băng thông 24/7") == (
        "Phiên bản hai chấm bốn chấm một tại ai pi một chín hai chấm một sáu tám chấm một chấm một "
        "tối ưu hai phần ba băng thông hai mươi tư trên bảy"
    )
    assert normalize_vietnamese_text("Báo cáo Quý III và Quý IV lúc 8h30p và 14:05:30") == (
        "Báo cáo Quý ba và Quý tư lúc tám giờ ba mươi phút và mười bốn giờ năm phút ba mươi giây"
    )
    assert normalize_vietnamese_text("Đội nhà thắng với tỷ số 2-1 trên màn hình tỷ lệ 16:9") == (
        "Đội nhà thắng với tỷ số hai một trên màn hình tỷ lệ mười sáu chín"
    )
    assert normalize_vietnamese_text("Kết nối Cloudflare qua REST API chuẩn UI/UX lúc 14h00") == (
        "Kết nối Cloudflare qua rest ây pi ai chuẩn iu ai iu ích lúc mười bốn giờ"
    )




