import shopify
import os
from datetime import datetime, timedelta
from models import Session, CollectionHierarchy, ProductQueue
import time
import requests

class ShopifyClient:
    def __init__(self):
        # Use the REST API directly with requests for better control
        self.shop_url = os.getenv('SHOPIFY_SHOP_URL')
        self.access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')
        self.api_version = '2023-10'  # Use stable version
        self.base_url = f"https://{self.shop_url}/admin/api/{self.api_version}"
        self.headers = {
            'X-Shopify-Access-Token': self.access_token,
            'Content-Type': 'application/json'
        }
        
    def _make_request(self, endpoint, method='GET', data=None, params=None):
        """Make API request with error handling"""
        url = f"{self.base_url}/{endpoint}"
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=self.headers, params=params)
            elif method == 'POST':
                response = requests.post(url, headers=self.headers, json=data)
            elif method == 'PUT':
                response = requests.put(url, headers=self.headers, json=data)
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"API request error: {e}")
            return None
    
    def fetch_all_collections(self):
        """Fetch all collections and build hierarchy"""
        db = Session()
        try:
            all_collections = []
            
            # Fetch custom collections
            params = {'limit': 250}
            while True:
                result = self._make_request('custom_collections.json', params=params)
                if not result or 'custom_collections' not in result:
                    break
                
                collections = result['custom_collections']
                all_collections.extend(collections)
                
                # Check for pagination
                if len(collections) < 250:
                    break
                    
                # Get next page
                if 'Link' in result.get('headers', {}):
                    # Parse link header for next page
                    params['page_info'] = self._extract_page_info(result['headers']['Link'])
                else:
                    break
            
            # Fetch smart collections
            params = {'limit': 250}
            while True:
                result = self._make_request('smart_collections.json', params=params)
                if not result or 'smart_collections' not in result:
                    break
                
                collections = result['smart_collections']
                all_collections.extend(collections)
                
                if len(collections) < 250:
                    break
            
            # Build hierarchy
            self._build_collection_hierarchy(all_collections, db)
            
            return all_collections
            
        finally:
            db.close()
    
    def _build_collection_hierarchy(self, collections, db):
        """Analyze collection titles to determine hierarchy"""
        # Clear existing hierarchy
        db.query(CollectionHierarchy).delete()
        
        for collection in collections:
            title = collection.get('title', '')
            handle = collection.get('handle', '')
            collection_id = str(collection.get('id', ''))
            
            # Determine level based on title structure
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
                parent_title = ' > '.join(parts[:-1])
                parent = db.query(CollectionHierarchy).filter_by(title=parent_title).first()
                if parent:
                    parent_id = parent.collection_id
            
            hierarchy = CollectionHierarchy(
                collection_id=collection_id,
                handle=handle,
                title=title,
                level=min(level, 3),
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
            
            products_added = 0
            params = {
                'limit': 250,
                'updated_at_min': since_date.isoformat()
            }
            
            while True:
                result = self._make_request('products.json', params=params)
                if not result or 'products' not in result:
                    break
                
                products = result['products']
                
                for product in products:
                    # Check if product is already in queue
                    existing = db.query(ProductQueue).filter_by(
                        product_id=str(product['id']),
                        status='pending'
                    ).first()
                    
                    if not existing:
                        queue_item = ProductQueue(
                            product_id=str(product['id']),
                            title=product.get('title', ''),
                            description=product.get('body_html', ''),
                            status='pending'
                        )
                        db.add(queue_item)
                        products_added += 1
                
                if len(products) < 250:
                    break
                
                # Rate limit protection
                time.sleep(0.5)
            
            db.commit()
            return products_added
            
        finally:
            db.close()
    
    def update_product_collections(self, product_id, collection_ids):
        """Update product's collection assignments"""
        try:
            # First, get existing collects for this product
            existing_collects = self._make_request(f'collects.json?product_id={product_id}')
            
            # Remove existing collects
            if existing_collects and 'collects' in existing_collects:
                for collect in existing_collects['collects']:
                    self._make_request(f'collects/{collect["id"]}.json', method='DELETE')
                    time.sleep(0.2)
            
            # Add new collects
            for collection_id in collection_ids:
                data = {
                    'collect': {
                        'product_id': int(product_id),
                        'collection_id': int(collection_id)
                    }
                }
                self._make_request('collects.json', method='POST', data=data)
                time.sleep(0.2)
            
            return True
        except Exception as e:
            print(f"Error updating product collections: {e}")
            return False
    
    def get_product_current_collections(self, product_id):
        """Get current collection assignments for a product"""
        result = self._make_request(f'collects.json?product_id={product_id}')
        if result and 'collects' in result:
            return [str(c['collection_id']) for c in result['collects']]
        return []
    
    def _extract_page_info(self, link_header):
        """Extract page_info from Link header"""
        # This is a simplified version - you might need to parse the Link header properly
        return None
