import os
import time
from datetime import datetime, timedelta

import shopify
from models import Session, CollectionHierarchy, ProductQueue


class ShopifyClient:
    def __init__(self):
        # Normalize shop domain (no protocol)
        raw = os.getenv('SHOPIFY_SHOP_URL', '').strip()
        raw = raw.replace('https://', '').replace('http://', '').strip('/')

        if not raw:
            raise RuntimeError("SHOPIFY_SHOP_URL is not set. Example: your-store.myshopify.com")

        self.shop_domain = raw
        self.env_version = os.getenv('SHOPIFY_API_VERSION', '').strip()
        self.access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')
        if not self.access_token:
            raise RuntimeError("SHOPIFY_ACCESS_TOKEN is not set.")

        # Delimiter for your L1 > L2 > L3 titles
        self.sep = os.getenv('COLLECTION_PATH_SEPARATOR', ' > ')

        # Try to activate a valid API version (auto-fallback if env is wrong)
        self.api_version = None
        self._activate_session()

    def _activate_session(self):
        """
        Attempt to activate a Shopify session with a valid API version.
        Tries the env version first, then a list of known-good fallbacks, then 'unstable'.
        """
        # Order matters: env -> common stable -> unstable
        candidates = []
        if self.env_version:
            candidates.append(self.env_version)

        # Add a list of known-good recent stables (adjusted to cover most SDK releases)
        # We intentionally include multiple so older SDKs still find a valid match.
        fallbacks = [
            "2024-10",
            "2024-07",
            "2024-04",
            "2023-10",
            "unstable",
        ]
        for v in fallbacks:
            if v not in candidates:
                candidates.append(v)

        last_err = None
        for ver in candidates:
            try:
                session = shopify.Session(self.shop_domain, ver, self.access_token)
                shopify.ShopifyResource.activate_session(session)
                self.api_version = ver
                print(f"[ShopifyClient] Activated API version: {ver}")
                return
            except Exception as e:
                # If this is a version error, try next; otherwise bubble up
                if e.__class__.__name__ == "VersionNotFoundError":
                    last_err = e
                    continue
                last_err = e
                # Non-version errors shouldn’t be auto-retried with other versions
                break

        raise RuntimeError(
            f"Failed to activate Shopify session. Last error: {last_err}. "
            f"Set a valid SHOPIFY_API_VERSION (e.g., 2024-04) in Railway variables."
        )

    def fetch_all_collections(self):
        """
        Pull all custom + smart collections and rebuild the simple 3-level hierarchy
        based on the title separator (default ' > ').
        """
        db = Session()
        try:
            all_collections = []

            # Custom Collections
            cc = shopify.CustomCollection.find(limit=250)
            while True:
                all_collections.extend(cc)
                if not hasattr(cc, 'has_next_page') or not cc.has_next_page():
                    break
                cc = cc.next_page()
                time.sleep(0.2)

            # Smart Collections
            sc = shopify.SmartCollection.find(limit=250)
            while True:
                all_collections.extend(sc)
                if not hasattr(sc, 'has_next_page') or not sc.has_next_page():
                    break
                sc = sc.next_page()
                time.sleep(0.2)

            # Rebuild hierarchy table
            db.query(CollectionHierarchy).delete()
            db.commit()

            for c in all_collections:
                title = (c.title or '').strip()
                if self.sep in title:
                    parts = [p.strip() for p in title.split(self.sep)]
                else:
                    parts = [title] if title else []

                level = min(len(parts), 3) if parts else 1
                full_path = self.sep.join(parts[:level]) if parts else title

                row = CollectionHierarchy(
                    collection_id=str(c.id),
                    handle=c.handle or '',
                    title=title,
                    level=level,
                    parent_id=None,  # No native parent concept in Shopify collections
                    full_path=full_path,
                    updated_at=datetime.utcnow()
                )
                db.add(row)

            db.commit()
            return len(all_collections)
        finally:
            db.close()

    def fetch_products_for_scanning(self, since_date=None):
        """
        Queue products updated since 'since_date' (default: 1 day).
        """
        db = Session()
        try:
            if not since_date:
                since_date = datetime.utcnow() - timedelta(days=1)

            total = 0
            batch = shopify.Product.find(updated_at_min=since_date.isoformat(), limit=250)
            while True:
                for p in batch:
                    total += 1
                    pid = str(p.id)
                    exists = db.query(ProductQueue).filter_by(product_id=pid).first()
                    if not exists:
                        db.add(ProductQueue(
                            product_id=pid,
                            title=p.title or '',
                            description=(p.body_html or '')[:10000],  # trim super long HTML
                            status='pending'
                        ))
                db.commit()

                if not hasattr(batch, 'has_next_page') or not batch.has_next_page():
                    break
                batch = batch.next_page()
                time.sleep(0.3)

            return total
        finally:
            db.close()

    def get_product_current_collections(self, product_id):
        collects = shopify.Collect.find(product_id=product_id, limit=250)
        out = [str(c.collection_id) for c in collects]
        while hasattr(collects, 'has_next_page') and collects.has_next_page():
            collects = collects.next_page()
            out.extend([str(c.collection_id) for c in collects])
            time.sleep(0.2)
        return out

    def update_product_collections(self, product_id, target_collection_ids, level_map=None, max_l3=2, cleanup=False):
        """
        Adds product to target collections if not already present.
        Enforces max_l3 for level 3 collections.
        If cleanup=True, removes extra L3 assignments beyond the limit.
        """
        current_ids = set(self.get_product_current_collections(product_id))
        to_add = [cid for cid in target_collection_ids if cid not in current_ids]

        # Enforce L3 limit
        l3_current = 0
        if level_map:
            l3_current = sum(1 for cid in current_ids if level_map.get(cid) == 3)

        allowed_add = []
        for cid in to_add:
            if level_map and level_map.get(cid) == 3:
                if l3_current < max_l3:
                    allowed_add.append(cid)
                    l3_current += 1
            else:
                allowed_add.append(cid)

        for cid in allowed_add:
            collect = shopify.Collect()
            collect.product_id = product_id
            collect.collection_id = cid
            collect.save()
            time.sleep(0.25)  # rate-limit friendliness

        # Optional cleanup for excess L3
        if cleanup and level_map:
            current_ids = set(self.get_product_current_collections(product_id))  # refresh
            l3_ids = [cid for cid in current_ids if level_map.get(cid) == 3]
            if len(l3_ids) > max_l3:
                keep = set(target_collection_ids[:max_l3])
                remove_ids = [cid for cid in l3_ids if cid not in keep][: max(0, len(l3_ids) - max_l3)]
                for cid in remove_ids:
                    collects = shopify.Collect.find(product_id=product_id, collection_id=cid)
                    for col in collects:
                        col.destroy()
                        time.sleep(0.2)
        return True