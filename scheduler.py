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
