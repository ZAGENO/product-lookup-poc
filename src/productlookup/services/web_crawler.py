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

class WebCrawlerService:
    """Service for crawling product pages using Playwright"""

    def __init__(self):
        """Initialize the web crawler service"""
        self.browser = None
        self.browser_context = None
        self.logger = logging.getLogger(__name__)
        self.data_enricher = ProductDataEnricherService()

        # Load config from file
        self._load_extraction_config()

    def _load_extraction_config(self):
        """Load extraction configuration from environment variables"""
        config_path = os.getenv("CRAWLER_CONFIG_PATH")

        if not config_path:
            raise ProductLookupError("CRAWLER_CONFIG_PATH environment variable is not set")

        if not os.path.exists(config_path):
            raise ProductLookupError(f"Extraction config file not found at: {config_path}")

        try:
            with open(config_path, 'r') as f:
                self.extraction_config = json.load(f)
                self.logger.info(f"Loaded extraction config from {config_path}")
        except Exception as e:
            raise ProductLookupError(f"Failed to load extraction config: {str(e)}")

    async def initialize(self):
        """Initialize browser for crawling"""
        if not self.browser:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(headless=True)
            self.browser_context = await self.browser.new_context()


    # In web_crawler.py, modify get_detailed_product_info method
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

                        # Extract both sku_id and part_number separately
                        sku_id = None
                        part_number = None

                        # Extract SKU ID first
                        if not is_category_page and self.extraction_config["fields"]["sku_id"]["enabled"]:
                            selectors = self.extraction_config["fields"]["sku_id"]["selectors"]
                            for selector in selectors.split(', '):
                                sku_id = await self._extract_text(page, selector, field_type="sku_id")
                                if sku_id:
                                    sku_id = self._clean_identifier(sku_id)
                                    if sku_id:
                                        self.logger.info(f"Found SKU ID: {sku_id}")
                                        break

                        # Extract part number separately
                        if not is_category_page and self.extraction_config["fields"]["part_number"]["enabled"]:
                            selectors = self.extraction_config["fields"]["part_number"]["selectors"]
                            for selector in selectors.split(', '):
                                part_number = await self._extract_text(page, selector, field_type="part_number")
                                if part_number:
                                    part_number = self._clean_identifier(part_number)
                                    if part_number:
                                        self.logger.info(f"Found part number: {part_number}")
                                        break

                        # Set default values if not found
                        if not sku_id:
                            sku_id = "Not found"
                        if not part_number:
                            part_number = "Not found"

                        # Update the product with both extracted values
                        enriched_product = product_search_pb2.ProductData(
                            sku_id=sku_id,
                            part_number=part_number,
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
                            sku_id="Not found",
                            part_number="Not found",
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
                        sku_id="Not found",
                        part_number="Not found",
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

    def _clean_identifier(self, text):
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

    async def _extract_text(self, page, selector, field_type="part_number"):
        """Extract text from the first element matching the selector

        Args:
            page: Playwright page object
            selector: CSS selector to find elements
            field_type: Type of field to extract ("sku_id" or "part_number")
        """
        url = page.url

        # Skip extraction for category/listing pages
        if any(pattern in url for pattern in ['/c/', '/category/', '/collection/', '/products/']):
            self.logger.info(f"Skipping {field_type} extraction for category page: {url}")
            return None

        try:
            # Dictionary to store found values with confidence scores
            found_values = {}

            # Get page title for context
            page_title = await page.title()
            self.logger.info(f"Attempting to extract {field_type} using selector: {selector}")
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
                    self.logger.info(f"Found {element_count} elements matching standard selectors for {field_type}")
                except Exception as e:
                    self.logger.warning(f"Error counting standard elements for {field_type}: {str(e)}")

            # Try each individual selector with Playwright's API
            element = await page.query_selector(selector)
            if element:
                text = await element.text_content()
                if text:
                    text = text.strip()
                    self.logger.info(f"Found potential {field_type} with selector: '{text}'")
                    found_values[text] = 80  # High confidence for direct selector match
                    return text

            # Try structured data - highest confidence
            try:
                # Adjust JSON-LD search based on field type
                field_keys = ["sku", "productID"] if field_type == "sku_id" else ["mpn", "model"]

                structured_data_js = f'''() => {{
                    const jsonLd = document.querySelector('script[type="application/ld+json"]');
                    if (jsonLd) {{
                        try {{
                            const data = JSON.parse(jsonLd.textContent);
                            if (data.{field_keys[0]}) return data.{field_keys[0]};
                            if (data.{field_keys[1]}) return data.{field_keys[1]};
                        }} catch(e) {{}}
                    }}
                    return null;
                }}'''

                value = await page.evaluate(structured_data_js)
                if value:
                    self.logger.info(f"Found {field_type} in structured data: '{value}'")
                    found_values[value] = 95  # Very high confidence
                    return value
            except Exception as e:
                self.logger.debug(f"Error extracting {field_type} from structured data: {str(e)}")

            # Try meta tags - high confidence
            try:
                meta_property = "product:sku" if field_type == "sku_id" else "product:mpn"
                meta_js = f'''() => {{
                    const meta = document.querySelector('meta[property="{meta_property}"], meta[name="{meta_property}"]');
                    return meta ? meta.getAttribute('content') : null;
                }}'''

                meta_value = await page.evaluate(meta_js)
                if meta_value:
                    self.logger.info(f"Found {field_type} in meta tag: '{meta_value}'")
                    found_values[meta_value] = 90  # High confidence
                    return meta_value
            except Exception as e:
                self.logger.debug(f"Error extracting {field_type} from meta tags: {str(e)}")

            # URL path extraction - different patterns for SKU vs Part Number
            try:
                url_patterns_js = '''() => {
                    // Extract from URL
                    const url = window.location.href;

                    // Look for different patterns based on field type
                    const patterns = '%s' === 'sku_id' ? 
                        [
                            /\\/sku[\\/-](\\w{5,12})\\b/i,     // SKU in URL path
                            /\\/(S-\\d{5,10})\\//,             // S-prefixed SKUs 
                            /item\\/(\\w{5,12})\\b/i           // item/SKU pattern
                        ] : [
                            /\\/p[\\/-](\\d{6,10})\\b/i,       // Product IDs after /p/ (like 30389175)
                            /\\/(\\d{5,10})-/,                 // Numeric IDs followed by hyphen
                            /\\/([A-Z0-9]{2,6}[\\-\\/][0-9]{1,4})\\//i  // Like 960A/10
                        ];

                    for (const pattern of patterns) {
                        const match = url.match(pattern);
                        if (match && match[1]) return match[1];
                    }
                    return null;
                }'''.replace('%s', field_type)

                url_value = await page.evaluate(url_patterns_js)
                if url_value:
                    self.logger.info(f"Found {field_type} in URL: '{url_value}'")
                    found_values[url_value] = 85  # High confidence for URL-based IDs
                    return url_value
            except Exception as e:
                self.logger.debug(f"Error extracting {field_type} from URL: {str(e)}")

            # Pattern matching - different patterns for SKU vs Part Number
            try:
                # Different regex patterns based on field type
                patterns_js = '''() => {
                    // Get all text nodes
                    const textNodes = [];
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let node;
                    while(node = walker.nextNode()) {
                        if(node.textContent.trim()) textNodes.push(node.textContent.trim());
                    }

                    // Different patterns based on field type
                    const patterns = '%s' === 'sku_id' ? 
                        [
                            /(?:SKU|Item)[#:\\s]+([A-Z0-9\\-\\/]{4,15})\\b/i,
                            /\\bItem\\s*(?:Code|Number)?:\\s*([A-Z0-9\\-\\/]{4,15})\\b/i,
                            /\\bSKU\\s*(?:Code|Number)?:\\s*([A-Z0-9\\-\\/]{4,15})\\b/i
                        ] : [
                            /(?:Part|Model|Cat|Catalog)[#:\\s]+([A-Z0-9\\-\\/]{4,15})\\b/i,
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
                }'''.replace('%s', field_type)

                pattern_value = await page.evaluate(patterns_js)

                if pattern_value:
                    # Filter out common words
                    common_words = ["requires", "includes", "contains", "features", "warranty",
                                    "optional", "recommended", "available", "specifications",
                                    "details", "standard", "package", "content", "product", "online"]

                    if pattern_value.lower() in common_words:
                        self.logger.info(f"Filtered out common word: '{pattern_value}'")
                    else:
                        self.logger.info(f"Found {field_type} via text pattern matching: '{pattern_value}'")
                        found_values[pattern_value] = 60  # Lower confidence for pattern matching
                        return pattern_value
            except Exception as e:
                self.logger.debug(f"Error with pattern matching for {field_type}: {str(e)}")

            # Attribute-based extraction - different attributes for SKU vs Part Number
            try:
                attr_js = '''() => {
                    const attrSelectors = '%s' === 'sku_id' ? 
                        [
                            '[data-sku]', '[data-item-number]', '[itemprop="sku"]',
                            '[id*="sku"]', '[class*="sku"]'
                        ] : [
                            '[data-product-id]', '[data-part-number]', '[data-model]',
                            '[itemprop="productID"]', '[id*="product-id"]', '[id*="part-number"]'
                        ];

                    const attrNames = '%s' === 'sku_id' ? 
                        ['data-sku', 'data-item-number', 'content'] : 
                        ['data-product-id', 'data-part-number', 'data-model', 'content'];

                    for(const selector of attrSelectors) {
                        const el = document.querySelector(selector);
                        if(el) {
                            for(const attr of attrNames) {
                                if(el.hasAttribute(attr)) return el.getAttribute(attr);
                            }
                            return el.textContent.trim();
                        }
                    }
                    return null;
                }'''.replace('%s', field_type).replace('%s', field_type)

                attr_value = await page.evaluate(attr_js)

                if attr_value:
                    self.logger.info(f"Found {field_type} in attributes: '{attr_value}'")
                    found_values[attr_value] = 75  # Good confidence for attribute-based
                    return attr_value
            except Exception as e:
                self.logger.debug(f"Error extracting {field_type} from attributes: {str(e)}")

            # If we found multiple values, return the one with highest confidence
            if found_values:
                best_value = max(found_values.items(), key=lambda x: x[1])[0]
                self.logger.info(f"Selected best {field_type}: {best_value} with confidence {found_values[best_value]}")
                return best_value

            self.logger.info(f"No {field_type} found using any method")
        except Exception as e:
            self.logger.debug(f"Extraction error for {field_type} with selector '{selector}': {str(e)}")
        return None