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
                    time.sleep(self.retry_delay * (retry_count + 1))
                    return self.classify_product(product_title, product_description, retry_count + 1)
                else:
                    raise Exception("OpenAI rate limit exceeded after retries")
                    
            except openai.APITimeoutError:
                if retry_count < self.max_retries:
                    time.sleep(self.retry_delay)
                    return self.classify_product(product_title, product_description, retry_count + 1)
                else:
                    raise Exception("OpenAI API timeout after retries")
                    
            except json.JSONDecodeError:
                # Log the error and return a default structure
                self._log_error("JSON decode error", {"response": str(response)})
                return {
                    "assigned_collections": [],
                    "new_collection_suggestion": None
                }
                
        except Exception as e:
            self._log_error(f"Classification error: {str(e)}", {
                "product_title": product_title,
                "retry_count": retry_count
            })
            
            # Return empty result on error
            return {
                "assigned_collections": [],
                "new_collection_suggestion": None,
                "error": str(e)
            }
            
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
            
        except Exception as e:
            db.rollback()
            self._log_error("Error processing collection suggestion", {"error": str(e)})
        finally:
            db.close()
    
    def process_queue(self, batch_size=10):
        """Process pending products in queue with failsafes"""
        db = Session()
        processed_count = 0
        
        try:
            # Get pending products with retry limit
            pending_products = db.query(ProductQueue).filter(
                ProductQueue.status == 'pending',
                ProductQueue.retry_count < 3
            ).limit(batch_size).all()
            
            for product in pending_products:
                try:
                    # Skip if no title
                    if not product.title:
                        product.status = 'error'
                        product.error_message = 'Product has no title'
                        continue
                    
                    # Classify product
                    result = self.classify_product(
                        product.title, 
                        product.description or ''
                    )
                    
                    if result.get('error'):
                        product.status = 'error'
                        product.error_message = result['error']
                        product.retry_count += 1
                    else:
                        # Update queue item
                        product.assigned_collections = result.get('assigned_collections', [])
                        product.status = 'processed'
                        product.processed_at = datetime.utcnow()
                        product.confidence_scores = [
                            c.get('confidence', 0) for c in result.get('assigned_collections', [])
                        ]
                        
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
                        
                        processed_count += 1
                    
                    db.commit()
                    
                    # Rate limiting between products
                    time.sleep(0.5)
                    
                except Exception as e:
                    product.status = 'error'
                    product.error_message = str(e)
                    product.retry_count += 1
                    db.commit()
                    
                    self._log_error(f"Error processing product {product.product_id}", {
                        "error": str(e),
                        "product_id": product.product_id
                    })
            
            return processed_count
            
        except Exception as e:
            self._log_error("Queue processing error", {"error": str(e)})
            return processed_count
        finally:
            db.close()
    
    def _log_error(self, message, details=None):
        """Log errors to database"""
        db = Session()
        try:
            log_entry = SystemLog(
                level='ERROR',
                component='ai_classifier',
                message=message,
                details=details or {}
            )
            db.add(log_entry)
            db.commit()
        except:
            pass  # Don't let logging errors break the main flow
        finally:
            db.close()
        
        # Also log to console
        logger.error(f"{message}: {details}")
