"""Modular monolith persistence model. Separate ownership, shared canonical IDs.

The same physical schema runs on SQL Server 2019 or SQLite for isolated tests.
Temporal uniqueness and cross-table temporal containment require transaction-level service validation.
"""
from datetime import date, datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import (CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
                        JSON, String, Text, UniqueConstraint, Uuid, Boolean, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def guid(): return uuid4()

class Base(DeclarativeBase): pass
class OrgNode(Base):
    __tablename__ = 'org_node'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    __table_args__ = (CheckConstraint("kind IN ('LOCATION','LOCATION_SECTION')", name='ck_org_node_kind'),)

class Location(Base):
    __tablename__ = 'location'
    id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('org_node.id', ondelete='RESTRICT'), primary_key=True)
    code: Mapped[str] = mapped_column(String(45), unique=True, nullable=False)
    location_type: Mapped[str] = mapped_column(String(40), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    visibility: Mapped[str] = mapped_column(String(12), default='STANDARD', nullable=False)
    selectable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_location_dates'),
                      CheckConstraint("visibility IN ('STANDARD','RESTRICTED')", name='ck_location_visibility'))

class LocationName(Base):
    __tablename__ = 'location_name'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    location_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('location.id', ondelete='RESTRICT'), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_location_name_dates'),
                      Index('ix_location_name_date', 'location_id', 'valid_from', 'valid_to'))

class LocationParent(Base):
    __tablename__ = 'location_parent'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    child_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('location.id', ondelete='RESTRICT'), nullable=False)
    parent_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('location.id', ondelete='RESTRICT'), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint('child_id <> parent_id', name='ck_parent_self'),
                      CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_parent_dates'),
                      Index('ix_parent_child_dates', 'child_id', 'valid_from', 'valid_to'))

class SectionDefinition(Base):
    __tablename__ = 'section_definition'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    code: Mapped[str] = mapped_column(String(45), unique=True, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_secdef_dates'),)

class SectionName(Base):
    __tablename__ = 'section_name'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    definition_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('section_definition.id', ondelete='RESTRICT'), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_secname_dates'),)

class LocationSection(Base):
    __tablename__ = 'location_section'
    id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('org_node.id', ondelete='RESTRICT'), primary_key=True)
    location_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('location.id', ondelete='RESTRICT'), nullable=False)
    definition_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('section_definition.id', ondelete='RESTRICT'), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    visibility: Mapped[str] = mapped_column(String(12), default='STANDARD', nullable=False)
    __table_args__ = (UniqueConstraint('id','location_id', name='uq_location_section_pair'),
                      CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_locsec_dates'),
                      CheckConstraint("visibility IN ('STANDARD','RESTRICTED')", name='ck_locsec_visibility'),
                      Index('ix_locsec_lookup','location_id','valid_from','valid_to'))

class LocationAlias(Base):
    __tablename__ = 'location_alias'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    location_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('location.id', ondelete='RESTRICT'), nullable=False)
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(100), nullable=False)
    __table_args__ = (UniqueConstraint('location_id','normalized_alias',name='uq_location_alias'),
                      Index('ix_location_alias_search','normalized_alias'))

class SectionAlias(Base):
    __tablename__ = 'section_alias'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    definition_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('section_definition.id', ondelete='RESTRICT'), nullable=False)
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(100), nullable=False)
    __table_args__ = (UniqueConstraint('definition_id','normalized_alias',name='uq_section_alias'),)

class ReportingEdge(Base):
    __tablename__ = 'reporting_edge'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    source_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('org_node.id', ondelete='RESTRICT'), nullable=False)
    authority_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('org_node.id', ondelete='RESTRICT'), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    scope: Mapped[str] = mapped_column(String(250), default='', nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(160), nullable=False)
    __table_args__ = (CheckConstraint('source_id <> authority_id', name='ck_reporting_self'),
                      CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_reporting_dates'),
                      Index('ix_reporting_source','source_id','valid_from','valid_to'))

class Building(Base):
    __tablename__ = 'building'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    code: Mapped[str] = mapped_column(String(45), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    address: Mapped[str] = mapped_column(String(250), nullable=False)
    parish: Mapped[str] = mapped_column(String(100), nullable=False)
    latitude: Mapped[float | None] = mapped_column(nullable=True)
    longitude: Mapped[float | None] = mapped_column(nullable=True)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_building_dates'),
                      CheckConstraint('((latitude IS NULL AND longitude IS NULL) OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))', name='ck_gps'))

class LocationBuilding(Base):
    __tablename__ = 'location_building'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    location_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('location.id', ondelete='RESTRICT'), nullable=False)
    building_id: Mapped[UUID] = mapped_column(Uuid(), ForeignKey('building.id', ondelete='RESTRICT'), nullable=False)
    occupancy_type: Mapped[str] = mapped_column(String(20), default='PERMANENT', nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint('id','location_id',name='uq_location_building_pair'),
                      CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_locbuilding_dates'))

class SectionBuilding(Base):
    __tablename__ = 'section_building'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    section_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    location_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    location_building_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    assignment_type: Mapped[str] = mapped_column(String(20), default='SECONDARY',nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (ForeignKeyConstraint(['section_id','location_id'],['location_section.id','location_section.location_id'],ondelete='RESTRICT'),
                      ForeignKeyConstraint(['location_building_id','location_id'],['location_building.id','location_building.location_id'],ondelete='RESTRICT'),
                      CheckConstraint('valid_to IS NULL OR valid_from < valid_to', name='ck_secbuilding_dates'))

class Contact(Base):
    __tablename__ = 'contact'
    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=guid)
    kind: Mapped[str] = mapped_column(String(24),nullable=False)
    value: Mapped[str] = mapped_column(String(250),nullable=False)
    telephone_system: Mapped[str | None] = mapped_column(String(100))
    visibility: Mapped[str] = mapped_column(String(20),default='INTERNAL',nullable=False)
    __table_args__ = (CheckConstraint("kind IN ('PHONE','MOBILE','EXTENSION','EMAIL','FAX')",name='ck_contact_kind'),
                      CheckConstraint("visibility IN ('PUBLIC','INTERNAL','PERSONAL')", name='ck_contact_visibility'))

class ContactAssignment(Base):
    __tablename__ = 'contact_assignment'
    id: Mapped[UUID] = mapped_column(Uuid(),primary_key=True,default=guid)
    contact_id: Mapped[UUID] = mapped_column(Uuid(),ForeignKey('contact.id',ondelete='RESTRICT'),nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(32),nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(),nullable=False)
    purpose: Mapped[str] = mapped_column(String(64),nullable=False)
    valid_from: Mapped[date] = mapped_column(Date,nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint("entity_kind IN ('LOCATION','LOCATION_SECTION','BUILDING','PERSONNEL','VISITOR','OFFICE')",name='ck_contact_owner'),)

class Personnel(Base):
    __tablename__ = 'personnel'
    id: Mapped[UUID] = mapped_column(Uuid(),primary_key=True,default=guid)
    computer_number: Mapped[str] = mapped_column(String(45),unique=True,nullable=False)
    display_name: Mapped[str] = mapped_column(String(180),nullable=False)
    employment_status: Mapped[str] = mapped_column(String(40),default='SERVING',nullable=False)

class Posting(Base):
    __tablename__ = 'posting'
    id: Mapped[UUID] = mapped_column(Uuid(),primary_key=True,default=guid)
    personnel_id: Mapped[UUID] = mapped_column(Uuid(),ForeignKey('personnel.id',ondelete='RESTRICT'),nullable=False)
    location_id: Mapped[UUID] = mapped_column(Uuid(),nullable=False)
    section_id: Mapped[UUID] = mapped_column(Uuid(),nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean,default=True,nullable=False)
    valid_from: Mapped[date] = mapped_column(Date,nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(160),nullable=False)
    __table_args__ = (ForeignKeyConstraint(['section_id','location_id'],['location_section.id','location_section.location_id'],ondelete='RESTRICT'),
                      CheckConstraint('valid_to IS NULL OR valid_from < valid_to',name='ck_posting_dates'),
                      Index('ix_posting_person_date','personnel_id','valid_from','valid_to'))

class LeaveRequest(Base):
    __tablename__='leave_request'
    id: Mapped[UUID] = mapped_column(Uuid(),primary_key=True,default=guid)
    personnel_id: Mapped[UUID] = mapped_column(Uuid(),ForeignKey('personnel.id',ondelete='RESTRICT'),nullable=False)
    category: Mapped[str] = mapped_column(String(50),nullable=False)
    starts_on: Mapped[date] = mapped_column(Date,nullable=False)
    ends_before: Mapped[date] = mapped_column(Date,nullable=False)
    status: Mapped[str] = mapped_column(String(24),default='REQUESTED',nullable=False)
    approver_id: Mapped[str | None] = mapped_column(String(160))
    __table_args__ = (CheckConstraint('starts_on < ends_before',name='ck_leave_dates'),)

class ChangeRequest(Base):
    __tablename__ = 'change_request'
    id: Mapped[UUID] = mapped_column(Uuid(),primary_key=True,default=guid)
    action: Mapped[str] = mapped_column(String(60),nullable=False)
    payload: Mapped[dict] = mapped_column(JSON,nullable=False)
    reference: Mapped[str] = mapped_column(String(160),nullable=False)
    status: Mapped[str] = mapped_column(String(24),default='SUBMITTED',nullable=False)
    requested_by: Mapped[str] = mapped_column(String(180),nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(180))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=now,nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(String(250))
    __table_args__ = (CheckConstraint("status IN ('SUBMITTED','APPROVED','REJECTED','APPLIED')",name='ck_request_status'),)

class AuditEvent(Base):
    __tablename__='audit_event'
    id: Mapped[UUID] = mapped_column(Uuid(),primary_key=True,default=guid)
    actor: Mapped[str] = mapped_column(String(180),nullable=False)
    action: Mapped[str] = mapped_column(String(80),nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(60),nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid())
    outcome: Mapped[str] = mapped_column(String(20),nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(80),nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime,default=now,nullable=False)
    detail: Mapped[dict] = mapped_column(JSON,default=dict,nullable=False)

class OutboxEvent(Base):
    __tablename__='outbox_event'
    sequence: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    id: Mapped[UUID] = mapped_column(Uuid(),unique=True,nullable=False,default=guid)
    kind: Mapped[str] = mapped_column(String(100),nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(),nullable=False)
    payload: Mapped[dict] = mapped_column(JSON,nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime,default=now,nullable=False)
    __table_args__ = (Index('ix_outbox_at','at','id'),)

class ExternalID(Base):
    __tablename__='external_id'
    id: Mapped[UUID] = mapped_column(Uuid(),primary_key=True,default=guid)
    source: Mapped[str] = mapped_column(String(70),nullable=False)
    entity_type: Mapped[str] = mapped_column(String(70),nullable=False)
    source_id: Mapped[str] = mapped_column(String(180),nullable=False)
    canonical_id: Mapped[UUID] = mapped_column(Uuid(),nullable=False)
    __table_args__ = (UniqueConstraint('source','entity_type','source_id',name='uq_external_id'),)
