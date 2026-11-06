"""NSICCS ARQC simulator.

Option A UDK, ATC session key, then ISO 9797-1 MAC algorithm 3.
The MAC input is CDOL1, then AIP, ATC, and the full Issuer Application Data.
"""
from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", message=".*TripleDES.*")

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

# Amount, other amount, country, TVR, currency, date, type, unpredictable number,
# then AIP, ATC, and IAD. Values only, no tag or length.
_MAC_TAGS = ("9F02", "9F03", "9F1A", "95", "5F2A", "9A", "9C", "9F37", "82", "9F36", "9F10")


def _tdes_ecb(key: bytes, block: bytes) -> bytes:
    encryptor = Cipher(TripleDES(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def _des_ecb(key8: bytes, block: bytes, decrypt: bool = False) -> bytes:
    # K||K is single DES. NSICCS MAC uses DES, not the full session key, per block.
    cipher = Cipher(TripleDES(key8 + key8), modes.ECB())
    worker = cipher.decryptor() if decrypt else cipher.encryptor()
    return worker.update(block) + worker.finalize()


def _unhex(value: str, label: str) -> bytes:
    cleaned = "".join(value.split())
    if len(cleaned) % 2 != 0 or any(c not in "0123456789abcdefABCDEF" for c in cleaned):
        raise ValueError(f"{label} harus hex genap")
    return bytes.fromhex(cleaned)


def derive_udk(imk: bytes, pan: str, psn: str) -> bytes:
    """EMV option A. NSICCS uses the rightmost 16 digits of PAN || PSN."""
    if len(imk) != 16:
        raise ValueError("IMK-AC harus 16 byte (32 hex)")
    digits = "".join(ch for ch in pan if ch.isdigit())
    seq = "".join(ch for ch in psn if ch.isdigit())
    if len(seq) != 2:
        raise ValueError("PAN sequence harus 2 digit")
    joined = digits + seq
    if len(joined) < 2:
        raise ValueError("PAN tidak ditemukan")
    y_hex = joined[-16:].zfill(16)
    y = bytes.fromhex(y_hex)
    inverted = bytes(b ^ 0xFF for b in y)
    return _tdes_ecb(imk, y) + _tdes_ecb(imk, inverted)


def derive_session_key(udk: bytes, atc: bytes) -> bytes:
    if len(udk) != 16:
        raise ValueError("UDK harus 16 byte")
    if len(atc) != 2:
        raise ValueError("ATC harus 2 byte")
    left = _tdes_ecb(udk, atc + bytes.fromhex("F00000000000"))
    right = _tdes_ecb(udk, atc + bytes.fromhex("0F0000000000"))
    return left + right


def mac_algorithm_3(session_key: bytes, data: bytes) -> bytes:
    """ISO 9797-1 algorithm 3, padding method 2."""
    if len(session_key) != 16:
        raise ValueError("Session key harus 16 byte")
    padded = data + b"\x80"
    if len(padded) % 8:
        padded += b"\x00" * (8 - (len(padded) % 8))
    ksl, ksr = session_key[:8], session_key[8:]
    state = bytes(8)
    for offset in range(0, len(padded), 8):
        block = padded[offset : offset + 8]
        mixed = bytes(a ^ b for a, b in zip(block, state))
        state = _des_ecb(ksl, mixed)
    state = _des_ecb(ksr, state, decrypt=True)
    return _des_ecb(ksl, state)


def _parse_tlvs(body: bytes) -> dict[str, bytes]:
    tags: dict[str, bytes] = {}
    index = 0
    while index < len(body):
        first = body[index]
        index += 1
        tag = bytes([first])
        if first & 0x1F == 0x1F:
            if index >= len(body):
                break
            tag += bytes([body[index]])
            index += 1
            while tag[-1] & 0x80:
                if index >= len(body):
                    break
                tag += bytes([body[index]])
                index += 1
        if index >= len(body):
            break
        length = body[index]
        index += 1
        if length & 0x80:
            count = length & 0x7F
            if index + count > len(body):
                break
            length = int.from_bytes(body[index : index + count], "big")
            index += count
        if index + length > len(body):
            break
        tags[tag.hex().upper()] = body[index : index + length]
        index += length
    return tags


def _replace_tag(body: bytes, tag: bytes, value: bytes) -> bytes:
    index = 0
    while index < len(body):
        first = body[index]
        index += 1
        found = bytes([first])
        if first & 0x1F == 0x1F:
            if index >= len(body):
                break
            found += bytes([body[index]])
            index += 1
            while found[-1] & 0x80:
                if index >= len(body):
                    break
                found += bytes([body[index]])
                index += 1
        if index >= len(body):
            break
        length = body[index]
        index += 1
        if length & 0x80:
            count = length & 0x7F
            if index + count > len(body):
                break
            length = int.from_bytes(body[index : index + count], "big")
            index += count
        if index + length > len(body):
            break
        if found == tag:
            if length != len(value):
                raise ValueError("Panjang 9F26 harus tetap 8 byte")
            return body[:index] + value + body[index + length :]
        index += length
    raise ValueError("Tag 9F26 tidak ada di DE55")


def _pan_from_5a(value: bytes) -> str:
    hex_pan = value.hex().upper()
    return hex_pan[:-1] if hex_pan.endswith("F") else hex_pan


def _psn_from_5f34(value: bytes | None) -> str:
    if not value:
        return "00"
    return f"{value[0]:02d}"


def simulate_de55(de55: bytes, imk_hex: str, pan: str | None = None, psn: str | None = None) -> dict:
    imk = _unhex(imk_hex, "IMK-AC")
    tags = _parse_tlvs(de55)
    missing = [tag for tag in _MAC_TAGS if tag not in tags]
    if missing:
        raise ValueError("DE55 kurang tag " + ", ".join(missing))
    pan_digits = _pan_from_5a(tags["5A"]) if tags.get("5A") else (pan or "")
    seq = psn or _psn_from_5f34(tags.get("5F34"))
    udk = derive_udk(imk, pan_digits, seq)
    atc = tags["9F36"]
    session_key = derive_session_key(udk, atc)
    mac_input = b"".join(tags[tag] for tag in _MAC_TAGS)
    arqc = mac_algorithm_3(session_key, mac_input)
    current = tags.get("9F26", b"")
    updated = _replace_tag(de55, bytes.fromhex("9F26"), arqc)
    return {
        "arqc": arqc.hex().upper(),
        "current_arqc": current.hex().upper(),
        "match": current == arqc,
        "atc": atc.hex().upper(),
        "mac_input": mac_input.hex().upper(),
        "udk": udk.hex().upper(),
        "session_key": session_key.hex().upper(),
        "de55": updated,
    }


def verify_public_vector() -> dict:
    """Published NSICCS tutorial vector. Confirms the formula, not a live card."""
    imk = bytes.fromhex("101112131415161718191A1B1C1D1E1F")
    udk = derive_udk(imk, "6019002502870977", "01")
    session_key = derive_session_key(udk, bytes.fromhex("0008"))
    data = bytes.fromhex(
        "00008976540000000000000003600000B000000360080804000FBBE0D5540000080101A00000000066028C0000"
    )
    arqc = mac_algorithm_3(session_key, data)
    expected_udk = "3A14850C664FCFAA902D3A3A3866AC2E"
    expected_sk = "DF1498F8A0926F047CD6683C796BCD58"
    expected_arqc = "B0B88E21B9734BB5"
    ok = (
        udk.hex().upper() == expected_udk
        and session_key.hex().upper() == expected_sk
        and arqc.hex().upper() == expected_arqc
    )
    return {
        "ok": ok,
        "arqc": arqc.hex().upper(),
        "expected_arqc": expected_arqc,
    }
