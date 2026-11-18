import re
from typing import Union

ONES = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]

def read_two_digits(val: int, has_hundred: bool = False) -> str:
    """Reads a two-digit integer (0-99) in Vietnamese."""
    chuc = val // 10
    donvi = val % 10
    res = []
    
    if chuc == 0:
        if has_hundred and donvi > 0:
            res.append("lẻ " + ONES[donvi])
        elif not has_hundred:
            res.append(ONES[donvi])
    elif chuc == 1:
        res.append("mười")
        if donvi == 1:
            res.append("một")
        elif donvi == 5:
            res.append("lăm")
        elif donvi > 0:
            res.append(ONES[donvi])
    else:
        res.append(ONES[chuc] + " mươi")
        if donvi == 1:
            res.append("mốt")
        elif donvi == 4:
            res.append("tư")
        elif donvi == 5:
            res.append("lăm")
        elif donvi > 0:
            res.append(ONES[donvi])
            
    return " ".join(res)

def read_three_digits(val: int, is_highest: bool = False) -> str:
    """Reads a three-digit group (0-999) in Vietnamese."""
    tram = val // 100
    du = val % 100
    res = []
    
    if tram > 0 or not is_highest:
        res.append(ONES[tram] + " trăm")
    if du > 0:
        res.append(read_two_digits(du, has_hundred=(tram > 0 or not is_highest)))
        
    return " ".join(res)

def read_int(n: Union[int, str]) -> str:
    """
    Reads an integer of arbitrary size into Vietnamese words.
    Supports numbers up to billions and beyond.
    """
    try:
        n = int(n)
    except (ValueError, TypeError):
        return str(n)
        
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + read_int(-n)
        
    units = ["", "nghìn", "triệu", "tỷ"]
    groups = []
    temp = n
    while temp > 0:
        groups.append(temp % 1000)
        temp //= 1000
        
    words = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        is_highest = (i == len(groups) - 1)
        g_words = read_three_digits(g, is_highest=is_highest)
        unit = units[i % 4]
        # Append "tỷ" for multiples of 4 groups
        ty_repeat = (i // 4)
        suffix = (" " + unit if unit else "") + (" tỷ" * ty_repeat if (i % 4 == 0 and ty_repeat > 0) else "")
        words.append(g_words + suffix)
        
    return " ".join(words).strip()

def read_digits_verbatim(digits_str: str) -> str:
    """Reads each digit individually (for decimal parts, phone numbers, codes)."""
    return " ".join(ONES[int(d)] for d in digits_str if d.isdigit())

def normalize_vietnamese_text(text: str) -> str:
    """
    Normalizes Vietnamese text for TTS synthesis:
    - Decimals with dot (1.6 -> một chấm sáu)
    - Thousands separator with dot (1.600 -> một nghìn sáu trăm)
    - Decimals with comma (1,3456 -> một phẩy ba bốn năm sáu)
    - Percentages (1.6% -> một chấm sáu phần trăm)
    - Currency (1.600đ / 1.600 VNĐ -> một nghìn sáu trăm đồng)
    - Date & time (12/05/2026, 8h30)
    - Raw integers (16 -> mười sáu)
    """
    if not text or not isinstance(text, str):
        return text

    # Normalize unicode spaces
    s = text.replace("\u00a0", " ")

    # Protect OmniVoice inline control / pronunciation tags (e.g. [laughter], [sigh], [B EY1 S])
    protected_tags = []
    def _stash_tag(m):
        idx = len(protected_tags)
        protected_tags.append(m.group(0))
        return f"__OVTAG_{idx}__"
    s = re.sub(r'\[[^\[\]\n]{1,64}\]', _stash_tag, s)
    
    # 1. Version numbers: v1.6, V1.6, ver 1.6 -> phiên bản một chấm sáu
    s = re.sub(r'\b(?:v|ver|phiên bản\s*v)\s*(\d+)\.(\d+)\b', r'phiên bản \1.\2', s, flags=re.IGNORECASE)

    # 2. Percentages: 1.6%, 1,3456%, 50%
    s = re.sub(r'(\d+(?:[.,]\d+)?)\s*%', r'\1 phần trăm', s)

    # 3. Currency: 1.600đ, 1.600 VNĐ, 1.600 vnd, 1.600 đồng, $1.6
    s = re.sub(r'(\d+(?:[.,]\d+)?)\s*(?:đ|vnđ|vnd)\b', r'\1 đồng', s, flags=re.IGNORECASE)
    s = re.sub(r'\$\s*(\d+(?:[.,]\d+)?)', r'\1 đô la', s)
    s = re.sub(r'€\s*(\d+(?:[.,]\d+)?)', r'\1 ơ-rô', s)

    # 4. Dates: DD/MM/YYYY or DD-MM-YYYY
    def repl_date(m):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"ngày {read_int(d)} tháng {read_int(mo)} năm {read_int(y)}"
    s = re.sub(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', repl_date, s)

    # 5. Times: 8h30, 8h, 8:30
    def repl_time(m):
        h = int(m.group(1))
        m_val = m.group(2)
        if m_val:
            return f"{read_int(h)} giờ {read_int(int(m_val))} phút"
        return f"{read_int(h)} giờ"
    s = re.sub(r'\b(\d{1,2})h(\d{1,2})?\b', repl_time, s, flags=re.IGNORECASE)
    s = re.sub(r'\b(\d{1,2}):(\d{2})\b', lambda m: f"{read_int(int(m.group(1)))} giờ {read_int(int(m.group(2)))} phút", s)

    # 6. Thousands separator with dot (Vietnamese standard): 1.600, 25.000, 1.600.000
    # Must have 1 to 3 digits followed by groups of exactly 3 digits separated by dots
    def repl_thousands(m):
        full_num_str = m.group(0).replace(".", "")
        try:
            return read_int(int(full_num_str))
        except ValueError:
            return m.group(0)
    s = re.sub(r'\b\d{1,3}(?:\.\d{3})+\b', repl_thousands, s)

    # 7. Decimals with comma (Vietnamese decimal notation): 1,3456, 1,5, 0,25
    def repl_decimal_comma(m):
        int_part = m.group(1)
        dec_part = m.group(2)
        int_words = read_int(int(int_part))
        dec_words = read_digits_verbatim(dec_part)
        return f"{int_words} phẩy {dec_words}"
    s = re.sub(r'\b(\d+),(\d+)\b', repl_decimal_comma, s)

    # 8. Decimals with dot (International decimal notation / software version): 1.6, 2.5, 3.14
    def repl_decimal_dot(m):
        int_part = m.group(1)
        dec_part = m.group(2)
        int_words = read_int(int(int_part))
        dec_words = read_digits_verbatim(dec_part)
        return f"{int_words} chấm {dec_words}"
    s = re.sub(r'\b(\d+)\.(\d+)\b', repl_decimal_dot, s)

    # 9. Number ranges: 1-2, 10-15 -> 1 đến 2
    s = re.sub(r'\b(\d+)\s*[-–—]\s*(\d+)\b', lambda m: f"{read_int(int(m.group(1)))} đến {read_int(int(m.group(2)))}", s)

    # 10. Multiplications / dimensions: 2x3, 4x4
    s = re.sub(r'\b(\d+)\s*[xX]\s*(\d+)\b', lambda m: f"{read_int(int(m.group(1)))} nhân {read_int(int(m.group(2)))}", s)

    # 11. Remaining standalone integers: 16, 2026, 100
    def repl_lone_int(m):
        val_str = m.group(0)
        try:
            return read_int(int(val_str))
        except ValueError:
            return val_str
    s = re.sub(r'\b\d+\b', repl_lone_int, s)

    # Restore protected OmniVoice inline control tags
    for idx, tag_val in enumerate(protected_tags):
        s = s.replace(f"__OVTAG_{idx}__", tag_val)

    # Collapse repeated whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    return s
