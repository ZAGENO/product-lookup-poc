# Product Search Microservice (gRPC)

A gRPC microservice that searches for products using Google Programmable Search Engine (PSE) and extracts structured data using Amazon Bedrock's Titan LLM.

## Features

- Query Google PSE with search terms
- Crawl resulting webpages to extract content
- Use LLM locally via Ollama (Mistral) to extract structured product data
- Return clean, structured product data via gRPC

## Setup and Deployment

### Environment Variables

Create a `.env` file in the project root with:

- `GOOGLE_API_KEY`
- `GOOGLE_PSE_ID`
- `GRPC_PORT`
- `MAX_WORKERS`
- `CRAWLER_CONFIG_PATH`
- `OLLAMA_HOST`
- `OLLAMA_MODEL`
- `MAX_SEARCH_RESULTS`
