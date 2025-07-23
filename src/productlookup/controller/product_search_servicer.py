# controller/product_search_servicer.py
import logging
import grpc
import asyncio
from typing import List, Dict, Any
from productlookup.services.google_search import GoogleSearchService
from productlookup.services.web_crawler import WebCrawlerService
from productlookup.exceptions import ProductLookupError
from productlookup.protos import product_search_pb2

logger = logging.getLogger(__name__)


class ProductSearchServicer:
    """Servicer for product search operations"""

    def __init__(self):
        """Initialize services"""
        self.google_service = GoogleSearchService()
        self.web_crawler = WebCrawlerService()
        self.logger = logging.getLogger(__name__)

    async def initialize(self):
        """Initialize services that require async setup"""
        await self.web_crawler.initialize()

    async def SearchProduct(self, request, context):
        """Handle product search requests"""
        query = request.query
        self.logger.info(f"Received search request for: {query}")

        try:
            # First try Google Search API
            products = await self.google_service.search(query)

            if products and len(products) > 0:
                self.logger.info(f"Found {len(products)} basic products, fetching details...")
                try:
                    # Enrich with detailed information
                    enriched_products = await self.web_crawler.get_detailed_product_info(products)

                    # Log if part numbers were found
                    for product in enriched_products:
                        if product.product_id:
                            self.logger.info(f"Found part number '{product.product_id}' for {product.product_url}")
                        else:
                            self.logger.warning(f"No part number found for {product.product_url}")

                    self.logger.info(f"Successfully enriched {len(enriched_products)} products")
                    return product_search_pb2.SearchProductResponse(products=enriched_products)
                except Exception as e:
                    self.logger.error(f"Failed to enrich products: {str(e)}", exc_info=True)
                    # Fall back to basic product info if enrichment fails
                    return product_search_pb2.SearchProductResponse(products=products)
            else:
                self.logger.warning(f"No products found for query: {query}")
                return product_search_pb2.SearchProductResponse(products=[])

        except Exception as e:
            self.logger.error(f"Search failed: {str(e)}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to search product: {str(e)}")
            return product_search_pb2.SearchProductResponse()


    # controller/product_search_servicer.py
    def search_product(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for products and extract data (synchronous version)
        """
        try:
            # Search for products (this is synchronous)
            search_results = self.google_service.search_products(query)

            # Process all results in a single asyncio.run call
            return asyncio.run(self._process_all_search_results(search_results))

        except Exception as e:
            logger.error(f"Error in search_product: {str(e)}")
            raise ProductLookupError(f"Failed to search product: {str(e)}")

    async def _process_all_search_results(self, search_results):
        """Process all search results in a single async operation"""
        try:
            # Initialize the web crawler once
            await self.web_crawler.initialize()

            # Create all basic product objects first
            basic_products = []
            for result in search_results:
                url = result.get("link")
                if url:
                    basic_product = product_search_pb2.ProductData(
                        product_id="",
                        product_name=result.get("title", ""),
                        description=result.get("snippet", ""),
                        product_url=url
                    )
                    basic_products.append(basic_product)

            # Process all products in a single call
            if not basic_products:
                return []

            enriched_products = await self.web_crawler.get_detailed_product_info(basic_products)

            # Convert to dictionary format
            products = []
            for product in enriched_products:
                product_dict = {
                    "product_id": product.product_id,
                    "product_name": product.product_name,
                    "brand": product.brand,
                    "price": product.price,
                    "description": product.description,
                    "product_url": product.product_url
                }
                products.append(product_dict)

            return products

        finally:
            # Clean up browser resources
            if hasattr(self.web_crawler, 'browser_context') and self.web_crawler.browser_context:
                await self.web_crawler.browser_context.close()
            if hasattr(self.web_crawler, 'browser') and self.web_crawler.browser:
                await self.web_crawler.browser.close()