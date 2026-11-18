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

LETTER_VI = {
    "A": "ây", "B": "bi", "C": "xi", "D": "đi", "E": "i",
    "F": "ép", "G": "gi", "H": "hát", "I": "ai", "J": "dây",
    "K": "cây", "L": "eo", "M": "em", "N": "en", "O": "ô",
    "P": "pi", "Q": "kiu", "R": "a", "S": "ét", "T": "ti",
    "U": "iu", "V": "vi", "W": "đắp liu", "X": "ích", "Y": "oai", "Z": "dét",
}

WORD_ACRONYMS = {
    "RAM", "ROM", "PIN", "SIM", "LAN", "WAN", "WIFI", "LASER",
    "NATO", "COVID", "ASEAN", "RADAR", "SONAR", "UNESCO", "UNICEF", "FIFA", "VISA",
}


def _expand_alnum_code(code_str: str) -> str:
    """Expands technical alphanumeric codes like 'MIS-C1XD', 'C1', '3D', '5G' into Vietnamese phonetics."""
    parts = []
    for sub in code_str.split("-"):
        if not sub:
            continue
        if sub in WORD_ACRONYMS:
            parts.append(sub.lower())
            continue
        for tok in re.findall(r"[A-Z]+|\d+", sub):
            if tok.isdigit():
                parts.append(read_int(int(tok)))
            elif tok in WORD_ACRONYMS:
                parts.append(tok.lower())
            else:
                parts.extend(LETTER_VI.get(ch, ch.lower()) for ch in tok)
    return " ".join(parts)


def normalize_vietnamese_text(text: str) -> str:
    """
    Normalizes Vietnamese text for TTS synthesis:
    - Decimals with dot (1.6 -> một chấm sáu)
    - Thousands separator with dot (1.600 -> một nghìn sáu trăm)
    - Decimals with comma (1,3456 -> một phẩy ba bốn năm sáu)
    - Percentages (1.6% -> một chấm sáu phần trăm)
    - Currency (1.600đ / 1.600 VNĐ -> một nghìn sáu trăm đồng)
    - Measurement & technical units (4,2 mm -> bốn phẩy hai mi li mét, 60 Nm -> sáu mươi niu tơn mét)
    - Alphanumeric model codes & uppercase acronyms (MIS-C1XD -> em ai ét xi một ích đi)
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
        return f"\x02ovtag{idx}\x03"
    s = re.sub(r'\[[^\[\]\n]{1,64}\]', _stash_tag, s)

    # 0. Diameter symbol & technical measurement units attached to numbers (before numbers become words)
    s = re.sub(r'[Øø]\s*(?=\d)', 'đường kính ', s)
    s = re.sub(r'(?<=\d)\s*(?:N[·.]m|Nm)\b', ' niu tơn mét', s)
    s = re.sub(r'(?<=\d)\s*GHz\b', ' gi ga héc', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<=\d)\s*MHz\b', ' mê ga héc', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<=\d)\s*kHz\b', ' ki lô héc', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<=\d)\s*Hz\b', ' héc', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<=\d)\s*mm\b', ' mi li mét', s)
    s = re.sub(r'(?<=\d)\s*cm\b', ' xen ti mét', s)
    s = re.sub(r'(?<=\d)\s*km\b', ' ki lô mét', s)
    s = re.sub(r'(?<=\d)\s*nm\b', ' na nô mét', s)
    s = re.sub(r'(?<=\d)\s*mg\b', ' mi li gam', s)
    s = re.sub(r'(?<=\d)\s*kg\b', ' ki lô gam', s)
    s = re.sub(r'(?<=\d)\s*ml\b', ' mi li lít', s)
    s = re.sub(r'(?<=\d)\s*°\s*C\b', ' độ xê', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<=\d)\s*°\s*F\b', ' độ ép', s, flags=re.IGNORECASE)
    
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

    # 5b. Multiplications / dimensions before decimal conversion so both '2x3' and '4,2x13' work cleanly
    s = re.sub(r'(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+(?:[.,]\d+)?)', r'\1 nhân \2', s)

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

    # 10. Mixed uppercase+digit technical/product codes (e.g. MIS-C1XD, C1XD, C1, 3D, 4K, 5G)
    s = re.sub(
        r'\b(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9]+(?:-[A-Z0-9]+)*\b',
        lambda m: _expand_alnum_code(m.group(0)),
        s,
    )

    # 10b. Short ALL-CAPS acronyms (2-5 chars, e.g. MIS, XD, AI, USB, GPU) in mixed-case sentences
    if any(c.islower() for c in s) or len(s.strip(".,!?;: ")) <= 5:
        def repl_acronym(m):
            tok = m.group(0)
            if tok in WORD_ACRONYMS:
                return tok.lower()
            return _expand_alnum_code(tok)
        s = re.sub(r'\b[A-Z]{2,5}(?:-[A-Z]{2,5})*\b', repl_acronym, s)

    # 10c. Common English technical terms inside Vietnamese medical/engineering dubbing
    s = re.sub(r'\bPilot\b', 'pai lốt', s)

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
        s = s.replace(f"\x02ovtag{idx}\x03", tag_val)

    # Collapse repeated whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    orig_first = text.lstrip()[0] if text and text.lstrip() else ""
    if s and orig_first.isalpha() and orig_first.isupper() and s[0].isalpha():
        s = s[0].upper() + s[1:]
    return s
