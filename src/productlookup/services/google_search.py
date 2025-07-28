# services/google_search.py
import requests
import logging
import os
import asyncio
from typing import List, Dict, Any
from productlookup.exceptions import ProductLookupError
from productlookup.protos import product_search_pb2

logger = logging.getLogger(__name__)


class GoogleSearchService:
    """Service for searching products using Google Programmable Search Engine"""

    def __init__(self):
        self.search_engine_id = os.getenv("GOOGLE_SEARCH_ENGINE_ID", "d3daf05153a424949")
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self.base_url = "https://customsearch.googleapis.com/customsearch/v1"
        self.logger = logging.getLogger(__name__)

        # with a default value of 5 and a hard limit of 10
        max_allowed = 10
        default_max = 5
        configured_max = int(os.getenv("MAX_SEARCH_RESULTS", default_max))
        self.max_search_results = min(configured_max, max_allowed)
        self.logger.info(f"Maximum search results set to: {self.max_search_results}")

    async def search(self, query: str, max_results: int = 5) -> List[product_search_pb2.ProductData]:
        """
        Async method to search for products and return them as ProductData objects
        """
        try:

            # Use configured value if max_results not explicitly provided
            if max_results is None:
                max_results = self.max_search_results
            else:
                # Ensure we don't exceed the configured limit
                max_results = min(max_results, self.max_search_results)


            # Call the synchronous method in a thread pool
            loop = asyncio.get_event_loop()
            search_results = await loop.run_in_executor(
                None, self.search_products, query, max_results
            )

            # Convert search results to ProductData objects
            products = []
            for result in search_results:
                url = result.get("link")
                title = result.get("title", "")
                snippet = result.get("snippet", "")

                if url:
                    product = product_search_pb2.ProductData(
                        sku_id="",
                        product_name=title,
                        brand="",
                        description=snippet,
                        price="",
                        product_url=url
                    )
                    products.append(product)

            self.logger.info(f"Found {len(products)} products via Google Search API")
            return products

        except Exception as e:
            self.logger.error(f"Error in async search: {str(e)}", exc_info=True)
            return []

    def search_products(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Synchronous method to search for products
        Returns raw search results as dictionaries
        """
        try:

            # Use configured value if max_results not explicitly provided
            if max_results is None:
                max_results = self.max_search_results
            else:
                # Ensure we don't exceed the configured limit
                max_results = min(max_results, self.max_search_results)

            params = {
                "key": self.api_key,
                "cx": self.search_engine_id,
                "q": query,
                "num": max_results
            }

            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()

            if "items" in data:
                return data["items"]
            return []

        except Exception as e:
            self.logger.error(f"Error searching products: {str(e)}")
            return []