import json
import os
from typing import Any

import httpx

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

NCBI_API_KEY = os.getenv("NCBI_API_KEY")


def search_pubmed(query: str, max_results: int = 10) -> dict[str, Any]:
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    try:
        response = httpx.get(ESEARCH_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        id_list = data["esearchresult"]["idlist"]
        count = data["esearchresult"]["count"]
        return {"ids": id_list, "count": count}
    except Exception as e:
        return {"error": str(e)}


def fetch_paper_details(pmids: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "abstract",
        "retmode": "text",
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    try:
        response = httpx.get(EFETCH_URL, params=params, timeout=30)
        response.raise_for_status()
        return {"abstracts": response.text}
    except Exception as e:
        return {"error": str(e)}


def dispatch_tool(name: str, inputs: dict) -> str:
    if name == "search_pubmed":
        result = search_pubmed(
            query=inputs["query"],
            max_results=inputs.get("max_results", 10),
        )
    elif name == "fetch_paper_details":
        result = fetch_paper_details(pmids=inputs["pmids"])
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)


TOOL_SCHEMAS = [
    {
        "name": "search_pubmed",
        "description": (
            "Search PubMed for peer-reviewed medical literature. "
            "Returns a list of PubMed IDs (PMIDs) matching the query. "
            "Use specific medical terminology for best results. "
            "Run multiple searches with different query terms to find the most relevant studies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Medical search query using PubMed syntax or plain terms.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Default is 10.",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_paper_details",
        "description": (
            "Fetch abstracts and metadata for a list of PubMed papers by their PMIDs. "
            "Returns structured text including title, authors, journal, year, and abstract. "
            "Fetch the most promising papers from search results before synthesizing findings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pmids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of PubMed IDs (PMIDs) to fetch details for. Limit to 5-8 at a time.",
                }
            },
            "required": ["pmids"],
        },
    },
]
