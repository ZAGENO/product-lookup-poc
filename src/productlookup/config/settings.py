import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Google Search API settings
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_PSE_ID = os.getenv("GOOGLE_PSE_ID")
# gRPC server settings
GRPC_PORT = os.getenv("GRPC_PORT", "50051")


# AWS Bedrock Configuration
# AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
# AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
# AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")




