# services/product_data_enricher.py
import json
import logging
import os
import asyncio
import aiohttp
from productlookup.protos import product_search_pb2


class ProductDataEnricherService:
    """Service for enriching product data using locally hosted Ollama LLM"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.cache = {}  # Simple in-memory cache (caching with product URL as key)

        # Ollama configuration
        # Ollama configuration
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.model_name = os.getenv("OLLAMA_MODEL", "mistral:latest")

        self.logger.info(f"Initialized Ollama client with model: {self.model_name}")

    async def enrich_product_data(self, product: product_search_pb2.ProductData,
                                  html_content: str) -> product_search_pb2.ProductData:
        """Enrich product data using Ollama to extract structured information from HTML"""
        if not html_content:
            return product

        # Create a cache key (using URL as key)
        cache_key = product.product_url

        # Check if we have cached results
        if cache_key in self.cache:
            self.logger.info(f"Using csached LLM extraction for {cache_key}")
            extracted_data = self.cache[cache_key]
            return self._update_product_with_data(product, extracted_data)

        try:
            prompt = self._create_extraction_prompt(product, html_content)
            llm_response = await self._call_ollama_api(prompt)

            if not llm_response:
                return product

            # Try to extract JSON from the response
            try:
                # Look for JSON in response
                json_start = llm_response.find('{')
                json_end = llm_response.rfind('}') + 1

                if json_start >= 0 and json_end > json_start:
                    json_str = llm_response[json_start:json_end]
                    extracted_data = json.loads(json_str)

                    # Cache the results
                    self.cache[cache_key] = extracted_data
                    return self._update_product_with_data(product, extracted_data)
                else:
                    self.logger.awarning("No JSON found in LLM response")
                    return product
            except json.JSONDecodeError as e:
                self.logger.error(f"Failed to parse LLM response as JSON: {str(e)}")
                return product

        except Exception as e:
            self.logger.error(f"Error enriching product data: {str(e)}")
            return product

    def _update_product_with_data(self, product, data):
        """Update product with extracted data"""
        # Prioritize existing IDs over LLM extraction
        sku_id = product.sku_id
        part_number = product.part_number

        # Only use LLM's values if none were found through direct extraction
        if not sku_id or sku_id == "Not found":
            extracted_id = data.get("sku_id")
            if extracted_id and "not found" not in extracted_id.lower():
                sku_id = extracted_id

        if not part_number or part_number == "Not found":
            extracted_part = data.get("part_number")
            if extracted_part and "not found" not in extracted_part.lower():
                part_number = extracted_part

        return product_search_pb2.ProductData(
            sku_id=sku_id,
            part_number=part_number,
            product_name=data.get("product_name", "") or product.product_name,
            brand=data.get("brand", "") or product.brand,
            price=data.get("price", "") or product.price,
            description=data.get("description", "") or product.description,
            product_url=product.product_url
        )

    async def _call_ollama_api(self, prompt: str) -> str:
        """Call Ollama API with retry logic"""
        max_retries = 3
        base_delay = 2  # seconds

        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.ollama_host}/api/generate"
                    payload = {
                        "model": self.model_name,
                        "prompt": prompt,
                        "stream": False,
                        "temperature": 0.2
                    }

                    async with session.post(url, json=payload) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(f"Ollama API error: {response.status} - {error_text}")
                            raise Exception(f"API error: {response.status}")

                        result = await response.json()
                        return result.get("response", "")

            except Exception as e:
                self.logger.error(f"Error calling Ollama API: {str(e)}")
                if attempt < max_retries - 1:
                    wait_time = base_delay * (2 ** attempt)
                    self.logger.info(f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    return ""

        return ""  # If we've exhausted all retries

    def _create_extraction_prompt(self, product, html_content):
        """Create a prompt for extracting structured data from HTML content"""
        # Truncate HTML content to avoid token limits
        truncated_html = html_content[:4000] if len(html_content) > 4000 else html_content

        prompt = f"""
        Extract structured product information from the following HTML content.
        The product is likely: {product.product_name}

        MOST IMPORTANT: Find BOTH the SKU ID and part number:

        - SKU ID: Typically labeled as SKU, Item #, or Product Code
          Look near "Product Information" or checkout sections
          Check for data attributes like data-sku or data-product-id

        - Part Number: Typically labeled as Part #, Model #, or Catalog Number
          Often found in specifications or technical details
          For medical/lab supplies, often formatted as XXX-YYYY-ZZ

        Note that SKU ID and part number might be the same in some cases, but extract both when available.
        If you cannot find a specific field, respond with "Not found" for that field.

        Return ONLY a JSON object with these fields:
        - sku_id: The SKU or item number
        - part_number: The part or model number
        - product_name: The full product name
        - brand: The brand or manufacturer
        - price: The price with currency symbol
        - description: A brief description (max 200 chars)

        HTML Content:
        {truncated_html}

        Respond with valid JSON only. No introduction or explanation.
        """

        return prompt