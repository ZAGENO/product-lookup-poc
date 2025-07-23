# services/web_crawler.py
import logging
import asyncio
import os
import json
from playwright.async_api import async_playwright
from typing import Dict, Any, Optional, List
from productlookup.exceptions import ProductLookupError
from productlookup.protos import product_search_pb2
from productlookup.services.product_data_enricher import ProductDataEnricherService

logger = logging.getLogger(__name__)

# Default config path
DEFAULT_CONFIG_PATH = "/Users/arijitroy/PycharmProjects/product-lookup-poc/src/productlookup/config/crawler_config.json"

class WebCrawlerService:
    """Service for crawling product pages using Playwright"""

    def __init__(self):
        """Initialize the web crawler service"""
        self.browser = None
        self.browser_context = None
        self.logger = logging.getLogger(__name__)
        self.data_enricher = ProductDataEnricherService()

        # Default extraction config (fallback if file cannot be loaded)
        self.extraction_config = {
            "fields": {
                "title": {
                    "enabled": True,
                    "selectors": "h1, .product-title, .title"
                },
                "brand": {
                    "enabled": True,
                    "selectors": ".brand, .manufacturer, .vendor"
                },
                "price": {
                    "enabled": True,
                    "selectors": ".price, .product-price, .offer-price"
                },
                "description": {
                    "enabled": True,
                    "selectors": ".description, .product-description, [id*=description]"
                },
                "part_number": {
                    "enabled": True,
                    "selectors": "[id*='part-number'], [id*='model-number'], [id*='sku'], .part-number, .model-number, .sku, .mpn, [data-test*='sku'], [data-test*='part'], li:has-text('Part #'), li:has-text('SKU'), li:has-text('Item #'), li:has-text('Model'), tr:has-text('Part Number')"
                }
            }
        }

        # Load config from file
        self._load_extraction_config()

    def _load_extraction_config(self):
        """Load extraction configuration from environment variables"""
        # Primary config path from .env file
        default_path = os.getenv("CRAWLER_CONFIG_PATH", "")

        # Allow override with EXTRACTION_CONFIG_PATH for backward compatibility
        config_path = os.getenv("EXTRACTION_CONFIG_PATH", default_path)

        try:
            if config_path and os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    custom_config = json.load(f)
                    self.extraction_config = custom_config
                    self.logger.info(f"Loaded extraction config from {config_path}")
            else:
                self.logger.warning(f"Config file not found at {config_path}, using default configuration")
        except Exception as e:
            self.logger.error(f"Failed to load extraction config: {str(e)}")
            self.logger.info("Using default configuration")

    async def initialize(self):
        """Initialize browser for crawling"""
        if not self.browser:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(headless=True)
            self.browser_context = await self.browser.new_context()


    async def get_detailed_product_info(self, products):
        """Scrape detailed information from each product URL with batching for API calls"""
        enriched_products = []
        batch_size = 3  # Process in small batches to avoid rate limits

        # Process products in batches
        for i in range(0, len(products), batch_size):
            batch = products[i:i + batch_size]
            batch_results = []

            for product in batch:
                try:
                    self.logger.info(f"Scraping details for: {product.product_url}")

                    # Create a new page without using async with
                    page = await self.browser_context.new_page()
                    try:
                        # Navigate to product URL with timeout
                        await page.goto(product.product_url, wait_until="domcontentloaded", timeout=20000)

                        # Get the HTML content for AI extraction
                        html_content = await page.content()

                        # Check if this is a category page
                        url = page.url
                        is_category_page = any(pattern in url for pattern in ['/c/', '/category/', '/collection/', '/products/'])

                        # Set a default part_number value
                        part_number = None

                        # Try to extract part number using CSS selectors, but only if it's not a category page
                        if not is_category_page and self.extraction_config["fields"]["part_number"]["enabled"]:
                            selectors = self.extraction_config["fields"]["part_number"]["selectors"]
                            for selector in selectors.split(', '):
                                part_number = await self._extract_text(page, selector)
                                if part_number:
                                    part_number = self._clean_part_number(part_number)
                                    if part_number:
                                        self.logger.info(f"Found part number: {part_number}")
                                        break
                        elif is_category_page:
                            self.logger.info(f"Skipping part number extraction for category page: {url}")
                            part_number = "Not found"  # Explicitly set "Not found" for category pages

                        # Always set a value for part_number if none was found
                        if not part_number:
                            part_number = "Not found"  # Set "Not found" if no part number was extracted

                        # Update the product with the extracted part number
                        enriched_product = product_search_pb2.ProductData(
                            product_id=part_number,
                            product_name=product.product_name,
                            brand=product.brand or "Not found",
                            price=product.price or "Not found",
                            description=product.description,
                            product_url=product.product_url
                        )

                        # Use AI to further enrich the product data if we have HTML content
                        if html_content:
                            enriched_product = await self.data_enricher.enrich_product_data(enriched_product, html_content)

                        # Add the result to batch_results
                        batch_results.append(enriched_product)

                    except Exception as e:
                        self.logger.error(f"Error scraping page content: {str(e)}")
                        batch_results.append(product_search_pb2.ProductData(
                            product_id="Not found",
                            product_name=product.product_name,
                            brand=product.brand or "Not found",
                            price=product.price or "Not found",
                            description=product.description or "Error occurred during extraction",
                            product_url=product.product_url
                        ))
                    finally:
                        # Make sure to close the page
                        await page.close()

                except Exception as e:
                    self.logger.error(f"Failed to create page for {product.product_url}: {str(e)}")
                    batch_results.append(product_search_pb2.ProductData(
                        product_id="Not found",
                        product_name=product.product_name,
                        brand=product.brand or "Not found",
                        price=product.price or "Not found",
                        description=product.description or "Error occurred during extraction",
                        product_url=product.product_url
                    ))

            # Add all results from this batch
            enriched_products.extend(batch_results)

            # Add delay between batches if not the last batch
            if i + batch_size < len(products):
                await asyncio.sleep(2)  # 2-second delay between batches

        return enriched_products

    def _clean_part_number(self, text):
        """Clean up part number text and validate it"""
        if not text:
            return ""

        # Log the input text
        self.logger.info(f"Cleaning part number text: '{text}'")

        # Continue with existing cleaning logic
        labels = ["Part #:", "Part #", "Part Number:", "Part Number",
                  "SKU:", "SKU", "Item #:", "Item #", "Model:", "Model",
                  "MPN:", "MPN", "Article #:", "Article #"]

        result = text.strip()
        for label in labels:
            if result.startswith(label):
                result = result[len(label):].strip()
                break

        # Only return if it looks like a valid part number
        if len(result) > 30 or len(result) < 3:
            self.logger.info(f"Rejected part number of invalid length: {len(result)}")
            return ""

        return result

    async def _extract_text(self, page, selector):
        """Extract text from the first element matching the selector"""

        url = page.url

        # Skip part number extraction for category/listing pages
        if any(pattern in url for pattern in ['/c/', '/category/', '/collection/', '/products/']):
            self.logger.info(f"Skipping part number extraction for category page: {url}")
            return None

        try:
            # For part numbers, try multiple approaches
            is_part_number = "part-number" in selector or "sku" in selector

            if is_part_number:
                self.logger.info(f"Attempting to extract part number using selector: {selector}")

                # Dictionary to store found part numbers with confidence scores
                part_numbers = {}

                # Get page title for context
                page_title = await page.title()
                self.logger.info(f"Page title: {page_title}")

                # Split into standard CSS selectors and Playwright-specific ones
                standard_selectors = []
                playwright_selectors = []

                for sel in selector.split(', '):
                    if ':has-text(' in sel:
                        playwright_selectors.append(sel)
                    else:
                        standard_selectors.append(sel)

                # Use standard selectors with JS evaluation
                if standard_selectors:
                    try:
                        std_selector_str = ', '.join(standard_selectors)
                        element_count = await page.evaluate(f'''
                            () => {{
                                try {{
                                    return document.querySelectorAll(`{std_selector_str}`).length;
                                }} catch (e) {{
                                    return "Error: " + e.message;
                                }}
                            }}
                        ''')
                        self.logger.info(f"Found {element_count} elements matching standard selectors")
                    except Exception as e:
                        self.logger.warning(f"Error counting standard elements: {str(e)}")

                # Try each individual selector with Playwright's API
                element = await page.query_selector(selector)
                if element:
                    text = await element.text_content()
                    if text:
                        text = text.strip()
                        if is_part_number:
                            self.logger.info(f"Found potential part number with selector: '{text}'")
                            part_numbers[text] = 80  # High confidence for direct selector match
                            return text

                # Try structured data - highest confidence
                try:
                    sku = await page.evaluate('''() => {
                        const jsonLd = document.querySelector('script[type="application/ld+json"]');
                        if (jsonLd) {
                            try {
                                const data = JSON.parse(jsonLd.textContent);
                                if (data.sku) return data.sku;
                                if (data.mpn) return data.mpn;
                                if (data.productID) return data.productID;
                            } catch(e) {}
                        }
                        return null;
                    }''')
                    if sku:
                        self.logger.info(f"Found part number in structured data: '{sku}'")
                        part_numbers[sku] = 95  # Very high confidence
                        return sku
                except Exception as e:
                    self.logger.debug(f"Error extracting structured data: {str(e)}")

                # Try meta tags - high confidence
                try:
                    meta_sku = await page.evaluate('''() => {
                        const meta = document.querySelector('meta[property="product:sku"], meta[name="product:sku"]');
                        return meta ? meta.getAttribute('content') : null;
                    }''')
                    if meta_sku:
                        self.logger.info(f"Found part number in meta tag: '{meta_sku}'")
                        part_numbers[meta_sku] = 90  # High confidence
                        return meta_sku
                except Exception as e:
                    self.logger.debug(f"Error extracting from meta tags: {str(e)}")

                # Try URL path extraction - also high confidence
                try:
                    url_part_number = await page.evaluate('''() => {
                        // Extract from URL
                        const url = window.location.href;

                        // Look for product ID patterns in URL
                        const patterns = [
                            /\\/p[\\/-](\\d{6,10})\\b/i,  // Product IDs after /p/ (like 30389175)
                            /\\/(\\d{5,10})-/,            // Numeric IDs followed by hyphen
                            /\\/([A-Z0-9]{2,6}[\\-\\/][0-9]{1,4})\\//i  // Like 960A/10
                        ];

                        for (const pattern of patterns) {
                            const match = url.match(pattern);
                            if (match && match[1]) return match[1];
                        }
                        return null;
                    }''')

                    if url_part_number:
                        self.logger.info(f"Found part number in URL: '{url_part_number}'")
                        part_numbers[url_part_number] = 85  # High confidence for URL-based IDs
                        return url_part_number
                except Exception as e:
                    self.logger.debug(f"Error extracting from URL: {str(e)}")

                # Pattern matching - lower confidence
                try:
                    part_number_patterns = await page.evaluate('''() => {
                        // Get all text nodes
                        const textNodes = [];
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                        let node;
                        while(node = walker.nextNode()) {
                            if(node.textContent.trim()) textNodes.push(node.textContent.trim());
                        }

                        // Common part number patterns
                        const patterns = [
                            /(?:SKU|Part|Item|Model|Cat|Catalog)[#:\\s]+([A-Z0-9\\-\\/]{4,15})\\b/i,
                            /\\b([A-Z]{2,4}[0-9]{3,10})\\b(?!\\s+[a-z])/,
                            /\\b([0-9]{5,10}[A-Z]{1,3})\\b/
                        ];

                        for(const nodeText of textNodes) {
                            for(const pattern of patterns) {
                                const match = nodeText.match(pattern);
                                if(match && match[1]) return match[1];
                            }
                        }
                        return null;
                    }''')

                    if part_number_patterns:
                        # Filter out common words
                        common_words = ["requires", "includes", "contains", "features", "warranty",
                                        "optional", "recommended", "available", "specifications",
                                        "details", "standard", "package", "content", "product", "online"]

                        if part_number_patterns.lower() in common_words:
                            self.logger.info(f"Filtered out common word: '{part_number_patterns}'")
                        else:
                            self.logger.info(f"Found part number via text pattern matching: '{part_number_patterns}'")
                            part_numbers[part_number_patterns] = 60  # Lower confidence for pattern matching
                            return part_number_patterns
                except Exception as e:
                    self.logger.debug(f"Error with pattern matching: {str(e)}")

                # Attribute-based extraction
                try:
                    attr_part_number = await page.evaluate('''() => {
                        const attrSelectors = [
                            '[data-sku]', '[data-product-id]', '[data-item-number]',
                            '[data-part-number]', '[data-model]', '[itemprop="sku"]',
                            '[itemprop="productID"]', '[id*="product-id"]'
                        ];

                        for(const selector of attrSelectors) {
                            const el = document.querySelector(selector);
                            if(el) {
                                for(const attr of ['data-sku', 'data-product-id', 'data-item-number',
                                                   'data-part-number', 'data-model', 'content']) {
                                    if(el.hasAttribute(attr)) return el.getAttribute(attr);
                                }
                                return el.textContent.trim();
                            }
                        }
                        return null;
                    }''')

                    if attr_part_number:
                        self.logger.info(f"Found part number in attributes: '{attr_part_number}'")
                        part_numbers[attr_part_number] = 75  # Good confidence for attribute-based
                        return attr_part_number
                except Exception as e:
                    self.logger.debug(f"Error extracting from attributes: {str(e)}")

                # If we found multiple part numbers, return the one with highest confidence
                if part_numbers:
                    best_part_number = max(part_numbers.items(), key=lambda x: x[1])[0]
                    self.logger.info(f"Selected best part number: {best_part_number} with confidence {part_numbers[best_part_number]}")
                    return best_part_number

                self.logger.info("No part number found using any method")
        except Exception as e:
            self.logger.debug(f"Extraction error for selector '{selector}': {str(e)}")
        return None