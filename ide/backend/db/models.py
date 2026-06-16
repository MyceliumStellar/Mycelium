from sqlalchemy import Column, String, BigInteger, ForeignKey, DateTime, LargeBinary
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from ide.backend.db.connection import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    github_user_id = Column(BigInteger, unique=True, nullable=False)
    github_username = Column(String(255), nullable=False)
    avatar_url = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class UserCredential(Base):
    __tablename__ = "user_credentials"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    encrypted_github_token = Column(String, nullable=False)
    token_salt = Column(LargeBinary, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ActiveWorkspace(Base):
    __tablename__ = "active_workspaces"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    current_repository_url = Column(String, nullable=False)
    current_active_branch = Column(String(100), default="main")
    last_synced_commit = Column(String(40))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
