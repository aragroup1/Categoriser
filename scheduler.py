from apscheduler.schedulers.background import BackgroundScheduler
from shopify_client import ShopifyClient
from ai_classifier import AIClassifier
from models import Session, ProductQueue, CollectionSuggestion
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TaskScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.shopify_client = ShopifyClient()
        self.ai_classifier = AIClassifier()
    
    def start(self):
        # Schedule daily product scan at 2 AM
        self.scheduler.add_job(
            func=self.scan_new_products,
            trigger="cron",
            hour=2,
            minute=0,
            id='daily_product_scan'
        )
        
        # Schedule queue processing every 30 minutes
        self.scheduler.add_job(
            func=self.process_queue,
            trigger="interval",
            minutes=30,
            id='process_queue'
        )
        
        # Schedule collection sync every 6 hours
        self.scheduler.add_job(
            func=self.sync_collections,
            trigger="interval",
            hours=6,
            id='sync_collections'
        )
        
        self.scheduler.start()
        logger.info("Scheduler started")
    
    def scan_new_products(self):
        """Daily scan for new products"""
        try:
            logger.info("Starting daily product scan")
            count = self.shopify_client.fetch_products_for_scanning()
            logger.info(f"Scanned {count} new products")
        except Exception as e:
            logger.error(f"Error in daily product scan: {e}")
    
    def process_queue(self):
        """Process pending products in queue"""
        try:
            logger.info("Processing product queue")
            processed = self.ai_classifier.process_queue(batch_size=20)
            logger.info(f"Processed {processed} products")
        except Exception as e:
            logger.error(f"Error processing queue: {e}")
    
    def sync_collections(self):
        """Sync collection hierarchy"""
        try:
            logger.info("Syncing collections")
            self.shopify_client.fetch_all_collections()
            logger.info("Collection sync completed")
        except Exception as e:
            logger.error(f"Error syncing collections: {e}")
    
    def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped")
