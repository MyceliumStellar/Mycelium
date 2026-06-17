from datetime import datetime, timedelta
from jose import jwt, JWTError
from cryptography.fernet import Fernet
import base64
from ide.backend.config import JWT_SECRET_KEY, ALGORITHM

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=180) # 3 hours
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return {}

def get_fernet_key() -> bytes:
    # Derive a 32-byte key from the JWT_SECRET_KEY
    key = JWT_SECRET_KEY.encode().ljust(32, b'\0')[:32]
    return base64.urlsafe_b64encode(key)

def encrypt_token(token: str) -> str:
    f = Fernet(get_fernet_key())
    return f.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    f = Fernet(get_fernet_key())
    return f.decrypt(encrypted_token.encode()).decode()
