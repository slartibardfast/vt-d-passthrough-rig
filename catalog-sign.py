#!/usr/bin/env python3
"""Rebuild and re-sign the AMD display driver catalog so it covers the patched INF.

Why this exists
---------------
CA178340.inf declares `CatalogFile=CA178340.CAT`, but that catalog was never put into
gpudrv.img, so the driver package cannot install on XP x64 no matter what the INF says.
Editing the INF to add PCI\\VEN_1002&DEV_67B0 changes the INF's hash, so the catalog has
to be rebuilt even once it is present. Windows XP has no boot-time opt-out for signing
enforcement (that is a Vista-and-later F8 entry), so the catalog must be signed by
something the guest trusts. That something is a certificate we mint here and install
into the guest's Trusted Root store.

What it does
------------
The catalog is a CMS SignedData whose eContentType is 1.3.6.1.4.1.311.10.1 (CAT_LIST)
and whose eContent is a CatalogInfoList - one CatalogInfo per file, each carrying a File
name, OSAtt, a language, and the file's hash under OID 1.3.6.1.4.1.311.2.1.25.

Only the INF changed, and SHA-1 is a fixed width, so the fix is to substitute the new
INF's hash into the existing CatalogInfoList and leave every other byte, and therefore
every enclosing ASN.1 length, exactly as AMD signed it. That is deliberately the
smallest possible edit: a regenerated catalog would risk reordering members and changing
the OS attribute handling.

The SignedData wrapper around that content is rebuilt because the signature has to come
from our key. The DER is written by hand below rather than by a library so that the
bytes are inspectable and the signedAttrs SET tag can be handled correctly.

Usage
-----
    catalog-sign.py --package <dir with the ORIGINAL cat+inf> \\
                   --inf <patched inf> --out <dir to write CA178340.cat and the .cer>

The original .cat and .inf must come from the corpus, not from the image: the image has
no catalog to start from.
"""
import argparse
import datetime
import hashlib
import os
import re
import sys

from asn1crypto import cms, core

# OIDs used, taken from the structure decoded out of AMD's own catalog.
OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_CATALOG_LIST = "1.3.6.1.4.1.311.10.1"
OID_SHA1 = "1.3.14.3.2.26"
OID_RSA = "1.2.840.113549.1.1.1"
OID_ATTR_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_ATTR_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_EKU_CODE_SIGNING = "1.3.6.1.5.5.7.3.3"

PEER_MEMBER_NAME = b"c\x00a\x001\x007\x008\x003\x004\x000\x00.\x00i\x00n\x00f\x00"


# --------------------------------------------------------------------------- DER

def der_len(n):
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def tlv(tag, content):
    return bytes([tag]) + der_len(len(content)) + content


def d_oid(dotted):
    return tlv(0x06, core.ObjectIdentifier(dotted).dump()[2:] if False else _oid_body(dotted))


def _oid_body(dotted):
    parts = [int(p) for p in dotted.split(".")]
    out = bytes([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        chunk = bytearray([p & 0x7F])
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(chunk))
    return out


def d_int(n):
    if n == 0:
        return tlv(0x02, b"\x00")
    body = n.to_bytes((n.bit_length() + 8) // 8, "big")
    return tlv(0x02, body)


def d_seq(*parts):
    return tlv(0x30, b"".join(parts))


def d_set(*parts):
    return tlv(0x31, b"".join(parts))


def d_ctx(n, content):
    """Context tag. [0] constructed, which is what all the implicit tags here are."""
    return tlv(0xA0 + n, content)


def d_octets(b):
    return tlv(0x04, b)


def content_of(encoded):
    """Strip the tag and length from a complete TLV.

    The implicit [0] that wraps signedAttrs carries the same content as the SET it is
    derived from, so the content has to be recovered from the SET. Assuming a one-byte
    length here silently corrupts any SET longer than 127 bytes, which every signedAttrs
    is, so the header width is read rather than assumed.
    """
    if encoded[1] < 0x80:
        return encoded[2:]
    n = encoded[1] & 0x7F
    return encoded[2 + n:]


def d_alg(oid):
    """AlgorithmIdentifier with the NULL parameters AMD's catalog used."""
    return d_seq(d_oid(oid), tlv(0x05, b""))


def d_bitstring(b):
    return tlv(0x03, b"\x00" + b)


# ----------------------------------------------------------------- certificate

def make_cert(common_name):
    """Mint the self-signed code-signing certificate that will be trusted in the guest."""
    import subprocess
    import tempfile
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    # Minted by the openssl CLI, not by the cryptography package: the package now refuses
    # SHA-1 certificate signatures outright, and SHA-1 is required here - Windows XP x64's
    # CryptoAPI cannot verify SHA-2 certificate signatures (SHA-2 Authenticode arrived with
    # Vista, which is why AMD's own 2014 catalog and its whole chain are SHA-1). A SHA-256
    # -signed leaf fails chain building on this OS with no error in setupapi.log, surfacing
    # only as #I087 "Driver node not trusted". The inner SignedData digest is SHA-1 for the
    # same reason. The explicit 2020-2050 validity brackets the guest's clock with room to
    # spare, because a certificate "not yet valid" is as unusable as an unverifiable one.
    with tempfile.TemporaryDirectory() as td:
        key_pem = os.path.join(td, "key.pem")
        cert_pem = os.path.join(td, "cert.pem")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha1",
             "-keyout", key_pem, "-out", cert_pem,
             "-subj", f"/CN={common_name}",
             "-not_before", "20200101000000Z", "-not_after", "20500101000000Z",
             "-addext", "basicConstraints=critical,CA:FALSE",
             "-addext", "keyUsage=critical,digitalSignature,keyCertSign,cRLSign",
             "-addext", "extendedKeyUsage=codeSigning"],
            check=True, capture_output=True)
        with open(key_pem, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        with open(cert_pem, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
    return key, cert.public_bytes(serialization.Encoding.DER)


# --------------------------------------------------------------------- rebuild

def find_inf_member(content, original_inf_bytes):
    """Locate the INF's hash inside the CatalogInfoList and return (offset, old, new).

    The hash is a 40-character uppercase hex SHA-1 encoded as UTF-16LE, not ASCII - which
    is the detail that made the first attempt at this search silently find nothing. Rather
    than locate it by parsing the surrounding structure, the edit is anchored on the value
    itself: SHA-1 of the unpatched INF, which is what AMD signed and what must therefore
    be sitting in the catalog. If that exact string is not present exactly once, the
    premise is wrong and this stops rather than guessing at an offset.
    """
    old_hex = hashlib.sha1(original_inf_bytes).hexdigest().upper()
    needle = old_hex.encode("utf-16-le")
    count = content.count(needle)
    if count != 1:
        raise SystemExit(
            f"expected exactly one occurrence of the unpatched INF's hash "
            f"({old_hex}) in the CatalogInfoList, found {count}. Refusing to guess.")
    return content.index(needle), old_hex


def signed_bytes(econtent):
    """The bytes AMD's messageDigest actually covers.

    Measured, not assumed: AMD's signature carries the digest 2EBAB748...FEA, which is
    SHA-1 of the CatalogInfoList with BOTH the [0] wrapper and the inner SEQUENCE header
    removed - not of the eContent as CMS would hand it over, and not of the file. Guessing
    any of the other three candidates would have produced a signature that looks fine and
    verifies against nothing.
    """
    inner = content_of(econtent)          # drop [0] { ... }
    return content_of(inner)              # drop SEQUENCE { ... }


def swap_message_digest(signed_attrs_set, digest):
    """Replace the messageDigest value inside AMD's signedAttrs SET, in place.

    Reusing AMD's own attribute bytes - including the SPC_SP_OPUS_INFO and
    SPC_STATEMENT_TYPE entries that a hand-rolled SET would have omitted, and their order -
    keeps this re-signing as close to what signtool produced as possible. The digest is a
    fixed 20 bytes so the replacement cannot disturb any length.

    The offset is found by walking the attribute's structure rather than by a hand-written
    hex constant: two earlier attempts at the constant were wrong (the SET around the
    digest is 31 16, not 31 09), and a wrong constant fails by not finding anything at all.
    """
    oid_marker = bytes.fromhex("06092a864886f70d010904")   # OID messageDigest, then OCTET tag
    i = signed_attrs_set.find(oid_marker)
    if i < 0 or signed_attrs_set.count(oid_marker) != 1:
        raise SystemExit("expected exactly one messageDigest attribute in AMD's signedAttrs")
    j = i + len(oid_marker)
    if signed_attrs_set[j] != 0x31 or signed_attrs_set[j + 1] >= 0x80:
        raise SystemExit("messageDigest value is not wrapped in a short-form SET as expected")
    k = j + 2
    if signed_attrs_set[k] != 0x04:
        raise SystemExit("messageDigest SET does not hold a primitive OCTET STRING")
    octet_len = signed_attrs_set[k + 1]
    at = k + 2
    if len(digest) != octet_len:
        raise SystemExit(f"digest is {len(digest)} bytes but the attribute holds {octet_len}")
    old = signed_attrs_set[at:at + octet_len]
    if old == digest:
        raise SystemExit("messageDigest already holds the new value")
    return signed_attrs_set[:at] + digest + signed_attrs_set[at + octet_len:], old


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True,
                    help="directory holding the ORIGINAL CA178340.cat (from the corpus)")
    ap.add_argument("--inf", required=True, help="the patched INF to be covered")
    ap.add_argument("--out", required=True, help="directory to write CA178340.cat and the .cer")
    ap.add_argument("--subject", default="R9 290X XP x64 driver package signer")
    args = ap.parse_args()

    cat_path = os.path.join(args.package, "CA178340.cat")
    original = open(cat_path, "rb").read()

    # 1. Read AMD's catalog and keep its CatalogInfoList byte-for-byte.
    sd = cms.ContentInfo.load(original)["content"]
    encap = sd["encap_content_info"]
    if encap["content_type"].dotted != OID_CATALOG_LIST:
        raise SystemExit(f"unexpected eContentType {encap['content_type'].dotted}")
    content = encap["content"].dump()

    # 2. Substitute only the INF's hash. Same width, so no length anywhere changes.
    original_inf = open(os.path.join(args.package, "CA178340.inf"), "rb").read()
    off, old = find_inf_member(content, original_inf)
    new = hashlib.sha1(open(args.inf, "rb").read()).hexdigest().upper()
    patched_content = content[:off] + new.encode("utf-16-le") + content[off + 80:]
    print(f"  catalog member hash for the INF: {old} -> {new}")
    if len(patched_content) != len(content):
        raise SystemExit("substitution changed the content length, which must not happen")

    # 3. Rebuild the SignedData around it, reusing AMD's own signedAttrs so the
    #    statement-type and opus-info attributes signtool emitted are preserved.
    key, cert_der = make_cert(args.subject)
    from asn1crypto import x509 as ax509
    our = ax509.Certificate.load(cert_der)
    issuer_and_serial = cms.IssuerAndSerialNumber({
        "issuer": our.issuer,
        "serial_number": our.serial_number,
    })

    amd_signed_attrs = sd["signer_infos"][0]["signed_attrs"].dump()
    old_digest = [a for a in sd["signer_infos"][0]["signed_attrs"]
                  if a["type"].dotted == OID_ATTR_MESSAGE_DIGEST][0]["values"][0].native
    new_digest = hashlib.sha1(signed_bytes(patched_content)).digest()
    if new_digest == old_digest:
        raise SystemExit("the patched INF hashes the same as the original; nothing to sign")
    signed_attrs, old_digest = swap_message_digest(amd_signed_attrs, new_digest)
    print(f"  signedAttrs reused from AMD's signature, messageDigest "
          f"{old_digest.hex().upper()} -> {new_digest.hex().upper()}")
    print(f"  digest is over the bare CatalogInfoList ({len(signed_bytes(patched_content))} bytes), "
          f"which is what AMD's digest covers")

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    signature = key.sign(hashlib.sha1(signed_attrs).digest(), padding.PKCS1v15(), hashes.SHA1())

    signer_info = d_seq(
        d_int(1),
        issuer_and_serial.dump(),
        d_alg(OID_SHA1),
        d_ctx(0, content_of(signed_attrs)),    # [0] IMPLICIT: SET content, tag dropped
        d_alg(OID_RSA),
        d_octets(signature),
    )

    # encap_content_info's eContent already carries its own [0] wrapper, so it is emitted
    # as-is rather than wrapped again. Likewise [0] IMPLICIT CertificateSet holds the
    # certificates' own encodings concatenated, not an OCTET STRING around them.
    signed_data = d_seq(
        d_int(1),
        d_set(d_alg(OID_SHA1)),
        d_seq(d_oid(OID_CATALOG_LIST), patched_content),
        d_ctx(0, cert_der),
        d_set(signer_info),
    )
    content_info = d_seq(d_oid(OID_SIGNED_DATA), d_ctx(0, signed_data))

    os.makedirs(args.out, exist_ok=True)
    out_cat = os.path.join(args.out, "CA178340.cat")
    open(out_cat, "wb").write(content_info)
    open(os.path.join(args.out, "CA178340-290x.cer"), "wb").write(cert_der)

    # 4. Prove the output before anyone boots with it.
    back = cms.ContentInfo.load(open(out_cat, "rb").read())["content"]
    assert back["encap_content_info"]["content"].dump() == patched_content, "eContent lost"
    sig_ok = key.public_key().verify(
        signature,
        hashlib.sha1(signed_attrs).digest(),
        padding.PKCS1v15(),
        hashes.SHA1())
    print(f"  wrote {out_cat} ({len(content_info)} bytes)")
    print(f"  eContent round-trips byte-for-byte: yes ({len(patched_content)} bytes)")
    print(f"  signature verifies under the new public key: {'yes' if sig_ok is None else 'NO'}")
    print(f"  certificate: {our.subject.human_friendly}")


if __name__ == "__main__":
    main()