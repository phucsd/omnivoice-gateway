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
        ty_repeat = (i // 4)
        suffix = (" " + unit if unit else "") + (" tỷ" * ty_repeat if (i % 4 == 0 and ty_repeat > 0) else "")
        words.append(g_words + suffix)

    return " ".join(words).strip()


def read_digits_verbatim(digits_str: str) -> str:
    """Reads each digit individually (for decimal parts, OTPs, codes)."""
    return " ".join(ONES[int(d)] for d in digits_str if d.isdigit())


def _read_digit_sequence_grouped(digits_str: str) -> str:
    """
    Reads long identifier / phone / CCCD / bank account digit sequences verbatim
    with natural 3-4 digit prosodic comma groups so TTS never drops syllables.
    """
    clean = "".join(ch for ch in digits_str if ch.isdigit())
    n = len(clean)
    if n <= 6:
        return read_digits_verbatim(clean)
    if n == 10:
        # Standard Vietnam 10-digit phone cadence: 4 - 3 - 3 (e.g. 0901 234 567)
        chunks = [clean[:4], clean[4:7], clean[7:]]
    elif n == 11:
        chunks = [clean[:4], clean[4:7], clean[7:]]
    elif n == 12:
        # Standard 12-digit CCCD cadence: 4 - 4 - 4
        chunks = [clean[:4], clean[4:8], clean[8:]]
    else:
        chunks = [clean[i:i + 4] for i in range(0, n, 4)]
        if len(chunks) >= 2 and len(chunks[-1]) == 1:
            tail5 = chunks[-2] + chunks[-1]
            chunks = chunks[:-2] + [tail5[:3], tail5[3:]]
    return ", ".join(read_digits_verbatim(c) for c in chunks if c)


_ROMAN_VALS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_VALID_ROMAN_RE = re.compile(r"^(?:X{0,3})(?:IX|IV|V?I{0,3})$")


def _parse_roman(roman_str: str) -> int:
    s = roman_str.upper().strip()
    if not s or not _VALID_ROMAN_RE.fullmatch(s):
        return 0
    total = 0
    prev = 0
    for ch in reversed(s):
        v = _ROMAN_VALS.get(ch, 0)
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


LETTER_VI = {
    "A": "ây", "B": "bi", "C": "xi", "D": "đi", "E": "i",
    "F": "ép", "G": "gi", "H": "hát", "I": "ai", "J": "dây",
    "K": "cây", "L": "eo", "M": "em", "N": "en", "O": "ô",
    "P": "pi", "Q": "kiu", "R": "a", "S": "ét", "T": "ti",
    "U": "iu", "V": "vi", "W": "đắp liu", "X": "ích", "Y": "oai", "Z": "dét",
}

# Pronounceable ALL-CAPS English words & acronyms that OmniVoice speaks naturally as words
# (prevents spelling out 'REST' -> 'a i ét ti' or 'FLASH' -> 'ép eo ây ét hát')
WORD_ACRONYMS = {
    "RAM", "ROM", "PIN", "SIM", "LAN", "WAN", "WIFI", "LASER",
    "NATO", "COVID", "ASEAN", "RADAR", "SONAR", "UNESCO", "UNICEF", "FIFA", "VISA",
    "REST", "SOAP", "GRPC", "CORS", "JSON", "YAML", "TOML", "BASH", "RUST", "DART",
    "SWIFT", "VITE", "NEXT", "NUXT", "NODE", "DENO", "CORE", "EDGE", "CLOUD", "HOST",
    "PORT", "PING", "ROOT", "USER", "ADMIN", "AUTH", "SYNC", "LOCK", "POOL", "HEAP",
    "CODE", "TEST", "FAST", "DATA", "FILE", "LINK", "FORM", "PAGE", "SITE", "CHAT",
    "POST", "REEL", "VLOG", "BLOG", "LIVE", "SHOW", "GAME", "PLAY", "TEAM", "CLUB",
    "SHOP", "MALL", "MART", "DEAL", "SALE", "FLASH", "SUPER", "ULTRA", "HYPER", "MEGA",
    "MINI", "MAX", "PLUS", "PRO", "LITE", "NANO", "AUTO", "DEMO", "BETA", "ZERO",
    "FULL", "FREE", "OPEN", "PASS", "SAFE", "SMART", "TECH", "SOFT", "HARD", "CHIP",
    "BANK", "CASH", "CARD", "LOAN", "FUND", "BOND", "GOLD", "TOUR", "VIEW", "CHILL",
    "SPA", "GYM", "YOGA", "GOLF", "CAFE", "MENU", "COMBO", "BUFFET", "SHIP", "BOOK",
    "CHECK", "TREND", "TOP", "HOT", "NEW", "VIP", "FAN", "BOSS", "LEAD",
}

SPECIAL_ACRONYMS_VI = {
    "ADN": "a đê en",
    "ARN": "a rờ en",
    "DNA": "đi en ây",
    "RNA": "rờ en ây",
    "GPU": "gờ pê u",
    "CPU": "xê pê u",
    "TPU": "tê pê u",
    "NPU": "en pê u",
    "CRISPR": "crít spơ",
    "USD": "u ét đê",
    "VND": "việt nam đồng",
    "MXH": "mạng xã hội",
    "BĐS": "bất động sản",
    "TNHH": "trách nhiệm hữu hạn",
    "THPT": "trung học phổ thông",
    "THCS": "trung học cơ sở",
    "ĐHQG": "đại học quốc gia",
    "TP": "thành phố",
    "TW": "trung ương",
}


def _expand_alnum_code(code_str: str) -> str:
    """Expands technical alphanumeric codes like 'MIS-C1XD', 'C1', '3D', 'USB-C' into Vietnamese phonetics."""
    parts = []
    for sub in code_str.split("-"):
        if not sub:
            continue
        if sub in SPECIAL_ACRONYMS_VI:
            parts.append(SPECIAL_ACRONYMS_VI[sub])
            continue
        if sub in WORD_ACRONYMS:
            parts.append(sub.lower())
            continue
        for tok in re.findall(r"[A-Z]+|\d+|[a-z]+", sub):
            if tok.isdigit():
                parts.append(read_int(int(tok)))
            elif tok.upper() in SPECIAL_ACRONYMS_VI and tok.isupper():
                parts.append(SPECIAL_ACRONYMS_VI[tok.upper()])
            elif tok.isupper() and tok in WORD_ACRONYMS:
                parts.append(tok.lower())
            elif tok.isupper():
                parts.extend(LETTER_VI.get(ch, ch.lower()) for ch in tok)
            else:
                parts.append(tok)
    return " ".join(parts)


def _resolve_sign_prefix(sign_ch: str, full_str: str, match_start: int) -> str:
    """Converts leading '-' or '+' before a number into 'âm ' or 'cộng ', avoiding duplicate 'âm âm'."""
    if not sign_ch:
        return ""
    before = full_str[:match_start].rstrip()
    last_word = before.rsplit(None, 1)[-1].lower() if before else ""
    if sign_ch == "-":
        if last_word in ("âm", "trừ"):
            return ""
        return "âm "
    if sign_ch == "+":
        if last_word in ("cộng", "dương"):
            return ""
        return "cộng "
    return ""


def normalize_vietnamese_text(text: str) -> str:
    """
    Normalizes Vietnamese + English mixed text for high-accuracy TTS synthesis:
    - Context-aware Roman numerals (Quý III -> Quý ba, Quý IV -> Quý tư, Thế kỷ XXI -> Thế kỷ hai mươi mốt)
    - Phone numbers, hotlines, OTPs, CCCDs, bank accounts & leading-zero IDs (0901234567, 048291, 007)
    - Dates & times (24/11/2026, ngày 30/4, tháng 11/2026, 8h30p, 2h15p30s, 14h00, 14:05:30, 24/7)
    - Software versions & IPv4 addresses (v2.4.1, 3.11.9, 192.168.1.1, H.264, IPv6)
    - E-commerce & international currencies (1.650.000đ, 199k, 2tr5, 15tr, $1,250.50, 100$, 500 USD)
    - Measurement, area/volume & hardware units (100 m², 50m2, 12 m³, -5°C, 500g, 1,5L, 4K, 5G, 60fps, 1080p, 500Mbps, GB/MB)
    - Fractions, ratios, scores & ranges (2/3, 9/10 điểm, tỷ số 2-1, tỷ lệ 16:9, 4,8% - 6,25%/năm)
    - Mixed thousands + decimals (1.284,65 -> một nghìn hai trăm tám mươi tư phẩy sáu năm; 1,200,000)
    - Signed numbers (-5, -15%, +0,45%)
    - Pronounceable English ALL-CAPS words vs letter-by-letter initialisms (REST API, UI/UX, USB-C)
    """
    if not text or not isinstance(text, str):
        return text

    # 0. Normalize unicode spaces & minus signs
    s = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2212", "-")

    # Protect OmniVoice inline control / pronunciation tags (e.g. [laughter], [sigh], [B EY1 S])
    # Use digit-free lowercase placeholders so neither number nor acronym rules touch them!
    protected_tags = []

    def _idx_to_alpha(n: int) -> str:
        out = []
        for _ in range(4):
            out.append(chr(ord("a") + (n % 26)))
            n //= 26
        return "".join(out)

    def _stash_tag(m):
        idx = len(protected_tags)
        protected_tags.append(m.group(0))
        return f"\x02ovtag_{_idx_to_alpha(idx)}\x03"

    s = re.sub(r"\[[^\[\]\n]{1,64}\]", _stash_tag, s)

    # 1. Context-aware Roman numerals BEFORE acronyms (Quý III, Quý IV, Thế kỷ XXI, Chương IV...)
    def _repl_roman_ctx(m):
        label = m.group(1)
        roman = m.group(2)
        val = _parse_roman(roman)
        if val <= 0:
            return m.group(0)
        if label.strip().lower() == "quý" and val == 4:
            return f"{label} tư"
        return f"{label} {read_int(val)}"

    s = re.sub(
        r"(?<!\w)(quý|thế\s+kỷ|chương|phần|tập|mục|cấp|bậc|hạng|loại|vành\s+đai|khóa|lần\s+thứ|đại\s+hội)\s+([IVXL]{1,6})\b",
        _repl_roman_ctx,
        s,
        flags=re.IGNORECASE,
    )

    # 2. Phone numbers, hotlines & explicit identifier digit sequences (preserve leading zeros & natural rhythm)
    # 2a. International +84 phone numbers
    def _repl_intl_phone(m):
        rest = m.group(1)
        groups = [g for g in re.findall(r"\d+", rest) if g]
        if sum(len(g) for g in groups) < 8:
            return m.group(0)
        if len(groups) == 1:
            return "cộng tám tư, " + _read_digit_sequence_grouped(groups[0])
        return "cộng tám tư, " + ", ".join(read_digits_verbatim(g) for g in groups)

    s = re.sub(
        r"(?:(?<=\s)|^)\+84([\s.\-]?\(?\d{2,4}\)?(?:[\s.\-]?\d{3,4}){2,3})\b",
        _repl_intl_phone,
        s,
    )

    # 2b. Vietnam switchboard / hotlines: 1900 1080, 1800-1166
    def _repl_hotline(m):
        p1, p2 = m.group(1), re.sub(r"\D", "", m.group(2))
        return f"{read_digits_verbatim(p1)}, {read_digits_verbatim(p2)}"

    s = re.sub(r"\b(1800|1900)[\s.\-](\d{4,6})\b", _repl_hotline, s)
    s = re.sub(r"\b(1800|1900)(\d{4})\b", _repl_hotline, s)

    # 2c. Spaced / dotted / hyphenated domestic phone numbers starting with 0: (028) 3822 5678, 0987.654.321
    def _repl_grouped_phone(m):
        raw_p = m.group(0)
        groups = re.findall(r"\d+", raw_p)
        total_d = sum(len(g) for g in groups)
        if total_d < 9 or total_d > 12:
            return raw_p
        return ", ".join(read_digits_verbatim(g) for g in groups)

    s = re.sub(
        r"(?<![\w.,\-])\(?0\d{1,3}\)?(?:[\s.\-]\d{3,5}){2,3}\b",
        _repl_grouped_phone,
        s,
    )

    # Normalize space-separated thousands (e.g. '1 500 000', '25 000') into dot-thousands early
    # (after phone numbers are already handled so phone groups are untouched)
    s = re.sub(
        r"(?<![\d.,])\b(\d{1,3}(?: \d{3}){2,}|\d{1,3} 000)\b(?![\s.\-]\d)",
        lambda m: m.group(1).replace(" ", "."),
        s,
    )

    # 2d. Explicit ID keywords (OTP, PIN, CCCD, CMND, STK, tài khoản, mã số) followed by 4+ digits
    def _repl_keyword_id(m):
        kw = m.group(1)
        digits = m.group(2)
        return f"{kw}{_read_digit_sequence_grouped(digits)}"

    s = re.sub(
        r"((?<!\w)(?:mã\s+xác\s+thực\s+otp|mã\s+otp|otp|mã\s+pin|mã\s+xác\s+thực|số\s+căn\s+cước|căn\s+cước\s+số|căn\s+cước|cccd|cmnd|số\s+tài\s+khoản|tài\s+khoản\s+số|tài\s+khoản|stk|số\s+thẻ|mã\s+vùng)(?:\s+của\s+[\w\s]{1,20}?)?\s+(?:là\s+)?)\b(\d{4,19})\b",
        _repl_keyword_id,
        s,
        flags=re.IGNORECASE,
    )

    # 3. Dates, 24/7, Aspect Ratios & Times (BEFORE leading-zero codes so 05/04/2026 and 08:05 work cleanly)
    s = re.sub(r"\b24\s*/\s*7\b", "hai mươi tư trên bảy", s)
    s = re.sub(r"\b24\s*/\s*24\b", "hai mươi tư trên hai mươi tư", s)

    def _read_month_vi(mo: int) -> str:
        return "tư" if mo == 4 else read_int(mo)

    def _repl_full_date(m):
        prefix = m.group(1) or "ngày"
        d, mo, y = int(m.group(2)), int(m.group(3)), int(m.group(4))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{prefix} {read_int(d)} tháng {_read_month_vi(mo)} năm {read_int(y)}"
        return m.group(0)

    s = re.sub(
        r"(?:(?<!\w)(ngày)\s+)?\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
        _repl_full_date,
        s,
        flags=re.IGNORECASE,
    )

    def _repl_month_year(m):
        prefix = m.group(1)
        mo, y = int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            return f"{prefix} {_read_month_vi(mo)} năm {read_int(y)}"
        return m.group(0)

    s = re.sub(r"(?<!\w)(tháng)\s+(\d{1,2})[/-](\d{4})\b", _repl_month_year, s, flags=re.IGNORECASE)

    def _repl_day_month(m):
        prefix = m.group(1)
        d, mo = int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{prefix} {read_int(d)} tháng {_read_month_vi(mo)}"
        return m.group(0)

    s = re.sub(r"(?<!\w)(ngày)\s+(\d{1,2})[/-](\d{1,2})\b(?![/-]\d)", _repl_day_month, s, flags=re.IGNORECASE)

    # Aspect ratios / proportions (e.g. 'tỷ lệ 16:9', 'tỉ lệ 1:1') BEFORE colon clock times!
    s = re.sub(
        r"((?<!\w)t[ỷỉ]\s+lệ\s+)(\d{1,3})\s*:\s*(\d{1,3})\b",
        lambda m: f"{m.group(1)}{read_int(int(m.group(2)))} {read_int(int(m.group(3)))}",
        s,
        flags=re.IGNORECASE,
    )

    # Explicit Vietnamese hour-minute-second shorthand: 2h15p30s, 8h30p, 8h30, 14h00, 5h
    def _repl_hms_shorthand(m):
        h = int(m.group(1))
        m_str = m.group(2)
        s_str = m.group(3)
        has_p_unit = bool(m.group(4))
        if h > 72 and not has_p_unit:
            return m.group(0)
        parts = [f"{read_int(h)} giờ"]
        if m_str is not None:
            mv = int(m_str)
            if mv > 59:
                return m.group(0)
            if mv > 0 or s_str is not None:
                parts.append(f"{read_int(mv)} phút")
        if s_str is not None:
            sv = int(s_str)
            if sv > 59:
                return m.group(0)
            if sv > 0:
                parts.append(f"{read_int(sv)} giây")
        return " ".join(parts)

    s = re.sub(
        r"\b(\d{1,2})h(?:(\d{1,2})(p|phút|')?(?:(\d{1,2})(?:s|giây|\"))?)?(?![a-zA-Zà-ỹÀ-Ỹ0-9])",
        lambda m: _repl_hms_shorthand_match(m),
        s,
        flags=re.IGNORECASE,
    )

    # Colon clock time HH:MM:SS and HH:MM
    def _repl_colon_hms(m):
        h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
        parts = [f"{read_int(h)} giờ"]
        if mi > 0 or sec > 0:
            parts.append(f"{read_int(mi)} phút")
        if sec > 0:
            parts.append(f"{read_int(sec)} giây")
        return " ".join(parts)

    s = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d):([0-5]\d)\b", _repl_colon_hms, s)

    def _repl_colon_hm(m):
        h, mi = int(m.group(1)), int(m.group(2))
        if mi == 0:
            return f"{read_int(h)} giờ"
        return f"{read_int(h)} giờ {read_int(mi)} phút"

    s = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", _repl_colon_hm, s)

    # Now catch remaining standalone leading-zero digit sequences (0901234567, 048291, 007)
    # and very long >=12-digit unseparated serial/account numbers
    s = re.sub(
        r"(?<![.,:/\d])\b0\d+\b(?![.,:/]\d)",
        lambda m: _read_digit_sequence_grouped(m.group(0)),
        s,
    )
    s = re.sub(
        r"(?<![.,:/\d])\b\d{12,}\b(?![.,:/]\d)",
        lambda m: _read_digit_sequence_grouped(m.group(0)),
        s,
    )

    # 4. Tech protocols, IPv4 addresses & multi-part version numbers (BEFORE thousands/decimals!)
    s = re.sub(r"\bH\.264\b", "hát hai sáu tư", s, flags=re.IGNORECASE)
    s = re.sub(r"\bH\.265\b", "hát hai sáu lăm", s, flags=re.IGNORECASE)
    s = re.sub(r"\bIPv4\b", "ai pi vi bốn", s, flags=re.IGNORECASE)
    s = re.sub(r"\bIPv6\b", "ai pi vi sáu", s, flags=re.IGNORECASE)
    s = re.sub(r"\bHTTP/2\b", "hát tê tê pê hai", s, flags=re.IGNORECASE)
    s = re.sub(r"\bHTTP/3\b", "hát tê tê pê ba", s, flags=re.IGNORECASE)
    s = re.sub(r"\bUI\s*/\s*UX\b", "iu ai iu ích", s, flags=re.IGNORECASE)
    s = re.sub(r"\bCI\s*/\s*CD\b", "xi ai xi đi", s, flags=re.IGNORECASE)

    # IPv4: 192.168.1.1, 8.8.8.8, 10.0.0.1 (exclude 4-group thousands like 1.000.000.000!)
    def _read_ip_octet(o_str: str) -> str:
        val = int(o_str)
        if val < 100:
            return read_int(val)
        return read_digits_verbatim(o_str)

    def _repl_ipv4(m):
        ip_str = m.group(1)
        parts = ip_str.split(".")
        # Exclude 4-group thousands/billions (where trailing 3 groups are all 3-digit numbers like 1.000.000.000)
        if all(len(p) == 3 for p in parts[1:]):
            before = s[:m.start()].lower()[-25:]
            if not any(k in before for k in ("ip", "dns", "host", "máy chủ", "địa chỉ")):
                return m.group(0)
        if any(len(p) > 1 and p.startswith("0") for p in parts):
            return m.group(0)
        return " chấm ".join(_read_ip_octet(p) for p in parts)

    s = re.sub(
        r"\b((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})\b",
        _repl_ipv4,
        s,
    )

    # Prefixed software versions: v2.4.1, v1.6, ver 2.0, phiên bản v1.6
    def _repl_prefixed_version(m):
        ver_parts = m.group(1).split(".")
        spoken = " chấm ".join(
            read_int(int(p)) if idx == 0 or len(p) <= 2 else read_digits_verbatim(p)
            for idx, p in enumerate(ver_parts)
        )
        return f"phiên bản {spoken}"

    s = re.sub(
        r"(?<!\w)(?:phiên\s+bản\s*v|v|ver)\s*(\d+(?:\.\d+)+)\b",
        _repl_prefixed_version,
        s,
        flags=re.IGNORECASE,
    )

    # Unprefixed 3-part SemVer (e.g. Python 3.11.9, iOS 18.2.1), excluding 3-group millions (1.600.000)
    def _repl_semver(m):
        tok = m.group(0)
        parts = tok.split(".")
        if len(parts[1]) == 3 and len(parts[2]) == 3:
            return tok
        return " chấm ".join(read_int(int(p)) for p in parts)

    s = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,4}\b", _repl_semver, s)

    # 5. E-commerce Vietnamese price shorthands & video/mobile network tags (BEFORE generic units/alnum)
    # 2tr5 -> hai triệu rưỡi, 2tr3 -> hai triệu ba trăm nghìn
    def _repl_tr_compound(m):
        tr_part = read_int(int(m.group(1)))
        sub_d = int(m.group(2))
        if sub_d == 5:
            return f"{tr_part} triệu rưỡi"
        if sub_d == 0:
            return f"{tr_part} triệu"
        return f"{tr_part} triệu {ONES[sub_d]} trăm nghìn"

    s = re.sub(r"\b(\d{1,4})\s*tr(?:iệu)?\s*(\d)\b", _repl_tr_compound, s, flags=re.IGNORECASE)
    s = re.sub(r"(\d(?:[\d.,]*\d)?)\s*tr(?:iệu)?\b", r"\1 triệu", s, flags=re.IGNORECASE)

    # 4K / 8K / 199k / 250K -> 'ca' (covers both 4K video resolution and 199k Vietnamese price!)
    s = re.sub(r"(\d(?:[\d.,]*\d)?)\s*[kK]\b", r"\1 ca", s)

    # Mobile generation 3G / 4G / 5G / 6G -> 'gờ' (before lowercase 'g' -> 'gam')
    s = re.sub(r"\b([23456])G\b", lambda m: f"{ONES[int(m.group(1))]} gờ", s)

    # Video resolutions: 1080p, 720p, 480p, 360p, 1440p, 2160p
    _RES_MAP = {
        "360": "ba sáu mươi",
        "480": "bốn tám mươi",
        "720": "bảy hai mươi",
        "1080": "một không tám không",
        "1440": "một bốn bốn không",
        "2160": "hai một sáu không",
    }
    s = re.sub(
        r"\b(360|480|720|1080|1440|2160)p\b",
        lambda m: f"{_RES_MAP[m.group(1)]} pê",
        s,
        flags=re.IGNORECASE,
    )

    # 6. Diameter, technical, physical, area/volume & digital units attached to numbers
    s = re.sub(r"[Øø]\s*(?=\d)", "đường kính ", s)
    s = re.sub(r"(?<=\d)\s*(?:N[\s·.\-]*cm|NCM)\b", " niu tơn xen ti mét", s)
    s = re.sub(r"\bN[·.]?cm\b", "niu tơn xen ti mét", s)
    s = re.sub(r"(?<=\d)\s*(?:N[·.]m|Nm)\b", " niu tơn mét", s)
    s = re.sub(r"(?<=\d)\s*RPM\b", " vòng một phút", s, flags=re.IGNORECASE)
    s = re.sub(r"\bvòng\s*/\s*phút\b", "vòng một phút", s, flags=re.IGNORECASE)

    # Area & volume units (both superscript ²/³ and ASCII 2/3)
    s = re.sub(r"(?<=\d)\s*km(?:²|2\b)", " ki lô mét vuông", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*cm(?:²|2\b)", " xen ti mét vuông", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*mm(?:²|2\b)", " mi li mét vuông", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*m(?:²|2\b)", " mét vuông", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*km(?:³|3\b)", " ki lô mét khối", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*cm(?:³|3\b)", " xen ti mét khối", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*mm(?:³|3\b)", " mi li mét khối", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*m(?:³|3\b)", " mét khối", s, flags=re.IGNORECASE)
    s = re.sub(r"\bm²", "mét vuông", s)
    s = re.sub(r"\bm³", "mét khối", s)

    # Speed, bandwidth, frequency & frame rate
    s = re.sub(r"(?<=\d)\s*km\s*/\s*h\b", " ki lô mét trên giờ", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*m\s*/\s*s\b", " mét trên giây", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*Gbps\b", " gi ga bít trên giây", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*Mbps\b", " mê ga bít trên giây", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*Kbps\b", " ki lô bít trên giây", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*fps\b", " ép pê ét", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*GHz\b", " gi ga héc", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*MHz\b", " mê ga héc", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*kHz\b", " ki lô héc", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*(?:Hz|hertz)\b", " héc", s, flags=re.IGNORECASE)

    # Digital storage units
    s = re.sub(r"(?<=\d)\s*TB\b", " tê ra bai", s)
    s = re.sub(r"(?<=\d)\s*GB\b", " gi ga bai", s)
    s = re.sub(r"(?<=\d)\s*MB\b", " mê ga bai", s)
    s = re.sub(r"(?<=\d)\s*KB\b", " ki lô bai", s)

    # Length, mass, liquid volume, electrical & temperature
    s = re.sub(r"(?<=\d)\s*[µμ]m\b", " mi crô mét", s)
    s = re.sub(r"(?<=\d)\s*mm\b", " mi li mét", s)
    s = re.sub(r"(?<=\d)\s*cm\b", " xen ti mét", s)
    s = re.sub(r"(?<=\d)\s*km\b", " ki lô mét", s)
    s = re.sub(r"(?<=\d)\s*nm\b", " na nô mét", s)
    s = re.sub(r"(?<=\d)(?:(?<=\d)|)\s*mg\b", " mi li gam", s)
    s = re.sub(r"(?<=\d)\s*kg\b", " ki lô gam", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*m[lL]\b", " mi li lít", s)
    s = re.sub(r"(?<=\d)\s*mAh\b", " mi li am pe giờ", s)
    s = re.sub(r"(?<=\d)\s*kWh\b", " ki lô oát giờ", s)
    s = re.sub(r"(?<=\d)\s*kW\b", " ki lô oát", s)
    # Single-letter units attached directly (e.g. 500g, 1,5L, 20m) or spaced with unambiguous case
    s = re.sub(r"(?<=\d)\s*g\b", " gam", s)
    s = re.sub(r"(?<=\d)\s*[lL]\b", " lít", s)
    s = re.sub(r"(?<=\d)m\b", " mét", s)
    s = re.sub(r"(?<=\d)\s*°\s*C\b", " độ xê", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*°\s*F\b", " độ ép", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*°(?![A-Za-z])", " độ", s)

    # Percentages & Currencies (both prefix and postfix, keeping full mixed numeric tokens intact!)
    _NUM_TOK = r"\d(?:[\d.,]*\d)?"
    s = re.sub(rf"({_NUM_TOK})\s*%", r"\1 phần trăm", s)
    # Convert international '$1,250' (single comma + 3 digits, no decimals) to dot thousands before stripping '$'
    def _repl_usd_prefix(m):
        sign = m.group(1) or ""
        num_s = m.group(2)
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", num_s):
            num_s = num_s.replace(",", ".")
        return f"{sign}{num_s} đô la"

    s = re.sub(rf"([+-])?\$\s*({_NUM_TOK})", _repl_usd_prefix, s)
    s = re.sub(rf"([+-])?€\s*({_NUM_TOK})", lambda m: f"{m.group(1) or ''}{m.group(2)} ơ-rô", s)
    s = re.sub(rf"([+-])?¥\s*({_NUM_TOK})", lambda m: f"{m.group(1) or ''}{m.group(2)} yên", s)
    s = re.sub(rf"([+-])?£\s*({_NUM_TOK})", lambda m: f"{m.group(1) or ''}{m.group(2)} bảng Anh", s)
    s = re.sub(rf"({_NUM_TOK})\s*\$", r"\1 đô la", s)
    s = re.sub(
        rf"({_NUM_TOK})\s*USD\b",
        lambda m: (m.group(1).replace(",", ".") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", m.group(1)) else m.group(1)) + " đô la Mỹ",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(rf"({_NUM_TOK})\s*EUR\b", r"\1 ơ-rô", s, flags=re.IGNORECASE)
    s = re.sub(rf"({_NUM_TOK})\s*(?:đ|vnđ|vnd)\b", r"\1 đồng", s, flags=re.IGNORECASE)

    # Per-unit slash expressions: '/năm', '/tháng', '/ngày', '/người', '/lượt', '/m²'
    s = re.sub(
        r"\s*/\s*(năm|tháng|tuần|ngày|đêm|giờ|phút|giây|người|lượt|chiếc|cái|suất|kỳ|gói|vé|phần)\b",
        r" một \1",
        s,
        flags=re.IGNORECASE,
    )

    # 7. Scores, Ranges, Multiplications & Fractions (BEFORE negative sign parsing!)
    # 7a. Match scores: 'tỷ số 2-1', 'chung cuộc 3-0', 'hòa 1-1'
    s = re.sub(
        r"((?<!\w)(?:t[ỷỉ]\s+số|chung\s+cuộc|hòa)\s+)(\d{1,2})\s*[-–—]\s*(\d{1,2})\b",
        lambda m: f"{m.group(1)}{read_int(int(m.group(2)))} {read_int(int(m.group(3)))}",
        s,
        flags=re.IGNORECASE,
    )

    # 7b. Ranges where the left side already expanded its unit/percent (e.g. '4,8 phần trăm - 6,25 phần trăm', '15 triệu - 20 triệu')
    s = re.sub(
        rf"({_NUM_TOK}\s+(?:phần trăm|triệu|tỷ|nghìn|ca|đồng|đô la|đô la Mỹ|độ|độ xê))\s*[-–—]\s*(?=\d)",
        r"\1 đến ",
        s,
    )

    # 7c. Direct numeric ranges: '6-12', '10-15 phần trăm', '2024-2026' (only when NOT preceded by '-' sign)
    s = re.sub(
        rf"(?<![-\w.,])({_NUM_TOK})\s*[-–—]\s*({_NUM_TOK})(?![.,]\d)",
        r"\1 đến \2",
        s,
    )

    # 7d. Multiplications / dimensions: '2x3', '4,2x13', '1920x1080'
    s = re.sub(rf"({_NUM_TOK})\s*[xX×]\s*({_NUM_TOK})", r"\1 nhân \2", s)

    # 7e. Fractions & X/Y ratings ('2/3 thị phần' -> 'hai phần ba', '3/4' -> 'ba phần tư', '9/10 điểm' -> 'chín trên mười')
    def _repl_fraction_or_score(m):
        num = int(m.group(1))
        den = int(m.group(2))
        after = s[m.end():m.end() + 20].lstrip().lower()
        before = s[max(0, m.start() - 20):m.start()].lower()
        is_score = (
            num >= den
            or den in (10, 100) and (after.startswith(("điểm", "câu", "trận", "sao", "tiêu chí")) or "đạt" in before)
        )
        if is_score:
            return f"{read_int(num)} trên {read_int(den)}"
        den_words = "tư" if (den == 4 and num < 4) else read_int(den)
        return f"{read_int(num)} phần {den_words}"

    s = re.sub(r"(?<![/\w.,])\b(\d{1,3})\s*/\s*(\d{1,3})\b(?![/\w.,])", _repl_fraction_or_score, s)

    # 8. Unified Atomic Number Parser (Signed & Unsigned: Mixed, Space-Thousands, Dot-Thousands, Decimals, Integers)
    # 8a. Space-separated thousands (e.g. '1 500 000', '25 000')
    def _repl_space_thousands(m):
        sign = _resolve_sign_prefix(m.group(1), s, m.start())
        int_str = m.group(2).replace(" ", "")
        dec_str = m.group(3)
        int_w = read_int(int(int_str))
        if dec_str is not None:
            return f"{sign}{int_w} phẩy {read_digits_verbatim(dec_str)}"
        return f"{sign}{int_w}"

    s = re.sub(
        r"(?:(?<=^)|(?<=[\s(\[{]))([+-])?(\d{1,3}(?: \d{3}){2,}|\d{1,3} 000)(?:,(\d+))?\b",
        _repl_space_thousands,
        s,
    )

    # 8b. Vietnamese mixed thousands + comma decimal: 1.284,65 / -18.450,75
    def _repl_vn_mixed(m):
        sign = _resolve_sign_prefix(m.group(1), s, m.start())
        int_w = read_int(int(m.group(2).replace(".", "")))
        dec_w = read_digits_verbatim(m.group(3))
        return f"{sign}{int_w} phẩy {dec_w}"

    s = re.sub(
        r"(?:(?<=^)|(?<=[\s(\[{]))([+-])?(\d{1,3}(?:\.\d{3})+),(\d+)\b",
        _repl_vn_mixed,
        s,
    )

    # 8c. International mixed thousands + dot decimal: 1,234.56 / 1,250.50
    def _repl_en_mixed(m):
        sign = _resolve_sign_prefix(m.group(1), s, m.start())
        int_w = read_int(int(m.group(2).replace(",", "")))
        dec_w = read_digits_verbatim(m.group(3))
        return f"{sign}{int_w} chấm {dec_w}"

    s = re.sub(
        r"(?:(?<=^)|(?<=[\s(\[{]))([+-])?(\d{1,3}(?:,\d{3})+)\.(\d+)\b",
        _repl_en_mixed,
        s,
    )

    # 8d. International multi-group comma thousands: 1,200,000
    def _repl_en_thousands(m):
        sign = _resolve_sign_prefix(m.group(1), s, m.start())
        return f"{sign}{read_int(int(m.group(2).replace(',', '')))}"

    s = re.sub(
        r"(?:(?<=^)|(?<=[\s(\[{]))([+-])?(\d{1,3}(?:,\d{3}){2,})\b",
        _repl_en_thousands,
        s,
    )

    # 8e. Vietnamese dot thousands: 1.600, 25.000, 12.050.000.000
    def _repl_vn_thousands(m):
        sign = _resolve_sign_prefix(m.group(1), s, m.start())
        return f"{sign}{read_int(int(m.group(2).replace('.', '')))}"

    s = re.sub(
        r"(?:(?<=^)|(?<=[\s(\[{]))([+-])?(\d{1,3}(?:\.\d{3})+)\b(?![.,]\d)",
        _repl_vn_thousands,
        s,
    )

    # 8f. Comma decimals (digit-by-digit per user preference): 1,3456, 18,4, -3,25, +0,45
    def _repl_comma_dec(m):
        sign = _resolve_sign_prefix(m.group(1), s, m.start())
        int_w = read_int(int(m.group(2)))
        dec_w = read_digits_verbatim(m.group(3))
        return f"{sign}{int_w} phẩy {dec_w}"

    s = re.sub(
        r"(?:(?<=^)|(?<=[\s(\[{]))([+-])?(\d+),(\d+)\b",
        _repl_comma_dec,
        s,
    )

    # 8g. Dot decimals: 1.6, 49.99, -0.5
    def _repl_dot_dec(m):
        sign = _resolve_sign_prefix(m.group(1), s, m.start())
        int_w = read_int(int(m.group(2)))
        dec_w = read_digits_verbatim(m.group(3))
        return f"{sign}{int_w} chấm {dec_w}"

    s = re.sub(
        r"(?:(?<=^)|(?<=[\s(\[{]))([+-])?(\d+)\.(\d+)\b",
        _repl_dot_dec,
        s,
    )

    # 9. Domain-specific scientific / tech terms & Alphanumeric codes / Acronyms
    s = re.sub(r"\bCRISPR\s*[-–—]\s*Cas9\b", "crít spơ cát chín", s, flags=re.IGNORECASE)
    s = re.sub(r"\bCRISPR\b", "crít spơ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bCas9\b", "cát chín", s, flags=re.IGNORECASE)
    s = re.sub(r"\bType-C\b", "thai xi", s, flags=re.IGNORECASE)
    s = re.sub(r"\bWi-Fi\b", "oai phai", s, flags=re.IGNORECASE)

    # 9a. Mixed uppercase+digit technical/product codes (e.g. MIS-C1XD, C1XD, C1, 3D, M4, MP3, MP4)
    s = re.sub(
        r"\b(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9]+(?:-[A-Z0-9]+)*\b",
        lambda m: _expand_alnum_code(m.group(0)),
        s,
    )

    # 9b. ALL-CAPS acronyms (2-5 chars, plus optional single-letter hyphen suffix like USB-C)
    if any(c.islower() for c in s) or len(s.strip(".,!?;: ")) <= 6:
        def _repl_acronym(m):
            tok = m.group(0)
            if tok in SPECIAL_ACRONYMS_VI:
                return SPECIAL_ACRONYMS_VI[tok]
            if tok in WORD_ACRONYMS:
                return tok.lower()
            return _expand_alnum_code(tok)

        s = re.sub(r"\b[A-Z]{2,5}(?:-[A-Z]{1,5})*\b", _repl_acronym, s)

    # 9c. Common English technical terms inside Vietnamese medical/engineering dubbing
    s = re.sub(r"\bPilot\b", "pai lốt", s)

    # 10. Remaining signed & unsigned standalone integers (including numbers attached to lowercase words if any)
    def _repl_signed_int(m):
        sign = _resolve_sign_prefix(m.group(1), s, m.start())
        return f"{sign}{read_int(int(m.group(2)))}"

    s = re.sub(r"(?:(?<=^)|(?<=[\s(\[{]))([+-])?(\d+)\b", _repl_signed_int, s)

    # Fallback for any remaining digit cluster attached to letters (e.g. covid19, b2b, q3, win11)
    s = re.sub(r"\d+", lambda m: " " + read_int(int(m.group(0))) + " ", s)

    # Restore protected OmniVoice inline control tags
    for idx, tag_val in enumerate(protected_tags):
        s = s.replace(f"\x02ovtag_{_idx_to_alpha(idx)}\x03", tag_val)

    # Collapse repeated whitespace & clean spaces before punctuation
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    orig_first = text.lstrip()[0] if text and text.lstrip() else ""
    if s and orig_first.isalpha() and orig_first.isupper() and s[0].isalpha():
        s = s[0].upper() + s[1:]
    return s


def _repl_hms_shorthand_match(m) -> str:
    """Helper for 8h30 / 8h30p / 2h15p30s / 14h00 shorthand matching."""
    h = int(m.group(1))
    m_str = m.group(2)
    p_tok = m.group(3)
    s_str = m.group(4)
    if h > 72 and not p_tok:
        return m.group(0)
    parts = [f"{read_int(h)} giờ"]
    if m_str is not None:
        mv = int(m_str)
        if mv > 59:
            return m.group(0)
        if mv > 0 or s_str is not None:
            parts.append(f"{read_int(mv)} phút")
    if s_str is not None:
        sv = int(s_str)
        if sv > 59:
            return m.group(0)
        if sv > 0:
            parts.append(f"{read_int(sv)} giây")
    return " ".join(parts)


_VI_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")
_ASCII_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")
_HIATUS_RE = re.compile(r"(?:ia|io|eo|ua(?=[a-z]))")


def count_vietnamese_spoken_syllables(text: str, already_normalized: bool = False) -> int:
    """
    Counts spoken syllables in Vietnamese TTS text AFTER phonetic normalization
    (so '10 Ncm' -> 6 syllables, '10mm' -> 4, 'MIS' -> 3, 'implant' -> 2, 'abutment' -> 3).
    """
    if not text or not isinstance(text, str):
        return 0
    s = text if already_normalized else normalize_vietnamese_text(text)
    s = re.sub(r"\[[^\[\]\n]{1,64}\]", " ", s)
    tokens = re.findall(r"[^\s\-–—/]+", s)
    total = 0
    for raw_tok in tokens:
        tok = re.sub(r"^[^\w]+|[^\w]+$", "", raw_tok, flags=re.UNICODE)
        if not tok:
            continue
        if _VI_NON_ASCII_RE.search(tok):
            total += 1
            continue
        w = tok.lower()
        if w.isdigit():
            total += max(1, len(read_int(int(w)).split()))
            continue
        groups = _ASCII_VOWEL_GROUP_RE.findall(w)
        if len(groups) <= 1:
            total += 1
            continue
        syl = len(groups)
        if w.endswith("e") and not w.endswith(("le", "ye", "ee", "oe", "ae")) and syl > 1:
            syl -= 1
        if _HIATUS_RE.search(w) and len(w) >= 5:
            syl += 1
        total += max(1, syl)
    return total


def estimate_vietnamese_spoken_duration(
    text: str,
    speed: float = 0.92,
    already_normalized: bool = False,
) -> float:
    """
    Estimates actual spoken audio duration (seconds) for OmniVoice native Vietnamese TTS.
    Empirically calibrated on studio Vietnamese narration (R^2 = 0.956, MAE = 0.165s at speed=0.96).
    """
    if not text or not isinstance(text, str) or not text.strip():
        return 0.0
    norm = text if already_normalized else normalize_vietnamese_text(text)
    norm_no_tags = re.sub(r"\[[^\[\]\n]{1,64}\]", " ", norm).strip()
    if not norm_no_tags:
        return 0.0
    syllables = count_vietnamese_spoken_syllables(norm_no_tags, already_normalized=True)
    if syllables <= 0:
        return 0.0
    commas = len(re.findall(r"[,;:]", norm_no_tags))
    internal_breaks = len(re.findall(r"[.!?]+(?=\s+\S)", norm_no_tags))
    base_at_096 = 0.2015 * syllables + 0.2174 * commas + 0.34 * internal_breaks + 0.0544
    safe_speed = max(0.5, min(2.0, float(speed or 0.92)))
    est = base_at_096 * ((0.96 / safe_speed) ** 0.55)
    return round(max(0.25, est), 3)


def estimate_spoken_duration(
    text: str,
    target_language: str = "Vietnamese",
    speed: float = 0.92,
) -> float:
    """Language-aware spoken duration estimator for dubbing translation calibration."""
    if not text or not isinstance(text, str) or not text.strip():
        return 0.0
    lang_low = (target_language or "vietnamese").strip().lower()
    if lang_low in ("vi", "vietnamese", "tiếng việt") or "viet" in lang_low or "việt" in lang_low:
        return estimate_vietnamese_spoken_duration(text, speed=speed)
    clean = re.sub(r"\[[^\[\]\n]{1,64}\]", " ", text).strip()
    if not clean:
        return 0.0
    safe_speed = max(0.5, min(2.0, float(speed or 0.96)))
    scale = 0.96 / safe_speed
    if any(k in lang_low for k in ("chinese", "zh", "japanese", "ja")):
        chars = len(re.sub(r"[\s,.!?;:，。！？；：、\-]+", "", clean))
        pauses = len(re.findall(r"[,;:.!?，；：。！？]", clean))
        return round(max(0.25, (chars / 5.4 + pauses * 0.18) * scale), 3)
    if any(k in lang_low for k in ("korean", "ko")):
        chars = len(re.sub(r"[\s,.!?;:\-]+", "", clean))
        pauses = len(re.findall(r"[,;:.!?]", clean))
        return round(max(0.25, (chars / 4.7 + pauses * 0.18) * scale), 3)
    words = len(clean.split())
    pauses = len(re.findall(r"[,;:]", clean))
    return round(max(0.25, (words / 2.65 + pauses * 0.18 + 0.08) * scale), 3)


def compute_segment_timing_budget(
    orig_dur: float,
    gap_after: float = 0.0,
    speed: float = 0.92,
) -> dict:
    """
    Computes safe duration & spoken-syllable budgets for a Vietnamese dubbing segment,
    incorporating Smart Gap-Borrowing into trailing silence so meaning never needs to be truncated.
    """
    slot_dur = max(0.4, float(orig_dur or 0.4))
    clean_gap = max(0.0, float(gap_after or 0.0))
    safe_borrow = min(0.80, max(0.0, clean_gap - 0.30) * 0.42)
    allowed_dur = round(slot_dur + safe_borrow, 3)

    safe_speed = max(0.5, min(2.0, float(speed or 0.92)))
    syl_sec = 0.2015 * ((0.96 / safe_speed) ** 0.55)
    pause_overhead = (0.22 if slot_dur >= 3.5 else 0.0) + 0.055

    min_dur = slot_dur * (0.86 if slot_dur >= 2.8 else 0.74)
    ideal_dur = slot_dur * 0.97
    max_dur = allowed_dur * 1.02

    min_syl = max(2, int(round((min_dur - pause_overhead) / syl_sec)))
    ideal_syl = max(min_syl, int(round((ideal_dur - pause_overhead) / syl_sec)))
    max_syl = max(ideal_syl + 1, int(round((max_dur - pause_overhead) / syl_sec)))

    return {
        "slot_dur": round(slot_dur, 3),
        "gap_after": round(clean_gap, 3),
        "safe_borrow": round(safe_borrow, 3),
        "allowed_dur": allowed_dur,
        "min_syl": min_syl,
        "ideal_syl": ideal_syl,
        "max_syl": max_syl,
    }
