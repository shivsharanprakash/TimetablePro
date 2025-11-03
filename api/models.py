from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from .db import Base


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(150), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    department = Column(String(150), nullable=True)

    configs = relationship('TimetableConfig', back_populates='owner')


class TimetableConfig(Base):
    __tablename__ = 'timetable_configs'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    config_name = Column(String(200), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship('User', back_populates='configs')
    timetables = relationship('FinalTimetable', back_populates='config')


class FinalTimetable(Base):
    __tablename__ = 'final_timetables'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    config_id = Column(Integer, ForeignKey('timetable_configs.id'), nullable=False)
    year_key = Column(String(16), nullable=False)  # SY, TY, BTech
    matrix_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    config = relationship('TimetableConfig', back_populates='timetables')




