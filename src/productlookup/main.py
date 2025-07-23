# main.py
import os
import logging
import grpc
import time
from concurrent import futures
from productlookup.protos import product_search_pb2, product_search_pb2_grpc
from productlookup.controller.product_search_servicer import ProductSearchServicer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ProductSearchService(product_search_pb2_grpc.ProductSearchServicer):
    """gRPC service implementation for product search"""

    def __init__(self):
        self.product_search = ProductSearchServicer()

    def SearchProduct(self, request, context):
        """
        Handle SearchProduct gRPC request

        Args:
            request: SearchProductRequest containing query
            context: gRPC context

        Returns:
            SearchProductResponse with product data
        """
        try:
            query = request.query
            logger.info(f"Received search request for: {query}")

            products = self.product_search.search_product(query)

            response = product_search_pb2.SearchProductResponse()
            for product in products:
                product_data = product_search_pb2.ProductData(
                    product_id=product.get("product_id", ""),
                    product_name=product.get("product_name", ""),
                    brand=product.get("brand", ""),
                    description=product.get("description", ""),
                    price=product.get("price", ""),
                    product_url=product.get("product_url", "")
                )
                response.products.append(product_data)

            return response

        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Error processing request: {str(e)}")
            return product_search_pb2.SearchProductResponse()


def serve():
    """Start the gRPC server"""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    product_search_pb2_grpc.add_ProductSearchServicer_to_server(
        ProductSearchService(), server
    )

    port = os.getenv("GRPC_PORT", "50051")
    server.add_insecure_port(f"[::]:{port}")
    server.start()

    logger.info(f"Server started on port {port}")

    try:
        while True:
            time.sleep(86400)  # One day in seconds
    except KeyboardInterrupt:
        server.stop(0)
        logger.info("Server stopped")


if __name__ == "__main__":
    serve()