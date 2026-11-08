"""Zwei-Faktor-Anmeldung ohne Zusatzpakete (außer cryptography):
- TOTP (Authenticator-App, RFC 6238) mit QR-Code als SVG
- Passkeys (WebAuthn Level 2, Attestation "none")
- Wiederherstellungscodes
"""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote


# ---------------------------------------------------------------- Base64url
def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    s = s or ""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- TOTP
def totp_new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_now(secret: str, t=None) -> str:
    return _totp_at(secret, int((t or time.time()) // 30))


def totp_verify(secret: str, code: str, last_counter=None, t=None):
    """Prüft einen 6-stelligen Code (±30 s). Gibt den Zähler zurück oder None.
    Ein schon benutzter Zähler (last_counter) gilt nicht noch einmal."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6 or not secret:
        return None
    now = int((t or time.time()) // 30)
    for c in (now - 1, now, now + 1):
        if hmac.compare_digest(_totp_at(secret, c), code):
            if last_counter is not None and c <= last_counter:
                return None
            return c
    return None


def totp_uri(secret: str, account: str, issuer: str) -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")


# ---------------------------------------------------------------- Wiederherstellungscodes
RC_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def recovery_new(n=10):
    return ["-".join("".join(secrets.choice(RC_ALPHABET) for _ in range(4)) for _ in range(2)) for _ in range(n)]


def recovery_hash(code: str) -> str:
    norm = "".join(ch for ch in (code or "").lower() if ch.isalnum())
    return hashlib.sha256(norm.encode()).hexdigest()


# ---------------------------------------------------------------- QR-Code (Byte-Modus, Fehlerkorrektur M)
# Version: (EC-Codewörter je Block, [(Blöcke, Datencodewörter je Block), ...])
_QR_M = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
         6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
         10: (26, [(4, 43), (1, 44)]), 11: (30, [(1, 50), (4, 51)]), 12: (22, [(6, 36), (2, 37)]),
         13: (22, [(8, 37), (1, 38)]), 14: (24, [(4, 40), (5, 41)]), 15: (24, [(5, 41), (5, 42)])}
_QR_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
             9: [6, 26, 46], 10: [6, 28, 50], 11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62],
             14: [6, 26, 46, 66], 15: [6, 26, 48, 70]}


def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    res = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < degree:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 0x02)
    return res


def _rs_remainder(data, divisor):
    res = [0] * len(divisor)
    for b in data:
        factor = b ^ res.pop(0)
        res.append(0)
        for i, coef in enumerate(divisor):
            res[i] ^= _gf_mul(coef, factor)
    return res


def qr_matrix(text: str):
    data = text.encode("utf-8")
    for ver in range(1, 16):
        ec_len, groups = _QR_M[ver]
        cap = sum(n * k for n, k in groups)
        cc_bits = 8 if ver < 10 else 16
        if 4 + cc_bits + 8 * len(data) <= cap * 8:
            break
    else:
        raise ValueError("Text zu lang für den QR-Code")
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in reversed(range(cc_bits))]
    for byte in data:
        bits += [(byte >> i) & 1 for i in reversed(range(8))]
    bits += [0] * min(4, cap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(cw) < cap:
        cw.append(pad)
        pad ^= 0xEC ^ 0x11
    # Blöcke + Fehlerkorrektur, verschränkt
    div = _rs_divisor(ec_len)
    blocks, pos = [], 0
    for n, k in groups:
        for _ in range(n):
            d = cw[pos:pos + k]
            pos += k
            blocks.append((d, _rs_remainder(d, div)))
    final = []
    for i in range(max(len(d) for d, _ in blocks)):
        final += [d[i] for d, _ in blocks if i < len(d)]
    for i in range(ec_len):
        final += [e[i] for _, e in blocks]

    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    al = _QR_ALIGN[ver]
    for i, ax in enumerate(al):
        for j, ay in enumerate(al):
            if (i == 0 and j == 0) or (i == 0 and j == len(al) - 1) or (i == len(al) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        d = (0 << 3) | mask                         # M = 00
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        b = ((d << 10) | rem) ^ 0x5412
        g = lambda i: (b >> i) & 1 == 1
        for i in range(6):
            setf(8, i, g(i))
        setf(8, 7, g(6))
        setf(8, 8, g(7))
        setf(7, 8, g(8))
        for i in range(9, 15):
            setf(14 - i, 8, g(i))
        for i in range(8):
            setf(size - 1 - i, 8, g(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, g(i))
        setf(8, size - 8, True)

    draw_format(0)
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        b = (ver << 12) | rem
        for i in range(18):
            bit = (b >> i) & 1 == 1
            a, c = size - 11 + i % 3, i // 3
            setf(a, c, bit)
            setf(c, a, bit)
    # Daten im Zickzack
    i, right = 0, size - 1
    total = len(final) * 8
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                up = ((right + 1) & 2) == 0
                y = size - 1 - vert if up else vert
                if not fn[y][x] and i < total:
                    mod[y][x] = (final[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2

    masks = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]

    def apply(mk):
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and masks[mk](x, y):
                    mod[y][x] = not mod[y][x]

    def penalty():
        p = 0
        for grid in (mod, list(map(list, zip(*mod)))):
            for row in grid:
                run, prev = 0, None
                for v in row:
                    if v == prev:
                        run += 1
                    else:
                        if run >= 5:
                            p += run - 2
                        run, prev = 1, v
                if run >= 5:
                    p += run - 2
                s = "".join("1" if v else "0" for v in row)
                p += 40 * (s.count("10111010000") + s.count("00001011101"))
        for y in range(size - 1):
            for x in range(size - 1):
                if mod[y][x] == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                    p += 3
        dark = sum(v for row in mod for v in row)
        p += abs(dark * 20 - size * size * 10) // (size * size) * 10
        return p

    best = None
    for mk in range(8):
        apply(mk)
        draw_format(mk)
        sc = penalty()
        if best is None or sc < best[0]:
            best = (sc, mk)
        apply(mk)               # zurück (XOR)
    apply(best[1])
    draw_format(best[1])
    return mod


def qr_svg(text: str, scale=6, border=4) -> str:
    m = qr_matrix(text)
    n = len(m) + 2 * border
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{n * scale}" height="{n * scale}" '
            f'shape-rendering="crispEdges" role="img" aria-label="QR-Code"><rect width="{n}" height="{n}" fill="#fff"/>'
            f'<path d="{path}" fill="#000"/></svg>')


# ---------------------------------------------------------------- CBOR (nur was WebAuthn braucht)
def cbor_decode(data: bytes, pos=0):
    ib = data[pos]
    major, info = ib >> 5, ib & 0x1F
    pos += 1
    if info < 24:
        val = info
    elif info in (24, 25, 26, 27):
        ln = 1 << (info - 24)
        val = int.from_bytes(data[pos:pos + ln], "big")
        pos += ln
    else:
        raise ValueError("CBOR: unbestimmte Länge nicht unterstützt")
    if major == 0:
        return val, pos
    if major == 1:
        return -1 - val, pos
    if major == 2:
        return bytes(data[pos:pos + val]), pos + val
    if major == 3:
        return data[pos:pos + val].decode("utf-8"), pos + val
    if major == 4:
        out = []
        for _ in range(val):
            v, pos = cbor_decode(data, pos)
            out.append(v)
        return out, pos
    if major == 5:
        out = {}
        for _ in range(val):
            k, pos = cbor_decode(data, pos)
            v, pos = cbor_decode(data, pos)
            out[k] = v
        return out, pos
    if major == 7:
        return {20: False, 21: True, 22: None}.get(info), pos
    raise ValueError("CBOR: Typ nicht unterstützt")


# ---------------------------------------------------------------- WebAuthn
class PasskeyError(ValueError):
    pass


def _client_data(raw: bytes, typ, challenge, origin):
    try:
        cd = json.loads(raw.decode("utf-8"))
    except Exception:
        raise PasskeyError("clientData ungültig")
    if cd.get("type") != typ:
        raise PasskeyError("falscher Typ")
    if not hmac.compare_digest(cd.get("challenge", ""), challenge or "-"):
        raise PasskeyError("Challenge passt nicht")
    if cd.get("origin") != origin:
        raise PasskeyError(f"Herkunft passt nicht ({cd.get('origin')})")
    return cd


def _auth_data(ad: bytes, rp_id, need_uv):
    if len(ad) < 37:
        raise PasskeyError("authData zu kurz")
    if not hmac.compare_digest(ad[:32], hashlib.sha256(rp_id.encode()).digest()):
        raise PasskeyError("RP-ID passt nicht")
    flags = ad[32]
    if not flags & 0x01:
        raise PasskeyError("Benutzer nicht anwesend")
    if need_uv and not flags & 0x04:
        raise PasskeyError("Keine Bestätigung per PIN/Fingerabdruck")
    return flags, struct.unpack(">I", ad[33:37])[0]


def webauthn_register_verify(cred: dict, challenge: str, origin: str, rp_id: str):
    """Gibt (credential_id_b64u, cose_key_bytes, sign_count, uv) zurück."""
    resp = cred.get("response") or {}
    _client_data(b64u_dec(resp.get("clientDataJSON")), "webauthn.create", challenge, origin)
    att, _ = cbor_decode(b64u_dec(resp.get("attestationObject")))
    ad = att.get("authData") if isinstance(att, dict) else None
    if not isinstance(ad, bytes):
        raise PasskeyError("attestationObject ungültig")
    flags, count = _auth_data(ad, rp_id, False)
    if not flags & 0x40:
        raise PasskeyError("Keine Schlüsseldaten")
    p = 37 + 16
    ln = struct.unpack(">H", ad[p:p + 2])[0]
    cred_id = ad[p + 2:p + 2 + ln]
    start = p + 2 + ln
    key, end = cbor_decode(ad, start)
    if not isinstance(key, dict) or key.get(3) not in (-7, -257, -8):
        raise PasskeyError("Schlüsseltyp nicht unterstützt")
    if b64u(cred_id) != cred.get("id"):
        raise PasskeyError("Credential-ID passt nicht")
    return b64u(cred_id), bytes(ad[start:end]), count, bool(flags & 0x04)


def _verify_sig(cose_bytes: bytes, sig: bytes, msg: bytes):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    key, _ = cbor_decode(cose_bytes)
    alg = key.get(3)
    try:
        if alg == -7:
            pub = ec.EllipticCurvePublicNumbers(int.from_bytes(key[-2], "big"), int.from_bytes(key[-3], "big"),
                                                ec.SECP256R1()).public_key()
            pub.verify(sig, msg, ec.ECDSA(hashes.SHA256()))
        elif alg == -257:
            pub = rsa.RSAPublicNumbers(int.from_bytes(key[-2], "big"), int.from_bytes(key[-1], "big")).public_key()
            pub.verify(sig, msg, padding.PKCS1v15(), hashes.SHA256())
        elif alg == -8:
            ed25519.Ed25519PublicKey.from_public_bytes(key[-2]).verify(sig, msg)
        else:
            raise PasskeyError("Schlüsseltyp nicht unterstützt")
    except InvalidSignature:
        raise PasskeyError("Signatur ungültig")


def webauthn_auth_verify(cred: dict, challenge: str, origin: str, rp_id: str, cose_key: bytes, stored_count: int,
                         need_uv=False):
    """Prüft eine Anmeldung. Gibt den neuen Signaturzähler zurück."""
    resp = cred.get("response") or {}
    cdj = b64u_dec(resp.get("clientDataJSON"))
    _client_data(cdj, "webauthn.get", challenge, origin)
    ad = b64u_dec(resp.get("authenticatorData"))
    _, count = _auth_data(ad, rp_id, need_uv)
    _verify_sig(cose_key, b64u_dec(resp.get("signature")), ad + hashlib.sha256(cdj).digest())
    if count and stored_count and count <= stored_count:
        raise PasskeyError("Zähler zu klein (möglicherweise kopierter Schlüssel)")
    return count
