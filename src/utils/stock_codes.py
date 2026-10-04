"""KRX short stock codes include uppercase ASCII letters as well as digits."""

import re

KRX_SHORT_CODE_REGEX = r"^[0-9A-Z]{6}$"
KRX_SHORT_CODE_PATTERN = re.compile(KRX_SHORT_CODE_REGEX)


def is_stock_code(value: object) -> bool:
    return isinstance(value, str) and KRX_SHORT_CODE_PATTERN.fullmatch(value) is not None
