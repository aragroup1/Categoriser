from flask import Flask, render_template, jsonify, request, redirect, url_for
from models import Session, ProductQueue, CollectionSuggestion, CollectionHierarchy
from sqlalchemy import func
import os
from datetime import datetime, timedelta
import logging

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize components lazily to avoid startup errors
shopify_client = None
ai_classifier = None
scheduler = None
initialized = False

def init_components():
    """Initialize components after app starts"""
    global shopify_client, ai_classifier, scheduler, initialized
    
    if initialized:
        return
    
    try:
        from shopify_client import ShopifyClient
        shopify_client = ShopifyClient()
        
        from ai_classifier import AIClassifier
        ai_classifier = AIClassifier()
        
        from scheduler import TaskScheduler
        scheduler = TaskScheduler()
        scheduler.start()
        
        # Try initial collection sync
        try:
            shopify_client.fetch_all_collections()
        except Exception as e:
            logger.error(f"Initial collection sync failed: {e}")
        
        initialized = True
        logger.info("Components initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize components: {e}")

@app.route('/')
def dashboard():
    """Main dashboard view"""
    init_components()  # Initialize on first request
    
    db = Session()
    try:
        # Get statistics
        stats = {
            'total_products_queued': db.query(ProductQueue).count(),
            'pending_products': db.query(ProductQueue).filter_by(status='pending').count(),
            'processed_products': db.query(ProductQueue).filter_by(status='processed').count(),
            'error_products': db.query(ProductQueue).filter_by(status='error').count(),
            'collection_suggestions': db.query(CollectionSuggestion).filter_by(status='pending').count(),
            'ready_suggestions': db.query(CollectionSuggestion).filter(
                CollectionSuggestion.product_count >= 5,
                CollectionSuggestion.status == 'pending'
            ).count(),
            'total_collections': db.query(CollectionHierarchy).count(),
            'l1_collections': db.query(CollectionHierarchy).filter_by(level=1).count(),
            'l2_collections': db.query(CollectionHierarchy).filter_by(level=2).count(),
            'l3_collections': db.query(CollectionHierarchy).filter_by(level=3).count(),
        }
        
        # Get recent activity
        recent_processed = db.query(ProductQueue).filter_by(
            status='processed'
        ).order_by(ProductQueue.processed_at.desc()).limit(10).all()
        
        # Get ready suggestions (5+ products)
        ready_suggestions = db.query(CollectionSuggestion).filter(
            CollectionSuggestion.product_count >= 5,
            CollectionSuggestion.status == 'pending'
        ).order_by(CollectionSuggestion.product_count.desc()).all()
        
        # Get recent errors
        recent_errors = db.query(ProductQueue).filter_by(
            status='error'
        ).order_by(ProductQueue.created_at.desc()).limit(5).all()
        
        return render_template('dashboard.html', 
                             stats=stats, 
                             recent_processed=recent_processed,
                             ready_suggestions=ready_suggestions,
                             recent_errors=recent_errors)
    finally:
        db.close()

@app.route('/scan-products', methods=['POST'])
def scan_products():
    """Manually trigger product scan"""
    try:
        init_components()
        
        if not shopify_client:
            return jsonify({
                'success': False,
                'error': 'System not initialized'
            }), 500
        
        # Fetch collections first
        shopify_client.fetch_all_collections()
        
        # Scan products from last 7 days
        since_date = datetime.utcnow() - timedelta(days=7)
        count = shopify_client.fetch_products_for_scanning(since_date)
        
        return jsonify({
            'success': True,
            'message': f'Queued {count} products for processing'
        })
    except Exception as e:
        logger.error(f"Error scanning products: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/process-queue', methods=['POST'])
def process_queue():
    """Manually trigger queue processing"""
    try:
        init_components()
        
        if not ai_classifier:
            return jsonify({
                'success': False,
                'error': 'System not initialized'
            }), 500
        
        # Process with rate limiting
        batch_size = int(request.form.get('batch_size', 10))
        processed = ai_classifier.process_queue(batch_size=batch_size)
        
        return jsonify({
            'success': True,
            'message': f'Processed {processed} products'
        })
    except Exception as e:
        logger.error(f"Error processing queue: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/apply-assignments', methods=['POST'])
def apply_assignments():
    """Apply AI assignments to Shopify"""
    try:
        init_components()
        
        if not shopify_client:
            return jsonify({
                'success': False,
                'error': 'System not initialized'
            }), 500
        
        db = Session()
        
        # Get processed products that haven't been applied
        products = db.query(ProductQueue).filter_by(
            status='processed',
            applied=False
        ).limit(10).all()
        
        applied_count = 0
        for product in products:
            if product.assigned_collections:
                collection_ids = [c['id'] for c in product.assigned_collections if c.get('confidence', 0) > 0.8]
                if collection_ids:
                    success = shopify_client.update_product_collections(
                        product.product_id,
                        collection_ids
                    )
                    if success:
                        product.applied = True
                        applied_count += 1
                    else:
                        product.status = 'error'
                        product.error_message = 'Failed to update Shopify'
        
        db.commit()
        db.close()
        
        return jsonify({
            'success': True,
            'message': f'Applied assignments for {applied_count} products'
        })
    except Exception as e:
        logger.error(f"Error applying assignments: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/suggestions')
def suggestions():
    """View all collection suggestions"""
    init_components()
    
    db = Session()
    try:
        suggestions = db.query(CollectionSuggestion).order_by(
            CollectionSuggestion.product_count.desc()
        ).all()
        return render_template('suggestions.html', suggestions=suggestions)
    finally:
        db.close()

@app.route('/approve-suggestion/<int:suggestion_id>', methods=['POST'])
def approve_suggestion(suggestion_id):
    """Approve a collection suggestion"""
    db = Session()
    try:
        suggestion = db.query(CollectionSuggestion).get(suggestion_id)
        if suggestion:
            suggestion.status = 'approved'
            db.commit()
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Suggestion not found'}), 404
    finally:
        db.close()

@app.route('/add-collection', methods=['POST'])
def add_collection():
    """Manually add a new collection to the hierarchy"""
    db = Session()
    try:
        data = request.json
        new_collection = CollectionHierarchy(
            collection_id=data['collection_id'],
            handle=data['handle'],
            title=data['title'],
            level=data['level'],
            parent_id=data.get('parent_id'),
            full_path=data['full_path']
        )
        db.add(new_collection)
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.close()

@app.route('/retry-errors', methods=['POST'])
def retry_errors():
    """Retry failed products"""
    db = Session()
    try:
        # Reset error products to pending
        error_products = db.query(ProductQueue).filter_by(status='error').all()
        for product in error_products:
            product.status = 'pending'
            product.error_message = None
            product.retry_count = 0
        db.commit()
        
        return jsonify({
            'success': True,
            'message': f'Reset {len(error_products)} products for retry'
        })
    finally:
        db.close()

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy', 
        'timestamp': datetime.utcnow().isoformat(),
        'initialized': initialized
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))