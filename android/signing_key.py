"""Make the Android apps' signing key from a passphrase (GitHub secret ANDROID_KEY_PASSPHRASE).

Android installs an update over an installed app only if both carry the same signing certificate.
The owner keeps one passphrase as a repository secret; this script turns it into the same RSA key and
the same self-signed certificate on every build, so no key file has to be stored or handed around.

    python3 signing_key.py OUT.p12        (passphrase in $ANDROID_KEY_PASSPHRASE)

Writes a PKCS#12 keystore (alias "box", store/key password in $KEYSTORE_PASSWORD, default "box").
Strength = the passphrase's: use a long random one (e.g. `openssl rand -base64 32`).
Needs the `cryptography` package. Deterministic: PBKDF2 (600k rounds) seeds an HMAC-SHA256 counter
stream, which picks the primes (Miller-Rabin), so the same passphrase always gives the same key.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import sys

SALT = b"neon-cable-42 android cable box signing key v1"
BITS = 3072


class Stream:
    """Deterministic random bytes: HMAC-SHA256(seed, counter)."""

    def __init__(self, seed: bytes):
        self.seed, self.n, self.buf = seed, 0, b""

    def take(self, k: int) -> bytes:
        while len(self.buf) < k:
            self.buf += hmac.new(self.seed, self.n.to_bytes(8, "big"), hashlib.sha256).digest()
            self.n += 1
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def below(self, top: int) -> int:
        k = (top.bit_length() + 7) // 8 + 8
        return int.from_bytes(self.take(k), "big") % top


SMALL = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))]


def probable_prime(n: int, rnd: Stream, rounds: int = 48) -> bool:
    if any(n % p == 0 for p in SMALL):
        return n in SMALL
    d, s = n - 1, 0
    while d % 2 == 0:
        d, s = d // 2, s + 1
    for _ in range(rounds):
        a = 2 + rnd.below(n - 3)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def prime(bits: int, rnd: Stream, e: int) -> int:
    while True:
        c = int.from_bytes(rnd.take(bits // 8), "big") | (3 << (bits - 2)) | 1   # top two bits: n has full size
        if (c - 1) % e and probable_prime(c, rnd):
            return c


def make(passphrase: str):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    seed = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), SALT, 600_000)
    rnd = Stream(seed)
    e = 65537
    p = prime(BITS // 2, rnd, e)
    q = prime(BITS // 2, rnd, e)
    while q == p:
        q = prime(BITS // 2, rnd, e)
    p, q = max(p, q), min(p, q)
    n, phi = p * q, (p - 1) * (q - 1)
    d = pow(e, -1, phi)
    key = rsa.RSAPrivateNumbers(p, q, d, d % (p - 1), d % (q - 1), pow(q, -1, p),
                                rsa.RSAPublicNumbers(e, n)).private_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Cable Box")])
    # every field fixed, and PKCS#1 v1.5 signatures are deterministic: the same certificate every time
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(int.from_bytes(hashlib.sha256(seed + b"serial").digest()[:16], "big") >> 1)
            .not_valid_before(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
            .not_valid_after(dt.datetime(2076, 1, 1, tzinfo=dt.timezone.utc))
            .sign(key, hashes.SHA256()))
    return key, cert


def main() -> int:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, pkcs12

    phrase = os.environ.get("ANDROID_KEY_PASSPHRASE", "")
    if len(phrase) < 16:
        print("ANDROID_KEY_PASSPHRASE is missing or shorter than 16 characters", file=sys.stderr)
        return 1
    key, cert = make(phrase)
    pw = os.environ.get("KEYSTORE_PASSWORD", "box").encode()
    with open(sys.argv[1], "wb") as f:
        f.write(pkcs12.serialize_key_and_certificates(b"box", key, cert, None, BestAvailableEncryption(pw)))
    print("signing certificate SHA-256:", cert.fingerprint(hashes.SHA256()).hex(":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
