from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

Base = declarative_base()

class ProductQueue(Base):
    __tablename__ = 'product_queue'
    
    id = Column(Integer, primary_key=True)
    product_id = Column(String(50), unique=True)
    title = Column(String(500))
    description = Column(Text)
    status = Column(String(50), default='pending')  # pending, processed, error
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
    assigned_collections = Column(JSON)
    error_message = Column(Text)

class CollectionSuggestion(Base):
    __tablename__ = 'collection_suggestions'
    
    id = Column(Integer, primary_key=True)
    suggested_name = Column(String(200), unique=True)
    parent_collection = Column(String(200))
    product_ids = Column(JSON)
    product_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50), default='pending')  # pending, approved, rejected

class CollectionHierarchy(Base):
    __tablename__ = 'collection_hierarchy'
    
    id = Column(Integer, primary_key=True)
    collection_id = Column(String(50), unique=True)
    handle = Column(String(200))
    title = Column(String(200))
    level = Column(Integer)  # 1, 2, or 3
    parent_id = Column(String(50))
    full_path = Column(String(500))
    updated_at = Column(DateTime, default=datetime.utcnow)

# Database setup
engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:///shopify_ai.db'))
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
