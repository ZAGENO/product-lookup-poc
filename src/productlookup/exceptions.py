class ProductLookupError(Exception):
    """Base exception for all product lookup errors"""
    pass

class GoogleSearchError(ProductLookupError):
    """Error when querying Google PSE"""
    pass

class WebCrawlerError(ProductLookupError):
    """Error when crawling websites"""
    pass

class BedRockError(ProductLookupError):
    """Error when interacting with Amazon Bedrock"""
    pass

class ConfigError(ProductLookupError):
    """Error in configuration"""
    pass