"""
CLI for Sentinel API key management.

Usage:
  python -m sentinel.auth.cli generate --name "my-agent" --scopes read,trade
  python -m sentinel.auth.cli list
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from sentinel.auth.service import APIKeyService


def cmd_generate(args: argparse.Namespace) -> None:
    service = APIKeyService()
    raw_key, hashed_key = service.generate_key()
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    client_record = {
        "client_id": args.name.lower().replace(" ", "-"),
        "name": args.name,
        "hashed_key": hashed_key,
        "scopes": scopes,
        "created_at": datetime.utcnow().isoformat(),
        "is_active": True,
        "rate_limit_per_minute": args.rate_limit,
    }
    print("\n=== New API Key ===")
    print(f"Raw key (save this — shown only once):\n  {raw_key}\n")
    print("Client record (add to SENTINEL_API_KEYS_JSON):")
    print(json.dumps(client_record, indent=2))
    print()


def cmd_list(args: argparse.Namespace) -> None:
    service = APIKeyService()
    clients = service.load_clients_from_env()
    if not clients:
        print("No clients loaded from environment.")
        return
    for _, client in clients.items():
        print(f"  {client.client_id:20s}  scopes={client.scopes}  active={client.is_active}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel API key management")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser("generate", help="Generate a new API key")
    gen_parser.add_argument("--name", required=True, help="Client name")
    gen_parser.add_argument("--scopes", default="read", help="Comma-separated scopes")
    gen_parser.add_argument("--rate-limit", type=int, default=60, help="Requests per minute")

    subparsers.add_parser("list", help="List loaded clients from environment")

    args = parser.parse_args()
    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()
