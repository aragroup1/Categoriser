import shopify
import os
from datetime import datetime, timedelta
from models import Session, CollectionHierarchy, ProductQueue
import time

class ShopifyClient:
    def __init__(self):
        shop_url = f"https://{os.getenv('SHOPIFY_SHOP_URL')}"
        api_version = '2024-01'
        private_app_password = os.getenv('SHOPIFY_ACCESS_TOKEN')
        
        session = shopify.Session(shop_url, api_version, private_app_password)
        shopify.ShopifyResource.activate_session(session)
        
    def fetch_all_collections(self):
        """Fetch all collections and build hierarchy"""
        db = Session()
        try:
            collections = []
            page_info = None
            
            while True:
                if page_info:
                    custom_collections = shopify.CustomCollection.find(page_info=page_info, limit=250)
                else:
                    custom_collections = shopify.CustomCollection.find(limit=250)
                
                collections.extend(custom_collections)
                
                if not custom_collections.has_next_page():
                    break
                page_info = custom_collections.next_page_info
            
            # Also fetch smart collections
            page_info = None
            while True:
                if page_info:
                    smart_collections = shopify.SmartCollection.find(page_info=page_info, limit=250)
                else:
                    smart_collections = shopify.SmartCollection.find(limit=250)
                
                collections.extend(smart_collections)
                
                if not smart_collections.has_next_page():
                    break
                page_info = smart_collections.next_page_info
            
            # Build hierarchy
            self._build_collection_hierarchy(collections, db)
            
            return collections
            
        finally:
            db.close()
    
    def _build_collection_hierarchy(self, collections, db):
        """Analyze collection titles to determine hierarchy"""
        # Clear existing hierarchy
        db.query(CollectionHierarchy).delete()
        
        for collection in collections:
            title = collection.title
            handle = collection.handle
            collection_id = str(collection.id)
            
            # Determine level based on title structure (adjust based on your naming convention)
            # Example: "Men > Clothing > T-Shirts" or "Men - Clothing - T-Shirts"
            if ' > ' in title:
                parts = title.split(' > ')
            elif ' - ' in title:
                parts = title.split(' - ')
            elif ' / ' in title:
                parts = title.split(' / ')
            else:
                parts = [title]
            
            level = len(parts)
            parent_id = None
            
            # Find parent collection if level > 1
            if level > 1:
                parent_title = ' > '.join(parts[:-1])  # Adjust separator as needed
                parent = db.query(CollectionHierarchy).filter_by(title=parent_title).first()
                if parent:
                    parent_id = parent.collection_id
            
            hierarchy = CollectionHierarchy(
                collection_id=collection_id,
                handle=handle,
                title=title,
                level=min(level, 3),  # Cap at 3 levels
                parent_id=parent_id,
                full_path=title,
                updated_at=datetime.utcnow()
            )
            db.add(hierarchy)
        
        db.commit()
    
    def fetch_products_for_scanning(self, since_date=None):
        """Fetch products that need scanning"""
        db = Session()
        try:
            if not since_date:
                since_date = datetime.utcnow() - timedelta(days=1)
            
            products = []
            page_info = None
            
            while True:
                if page_info:
                    batch = shopify.Product.find(
                        updated_at_min=since_date.isoformat(),
                        page_info=page_info,
                        limit=250
                    )
                else:
                    batch = shopify.Product.find(
                        updated_at_min=since_date.isoformat(),
                        limit=250
                    )
                
                for product in batch:
                    # Check if product is already in queue
                    existing = db.query(ProductQueue).filter_by(
                        product_id=str(product.id),
                        status='pending'
                    ).first()
                    
                    if not existing:
                        queue_item = ProductQueue(
                            product_id=str(product.id),
                            title=product.title,
                            description=product.body_html or '',
                            status='pending'
                        )
                        db.add(queue_item)
                
                products.extend(batch)
                
                if not batch.has_next_page():
                    break
                page_info = batch.next_page_info
                
                # Rate limit protection
                time.sleep(0.5)
            
            db.commit()
            return len(products)
            
        finally:
            db.close()
    
    def update_product_collections(self, product_id, collection_ids):
        """Update product's collection assignments"""
        try:
            product = shopify.Product.find(product_id)
            
            for collection_id in collection_ids:
                collect = shopify.Collect()
                collect.product_id = product_id
                collect.collection_id = collection_id
                collect.save()
                time.sleep(0.2)  # Rate limit protection
            
            return True
        except Exception as e:
            print(f"Error updating product collections: {e}")
            return False
    
    def get_product_current_collections(self, product_id):
        """Get current collection assignments for a product"""
        collects = shopify.Collect.find(product_id=product_id)
        return [str(c.collection_id) for c in collects]
