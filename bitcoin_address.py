"""Bitcoin mainnet address validation helpers."""

from __future__ import annotations

import hashlib


BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32_CHARSET_MAP = {char: index for index, char in enumerate(BECH32_CHARSET)}
BECH32_CONST = 1
BECH32M_CONST = 0x2BC830A3


def _base58_decode(address: str) -> bytes:
    value = 0
    for char in address:
        if char not in BASE58_ALPHABET:
            raise ValueError("Invalid Base58 character.")
        value = (value * 58) + BASE58_ALPHABET.index(char)
    raw = value.to_bytes((value.bit_length() + 7) // 8, byteorder="big")
    leading = len(address) - len(address.lstrip("1"))
    return b"\x00" * leading + raw


def _validate_base58_address(address: str) -> bool:
    if not address or address[0] not in {"1", "3"}:
        return False
    try:
        decoded = _base58_decode(address)
    except ValueError:
        return False
    if len(decoded) != 25:
        return False
    checksum = decoded[-4:]
    body = decoded[:-4]
    expected = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
    return checksum == expected


def _bech32_polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for index in range(5):
            if (top >> index) & 1:
                checksum ^= generator[index]
    return checksum


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _bech32_verify_checksum(hrp: str, data: list[int]) -> str | None:
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if polymod == BECH32_CONST:
        return "bech32"
    if polymod == BECH32M_CONST:
        return "bech32m"
    return None


def _convertbits(data: list[int], from_bits: int, to_bits: int, pad: bool) -> list[int] | None:
    accumulator = 0
    bits = 0
    result: list[int] = []
    max_value = (1 << to_bits) - 1
    max_accumulator = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            return None
        accumulator = ((accumulator << from_bits) | value) & max_accumulator
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((accumulator >> bits) & max_value)
    if pad:
        if bits:
            result.append((accumulator << (to_bits - bits)) & max_value)
    elif bits >= from_bits or ((accumulator << (to_bits - bits)) & max_value):
        return None
    return result


def _validate_bech32_address(address: str) -> bool:
    if address.lower() != address and address.upper() != address:
        return False
    candidate = address.lower()
    if not candidate.startswith("bc1"):
        return False
    separator = candidate.rfind("1")
    if separator < 1 or separator + 7 > len(candidate):
        return False
    hrp = candidate[:separator]
    data = []
    for char in candidate[separator + 1 :]:
        value = BECH32_CHARSET_MAP.get(char)
        if value is None:
            return False
        data.append(value)
    spec = _bech32_verify_checksum(hrp, data)
    if spec is None:
        return False
    values = data[:-6]
    if not values:
        return False
    witness_version = values[0]
    if witness_version > 16:
        return False
    program = _convertbits(values[1:], 5, 8, False)
    if program is None or len(program) < 2 or len(program) > 40:
        return False
    if witness_version == 0 and len(program) not in {20, 32}:
        return False
    if witness_version == 0 and spec != "bech32":
        return False
    if witness_version != 0 and spec != "bech32m":
        return False
    return True


def validate_bitcoin_address(address: str) -> bool:
    candidate = address.strip()
    if candidate.lower().startswith("bc1"):
        return _validate_bech32_address(candidate)
    return _validate_base58_address(candidate)
