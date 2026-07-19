import os
import json
import base64
import time
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".taxos_did.key")

class SSIVault:
    def __init__(self):
        self.private_key = self._load_or_generate_key()
        self.public_key = self.private_key.public_key()
        self.did = self._generate_did()

    def _load_or_generate_key(self) -> ec.EllipticCurvePrivateKey:
        if os.path.exists(KEY_FILE):
            try:
                with open(KEY_FILE, "rb") as f:
                    return serialization.load_pem_private_key(f.read(), password=None)
            except Exception:
                pass
        
        # Generate new secp256r1 ECC key
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        with open(KEY_FILE, "wb") as f:
            f.write(pem)
        return key

    def _generate_did(self) -> str:
        """Generates a did:key based on the secp256r1 public key encoded in DER format."""
        pub_der = self.public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        # Base58/Base64 encoding equivalent for clean URI did representation
        encoded = base64.b64encode(pub_der).decode("utf-8").replace("=", "").replace("+", "-").replace("/", "_")
        return f"did:key:z{encoded}"

    def issue_verifiable_credential(self, freelancer_name: str, gst_status: str, total_turnover: float, last_hash: str) -> dict:
        """Issues a JSON-LD compliant Verifiable Credential signed with the PC Hub DID."""
        credential = {
            "@context": [
                "https://www.w3.org/2018/credentials/v1",
                "https://w3id.org/security/suites/ed25519-2020/v1"
            ],
            "id": f"urn:uuid:{base64.b64encode(os.urandom(9)).decode('utf-8').replace('/', 'a')}",
            "type": ["VerifiableCredential", "TaxOSGSTCompliance"],
            "issuer": self.did,
            "issuanceDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "credentialSubject": {
                "id": "did:key:freelancer-devashish-sharma",
                "freelancerName": freelancer_name,
                "gstStatus": gst_status,
                "verifiedTurnoverTier": "< 20 Lakh" if total_turnover < 2000000 else ">= 20 Lakh",
                "ledgerAttestationHash": last_hash
            }
        }

        # Normalize JSON to prepare for signing (canonicalization matching JS stringify)
        normalized = json.dumps(credential, separators=(',', ':'), sort_keys=True)
        
        # Sign the payload using secp256r1 ECDSA with SHA-256
        signature = self.private_key.sign(
            normalized.encode("utf-8"),
            ec.ECDSA(hashes.SHA256())
        )
        
        # Convert DER to raw P1363 64-byte (R || S) format for Web Crypto API compatibility
        r, s = decode_dss_signature(signature)
        raw_signature = r.to_bytes(32, byteorder="big") + s.to_bytes(32, byteorder="big")
        
        # Format the proof block
        credential["proof"] = {
            "type": "JsonWebSignature2020",
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "verificationMethod": f"{self.did}#key-1",
            "proofPurpose": "assertionMethod",
            "jws": base64.b64encode(raw_signature).decode("utf-8")
        }
        
        return credential

    def verify_credential(self, credential: dict) -> bool:
        """Verifies the JSON-LD signature of a credential."""
        if "proof" not in credential:
            return False
        
        proof = credential["proof"]
        jws = proof["jws"]
        
        # Recreate unsigned credential copy
        unsigned = {k: v for k, v in credential.items() if k != "proof"}
        normalized = json.dumps(unsigned, separators=(',', ':'), sort_keys=True)
        
        try:
            raw_signature = base64.b64decode(jws.encode("utf-8"))
            if len(raw_signature) == 64:
                # Convert raw IEEE P1363 (R || S) to ASN.1 DER format for Python verification
                r = int.from_bytes(raw_signature[:32], byteorder="big")
                s = int.from_bytes(raw_signature[32:], byteorder="big")
                signature = encode_dss_signature(r, s)
            else:
                signature = raw_signature
            
            # Verify signature using the public key
            self.public_key.verify(
                signature,
                normalized.encode("utf-8"),
                ec.ECDSA(hashes.SHA256())
            )
            return True
        except Exception:
            return False
