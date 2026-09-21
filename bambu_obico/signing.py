from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class SigningError(RuntimeError):
    pass


def _first_certificate(pem: bytes) -> x509.Certificate:
    begin = b"-----BEGIN CERTIFICATE-----"
    end = b"-----END CERTIFICATE-----"
    start = pem.find(begin)
    if start < 0:
        raise SigningError("slicer_cert.pem contains no certificate")
    finish = pem.find(end, start)
    if finish < 0:
        raise SigningError("slicer_cert.pem contains an incomplete certificate")
    finish += len(end)
    try:
        return x509.load_pem_x509_certificate(pem[start:finish] + b"\n")
    except ValueError as exc:
        raise SigningError("Unable to parse slicer certificate") from exc


class BambuSigner:
    """Load locally-provisioned Bambu signing credentials and sign print messages."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        key_path = self.directory / "slicer_key.pem"
        cert_path = self.directory / "slicer_cert.pem"
        crl_path = self.directory / "slicer_crl.pem"

        missing = [p.name for p in (key_path, cert_path, crl_path) if not p.is_file()]
        if missing:
            raise SigningError(
                "Missing signing credential files in "
                f"{self.directory}: {', '.join(missing)}"
            )

        try:
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise SigningError("Unable to load slicer_key.pem") from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise SigningError("slicer_key.pem is not an RSA private key")

        cert_pem = cert_path.read_bytes()
        cert = _first_certificate(cert_pem)
        cert_pub = cert.public_key()
        if not isinstance(cert_pub, rsa.RSAPublicKey):
            raise SigningError("slicer certificate does not contain an RSA public key")
        if key.public_key().public_numbers() != cert_pub.public_numbers():
            raise SigningError("slicer_key.pem does not match the leaf slicer certificate")

        crl_pem = crl_path.read_bytes()
        try:
            x509.load_pem_x509_crl(crl_pem)
        except ValueError as exc:
            raise SigningError("Unable to parse slicer_crl.pem") from exc

        serial = format(cert.serial_number, "x").lower()
        if len(serial) % 2:
            serial = "0" + serial
        issuer = cert.issuer.rfc4514_string()
        if not issuer:
            raise SigningError("Slicer certificate has no issuer")

        self._key = key
        self._cert_pem = cert_pem.decode("ascii")
        self._crl_pem = crl_pem.decode("ascii")
        self.cert_id = serial + issuer

    @property
    def app_cert_pem(self) -> str:
        return self._cert_pem

    @property
    def crl_pem(self) -> str:
        return self._crl_pem

    @staticmethod
    def public_key_from_certificate(data: bytes) -> rsa.RSAPublicKey:
        """Load an RSA public key from a PEM/DER X.509 certificate."""
        try:
            if b"-----BEGIN CERTIFICATE-----" in data:
                cert = _first_certificate(data)
            else:
                cert = x509.load_der_x509_certificate(data)
        except ValueError as exc:
            raise SigningError("Unable to parse printer device certificate") from exc
        pub = cert.public_key()
        if not isinstance(pub, rsa.RSAPublicKey):
            raise SigningError("Printer device certificate does not contain an RSA key")
        return pub

    @staticmethod
    def encrypt_field(public_key: rsa.RSAPublicKey, plaintext: str) -> str:
        """RSA-PKCS#1 v1.5 blockwise encryption used by secured MQTT fields."""
        data = plaintext.encode("utf-8")
        key_bytes = (public_key.key_size + 7) // 8
        max_chunk = key_bytes - 11
        if max_chunk <= 0:
            raise SigningError("Printer RSA key is too small")
        encrypted = bytearray()
        if not data:
            encrypted.extend(public_key.encrypt(b"", padding.PKCS1v15()))
        else:
            for offset in range(0, len(data), max_chunk):
                encrypted.extend(
                    public_key.encrypt(
                        data[offset : offset + max_chunk],
                        padding.PKCS1v15(),
                    )
                )
        return base64.b64encode(bytes(encrypted)).decode("ascii")

    def sign_print(self, print_section: dict[str, Any]) -> str:
        """Return the exact signed MQTT envelope for a print command."""
        print_json = json.dumps(
            print_section,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        to_sign = '{"print":' + print_json + "}"
        encoded = to_sign.encode("utf-8")
        signature = self._key.sign(
            encoded,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        envelope = {
            "header": {
                "cert_id": self.cert_id,
                "payload_len": len(encoded),
                "sign_alg": "RSA_SHA256",
                "sign_string": base64.b64encode(signature).decode("ascii"),
                "sign_ver": "v1.0",
            },
            "print": json.loads(print_json),
        }
        return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)

    def app_cert_install(self, sequence_id: str) -> str:
        payload = {
            "security": {
                "sequence_id": sequence_id,
                "command": "app_cert_install",
                "app_cert": self._cert_pem,
                "crl": self._crl_pem,
            }
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
