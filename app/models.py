from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RoleEnum(str, Enum):
    ADMIN = "admin"
    FD = "fd"
    NURSE = "nurse"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[RoleEnum] = mapped_column(SqlEnum(RoleEnum), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preferred_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), nullable=True)
    preferred_provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"), nullable=True)
    preferred_location_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_provider_ids: Mapped[str | None] = mapped_column(Text, nullable=True)

    preferred_location: Mapped["Location | None"] = relationship("Location", foreign_keys=[preferred_location_id])
    preferred_provider: Mapped["Provider | None"] = relationship("Provider", foreign_keys=[preferred_provider_id])


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)


class Visit(Base):
    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    mrn: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=False)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), nullable=False)

    arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ready_for_clinical_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    intake_complete_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lab_complete_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkout_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    arrived_delay_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_for_clinical_delay_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    intake_complete_delay_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_in_delay_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_out_delay_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    lab_complete_delay_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkout_delay_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    delay_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    location: Mapped[Location] = relationship("Location")
    provider: Mapped[Provider] = relationship("Provider")
    created_by_user: Mapped[User] = relationship("User")
    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="visit", cascade="all, delete-orphan")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    visit: Mapped[Visit] = relationship("Visit", back_populates="audit_logs")
    changed_by_user: Mapped[User] = relationship("User")
