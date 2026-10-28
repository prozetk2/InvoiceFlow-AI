"""Run local ERP handoff against an existing APPROVED document over HTTP.

Usage: python scripts/rpa_handoff_cli.py <32-hex-document-id>
Prerequisite: an operator has approved the document via the review UI/API.
"""
import argparse
import os

import httpx

from invoiceflow_ai.rpa_handoff import run_demo_worker, submit_approved_job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("document_id", help="Approved 32-digit hex document ID")
    parser.add_argument("--db", default="data/rpa_demo.sqlite3")
    parser.add_argument("--simulate", action="store_true", help="Run local demo worker (not real RPA)")
    args = parser.parse_args()
    key = os.environ.get("INVOICEFLOW_API_KEY")
    if not key:
        parser.error("INVOICEFLOW_API_KEY is required")
    url = os.environ.get("INVOICEFLOW_API_URL", "http://127.0.0.1:8000")
    with httpx.Client(base_url=url, timeout=30) as client:
        result = submit_approved_job(client=client, api_headers={"X-API-Key": key},
                                     document_id=args.document_id, db_path=args.db)
        print(result)
    if args.simulate:
        print(run_demo_worker(document_id=args.document_id, db_path=args.db))


if __name__ == "__main__":
    main()
