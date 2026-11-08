"""Služby, negativní odpovědi a bezpečnostní politika (co se smí poslat)."""
from __future__ import annotations

NRC = {
    0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength", 0x14: "responseTooLong", 0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect", 0x24: "requestSequenceError", 0x31: "requestOutOfRange",
    0x33: "securityAccessDenied", 0x35: "invalidKey", 0x72: "generalProgrammingFailure",
    0x78: "responsePending", 0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession", 0x80: "serviceNotSupportedInActiveSession(KWP)",
}
NRC_CS = {
    0x11: "služba nepodporována", 0x12: "podfunkce/formát nepodporován",
    0x13: "nesprávná délka zprávy", 0x22: "nesplněné podmínky", 0x31: "mimo rozsah",
    0x33: "vyžaduje zabezpečený přístup", 0x78: "odpověď se připravuje",
    0x7E: "podfunkce nepodporována v aktuální relaci", 0x7F: "služba nepodporována v aktuální relaci",
    0x80: "služba nepodporována v aktuální relaci",
}

# Čtecí služby. 0x10 jen s bezpečnými relacemi, 0x14 (mazání) jen přes clear_dtc().
READ_ONLY_SIDS = {0x10, 0x12, 0x13, 0x17, 0x18, 0x19, 0x1A, 0x20, 0x21, 0x22, 0x3E, 0x01, 0x03, 0x07, 0x09, 0x0A}
SAFE_SESSIONS = {0x01, 0x03, 0x81}  # default, extended (UDS), KWP default. NIKDY 0x02/0x85 (programování).
CLEAR_SIDS = {0x14, 0x04}  # UDS/KWP clear, OBD-II mód 04


class SafetyError(PermissionError):
    pass


def check_request(payload: bytes, allow_clear: bool = False) -> None:
    if not payload:
        raise SafetyError("Prázdný požadavek.")
    sid = payload[0]
    if sid in CLEAR_SIDS:
        if allow_clear:
            return
        raise SafetyError("Mazání DTC (0x14/0x04) je možné jen přes potvrzenou akci 'Smazat DTC'.")
    if sid not in READ_ONLY_SIDS:
        raise SafetyError(f"Služba 0x{sid:02X} není čtecí – FionaDiag ji neodešle "
                          "(žádné zápisy, kódování, flash ani testy akčních členů).")
    if sid == 0x10 and (len(payload) < 2 or payload[1] not in SAFE_SESSIONS):
        raise SafetyError("Povolené relace jsou jen 10 01, 10 03 a 10 81.")


def describe(request: bytes, msg: bytes | None) -> str:
    if msg is None:
        return "bez odpovědi"
    if len(msg) >= 3 and msg[0] == 0x7F:
        return f"NRC 0x{msg[2]:02X} {NRC.get(msg[2], '?')} ({NRC_CS.get(msg[2], '')})".replace(" ()", "")
    if msg and request and msg[0] == request[0] + 0x40:
        return "OK " + msg.hex(" ").upper()
    return "? " + msg.hex(" ").upper()


def hx(b: bytes | None) -> str:
    return "" if b is None else b.hex(" ").upper()
