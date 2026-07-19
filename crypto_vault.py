import os
import base64
import random
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".taxos_vault.key")
PAIRING_PIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".taxos_pairing.pin")

class CryptoVault:
    def __init__(self):
        self.master_key = self._load_or_create_key()
        self.aesgcm = AESGCM(self.master_key)

    def _load_or_create_key(self) -> bytes:
        """Loads key from local file or generates a secure new one if it doesn't exist."""
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, "rb") as f:
                return f.read()
        else:
            # Generate a random master key
            key = AESGCM.generate_key(bit_length=256)
            with open(KEY_FILE, "wb") as f:
                f.write(key)
            # Make key file read-only for owner (POSIX-like systems, ignore error on Windows)
            try:
                os.chmod(KEY_FILE, 0o600)
            except Exception:
                pass
            return key

    def encrypt(self, plain_text: str) -> str:
        """Encrypts plain text using AES-256-GCM and returns a base64 encoded string."""
        if not plain_text:
            return ""
        nonce = os.urandom(12)
        encrypted_bytes = self.aesgcm.encrypt(nonce, plain_text.encode("utf-8"), None)
        # Store nonce + cipherText together
        combined = nonce + encrypted_bytes
        return base64.b64encode(combined).decode("utf-8")

    def decrypt(self, cipher_text: str) -> str:
        """Decrypts a base64 encoded AES-256-GCM cipher string and returns plain text."""
        if not cipher_text:
            return ""
        try:
            combined = base64.b64decode(cipher_text.encode("utf-8"))
            if len(combined) < 12:
                return "[Decryption Error: Short Ciphertext]"
            nonce = combined[:12]
            encrypted_bytes = combined[12:]
            decrypted_bytes = self.aesgcm.decrypt(nonce, encrypted_bytes, None)
            return decrypted_bytes.decode("utf-8")
        except Exception as e:
            return f"[Decryption Error: {str(e)}]"

    def generate_pairing_pin(self) -> str:
        """Generates a secure 6-digit pairing PIN, saves it, and returns it."""
        pin = f"{random.randint(100000, 999999)}"
        with open(PAIRING_PIN_FILE, "w") as f:
            f.write(pin)
        return pin

    def get_current_pin(self) -> str:
        """Retrieves the currently active pairing PIN."""
        if os.path.exists(PAIRING_PIN_FILE):
            with open(PAIRING_PIN_FILE, "r") as f:
                return f.read().strip()
        return self.generate_pairing_pin()

    def verify_pairing_pin(self, user_pin: str) -> bool:
        """Compares user PIN with current active PIN. Deletes PIN file on successful verification."""
        active_pin = self.get_current_pin()
        if user_pin == active_pin:
            # Delete PIN file to prevent reuse
            try:
                os.remove(PAIRING_PIN_FILE)
            except Exception:
                pass
            return True
        return False

    def generate_device_token(self, device_name: str) -> str:
        """Generates a secure, random device authorization token."""
        return secrets.token_hex(32)
