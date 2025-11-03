import os
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime
from urllib.parse import quote_plus


DB_USER = os.environ.get('DB_USER', 'root')
DB_PASS = os.environ.get('DB_PASS', 'Sharan@1383')
DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
DB_PORT = os.environ.get('DB_PORT', '3306')
DB_NAME = os.environ.get('DB_NAME', 'timetable')

DATABASE_URL = f"mysql+mysqlconnector://{DB_USER}:{quote_plus(DB_PASS)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(150), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    department = Column(String(150), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    projects = relationship('TimetableProject', back_populates='owner', cascade='all, delete-orphan')


class TimetableProject(Base):
    __tablename__ = 'timetable_projects'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    project_name = Column(String(200), nullable=False)
    config_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship('User', back_populates='projects')
    timetables = relationship('TimetableData', back_populates='project', cascade='all, delete-orphan')


class TimetableData(Base):
    __tablename__ = 'timetable_data'
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey('timetable_projects.id'), nullable=False)
    year_key = Column(String(16), nullable=False)  # SY, TY, BTech
    matrix_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship('TimetableProject', back_populates='timetables')


def init_db():
    """Initialize database tables. Creates tables if they don't exist."""
    # Check if tables exist and have correct schema
    inspector = inspect(engine)
    tables_exist = inspector.has_table('users')
    
    if tables_exist:
        # Check if users table has created_at column
        columns = [col['name'] for col in inspector.get_columns('users')]
        if 'created_at' not in columns:
            # Schema is outdated - recreate tables
            # WARNING: This will delete all existing data!
            print("Warning: Database schema outdated. Recreating tables (this will delete all data)...")
            
            # Disable foreign key checks, drop all tables (including old ones), then re-enable
            with engine.begin() as conn:
                # Disable foreign key checks
                conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
                
                # Get all existing table names and drop them
                all_tables = inspector.get_table_names()
                for table_name in all_tables:
                    conn.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
                
                # Re-enable foreign key checks
                conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    
    # Create tables if they don't exist (or after dropping outdated ones)
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


