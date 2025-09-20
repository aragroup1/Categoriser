import openai
import os
import json
from models import Session, CollectionHierarchy, ProductQueue, CollectionSuggestion, SystemLog
from datetime import datetime
import time
import logging

logger = logging.getLogger(__name__)

class AIClassifier:
    def __init__(self):
        openai.api_key = os.getenv('OPENAI_API_KEY')
        self.client = openai.OpenAI()
        self.max_retries = 3
        self.retry_delay = 2
    
    def classify_product(self, product_title, product_description, retry_count=0):
        """Use AI to classify product into collections with retry logic"""
        db = Session()
        try:
            # Get all L3 collections with their full paths
            l3_collections = db.query(CollectionHierarchy).filter_by(level=3).all()
            
            if not l3_collections:
                # Fallback to L2 if no L3
                l3_collections = db.query(CollectionHierarchy).filter_by(level=2).all()
            
            if not l3_collections:
                # Fallback to L1 if no L2
                l3_collections = db.query(CollectionHierarchy).filter_by(level=1).all()
            
            if not l3_collections:
                raise Exception("No collections found in hierarchy")
            
            collection_list = [
                {
                    'id': c.collection_id,
                    'title': c.title,
                    'path': c.full_path
                }
                for c in l3_collections
            ]
            
            # Truncate description if too long (token limit failsafe)
            if len(product_description) > 2000:
                product_description = product_description[:2000] + "..."
            
            prompt = f"""
            You are a product categorization expert for an e-commerce store. 
            
            Product Title: {product_title}
            Product Description: {product_description}
            
            Available Collections (Level 3):
            {json.dumps(collection_list[:50], indent=2)}  # Limit to 50 collections to avoid token limits
            
            Task:
            1. Analyze the product and assign it to 1-2 most relevant collections from the list above.
            2. If the product doesn't fit well into any existing collection, suggest a new collection name and identify the closest parent collection.
            
            Return a JSON response in this exact format:
            {{
                "assigned_collections": [
                    {{"id": "collection_id", "title": "collection_title", "confidence": 0.95}}
                ],
                "new_collection_suggestion": {{
                    "suggested_name": "New Collection Name",
                    "parent_collection": "Parent Collection Title",
                    "reason": "Brief explanation"
                }}
            }}
            
            Rules:
            - Assign to maximum 2 collections
            - Only suggest new collection if confidence for all existing collections is below 0.7
            - Be specific and accurate in your assignments
            - Always return valid JSON
            """
            
            try:
                response = self.client.chat.completions.create(
                    model="gpt-4-turbo-preview",
                    messages=[
                        {"role": "system", "content": "You are a product categorization expert. Always return valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    response_format={"type": "json_object"},
                    timeout=30  # 30 second timeout
                )
                
                result = json.loads(response.choices[0].message.content)
                
                # Validate result structure
                if 'assigned_collections' not in result:
                    result['assigned_collections'] = []
                
                # Process new collection suggestion if any
                if result.get('new_collection_suggestion') and result['new_collection_suggestion'].get('suggested_name'):
                    self._process_collection_suggestion(
                        result['new_collection_suggestion'],
                        product_id=None
                    )
                
                return result
                
            except openai.RateLimitError:
                if retry_count < self.max_retries:
                    time.sleep(self.retry_delay * (
