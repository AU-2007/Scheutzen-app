"""Web Push (RFC 8030/8291/8292) ohne Zusatzpakete – nur cryptography.
Kostenlos: die Push-Dienste von Google, Apple, Mozilla und Samsung verlangen nichts."""
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sicherheit import b64u, b64u_dec


def _hmac(key, data):
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _raw_pub(pub):
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


class Vapid:
    """Server-Schlüssel. Wird einmal erzeugt und im Datenordner gespeichert."""

    def __init__(self, path):
        self.path = path
        if os.path.exists(path):
            with open(path, "rb") as f:
                self.key = serialization.load_pem_private_key(f.read(), None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption())
            fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
            try:
                os.link(path + ".tmp", path)       # mehrere Worker: wer zuerst kommt, gewinnt
            except FileExistsError:
                pass
            os.unlink(path + ".tmp")
            with open(path, "rb") as f:
                self.key = serialization.load_pem_private_key(f.read(), None)
        self.public = b64u(_raw_pub(self.key.public_key()))

    def auth_header(self, endpoint, subject):
        u = urlparse(endpoint)
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        body = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                                "sub": subject}, separators=(",", ":")).encode())
        r, s = decode_dss_signature(self.key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
        sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={head}.{body}.{sig}, k={self.public}"


def encrypt(payload: bytes, p256dh: str, auth: str, _salt=None, _as_key=None) -> bytes:
    """aes128gcm-Verschlüsselung nach RFC 8291."""
    ua_pub_raw = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_raw)
    as_key = _as_key or ec.generate_private_key(ec.SECP256R1())
    as_pub_raw = _raw_pub(as_key.public_key())
    shared = as_key.exchange(ec.ECDH(), ua_pub)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_pub_raw + as_pub_raw + b"\x01")
    salt = _salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">IB", 4096, len(as_pub_raw)) + as_pub_raw + ct


def send(vapid: Vapid, sub: dict, data: dict, subject: str, ttl=24 * 3600, timeout=10):
    """Schickt eine Nachricht. Gibt den HTTP-Status zurück (201 = ok, 404/410 = Abo ungültig)."""
    endpoint = sub["endpoint"]
    if urlparse(endpoint).scheme != "https":
        return 400
    body = encrypt(json.dumps(data).encode(), sub["p256dh"], sub["auth"])
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "TTL": str(ttl), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "Urgency": "normal", "Authorization": vapid.auth_header(endpoint, subject)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0
