import openai
import os
import json
from models import Session, CollectionHierarchy, ProductQueue, CollectionSuggestion
from datetime import datetime

class AIClassifier:
    def __init__(self):
        openai.api_key = os.getenv('OPENAI_API_KEY')
        self.client = openai.OpenAI()
    
    def classify_product(self, product_title, product_description):
        """Use AI to classify product into collections"""
        db = Session()
        try:
            # Get all L3 collections with their full paths
            l3_collections = db.query(CollectionHierarchy).filter_by(level=3).all()
            
            if not l3_collections:
                # If no L3 collections, use L2
                l3_collections = db.query(CollectionHierarchy).filter_by(level=2).all()
            
            collection_list = [
                {
                    'id': c.collection_id,
                    'title': c.title,
                    'path': c.full_path
                }
                for c in l3_collections
            ]
            
            prompt = f"""
            You are a product categorization expert for an e-commerce store. 
            
            Product Title: {product_title}
            Product Description: {product_description}
            
            Available Collections (Level 3):
            {json.dumps(collection_list, indent=2)}
            
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
            """
            
            response = self.client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": "You are a product categorization expert."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Process new collection suggestion if any
            if result.get('new_collection_suggestion') and result['new_collection_suggestion'].get('suggested_name'):
                self._process_collection_suggestion(
                    result['new_collection_suggestion'],
                    product_id=None  # Will be added when processing queue
                )
            
            return result
            
        finally:
            db.close()
    
    def _process_collection_suggestion(self, suggestion, product_id):
        """Process and store collection suggestions"""
        db = Session()
        try:
            suggested_name = suggestion['suggested_name']
            
            # Check if suggestion already exists
            existing = db.query(CollectionSuggestion).filter_by(
                suggested_name=suggested_name
            ).first()
            
            if existing:
                # Add product to existing suggestion
                product_ids = existing.product_ids or []
                if product_id and product_id not in product_ids:
                    product_ids.append(product_id)
                    existing.product_ids = product_ids
                    existing.product_count = len(product_ids)
            else:
                # Create new suggestion
                new_suggestion = CollectionSuggestion(
                    suggested_name=suggested_name,
                    parent_collection=suggestion.get('parent_collection', ''),
                    product_ids=[product_id] if product_id else [],
                    product_count=1 if product_id else 0
                )
                db.add(new_suggestion)
            
            db.commit()
            
        finally:
            db.close()
    
    def process_queue(self):
        """Process all pending products in queue"""
        db = Session()
        try:
            pending_products = db.query(ProductQueue).filter_by(status='pending').limit(50).all()
            
            for product in pending_products:
                try:
                    # Classify product
                    result = self.classify_product(product.title, product.description)
                    
                    # Update queue item
                    product.assigned_collections = result.get('assigned_collections', [])
                    product.status = 'processed'
                    product.processed_at = datetime.utcnow()
                    
                    # Update collection suggestion with product ID if needed
                    if result.get('new_collection_suggestion'):
                        suggestion = result['new_collection_suggestion']
                        if suggestion.get('suggested_name'):
                            existing = db.query(CollectionSuggestion).filter_by(
                                suggested_name=suggestion['suggested_name']
                            ).first()
                            if existing:
                                product_ids = existing.product_ids or []
                                if product.product_id not in product_ids:
                                    product_ids.append(product.product_id)
                                    existing.product_ids = product_ids
                                    existing.product_count = len(product_ids)
                    
                    db.commit()
                    
                except Exception as e:
                    product.status = 'error'
                    product.error_message = str(e)
                    db.commit()
            
            return len(pending_products)
            
        finally:
            db.close()
