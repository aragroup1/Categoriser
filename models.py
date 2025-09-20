from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, JSON, Float
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
    retry_count = Column(Integer, default=0)
    applied = Column(Boolean, default=False)  # Whether assignments were applied to Shopify
    confidence_scores = Column(JSON)  # Store confidence scores for audit

class CollectionSuggestion(Base):
    __tablename__ = 'collection_suggestions'
    
    id = Column(Integer, primary_key=True)
    suggested_name = Column(String(200), unique=True)
    parent_collection = Column(String(200))
    product_ids = Column(JSON)
    product_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50), default='pending')  # pending, approved, rejected
    confidence_avg = Column(Float)  # Average confidence for this suggestion

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
    product_count = Column(Integer, default=0)  # Track products per collection

class SystemLog(Base):
    __tablename__ = 'system_logs'
    
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    level = Column(String(20))  # INFO, WARNING, ERROR
    component = Column(String(50))  # shopify_client, ai_classifier, scheduler
    message = Column(Text)
    details = Column(JSON)

# Database setup with connection pooling
engine = create_engine(
    os.getenv('DATABASE_URL', 'sqlite:///shopify_ai.db'),
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True  # Verify connections before using
)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
