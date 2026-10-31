"""Make, list and revoke device API keys.

Run it from the backend folder, so it finds the same database as the server
(or set BEACON_DB to the same path):

    python manage_keys.py create alice_esp     # make a key for a device
    python manage_keys.py list                 # see all keys (never shows the keys themselves)
    python manage_keys.py revoke 3             # stop key number 3 from working

With Docker:

    docker compose exec backend python manage_keys.py create alice_esp
"""

import argparse
import sys

import auth
import db


def create(args: argparse.Namespace) -> int:
    try:
        issued = auth.issue_key(args.device_id)
    except ValueError as err:
        print(f"Error: {err}")
        return 1
    print(f"Created key #{issued['id']} for device '{issued['device_id']}'.")
    print("Copy it now. It is stored only as a hash, so it cannot be shown again:\n")
    print(f"    {issued['key']}\n")
    print(f"The device sends it with every event, in this header:  {auth.KEY_HEADER}: <key>")
    print("Keep it out of git (put it in a git-ignored file, never in committed code).")
    return 0


def list_all(args: argparse.Namespace) -> int:
    keys = db.list_keys()
    if not keys:
        print("No keys yet. Make one with:  python manage_keys.py create <device_id>")
        return 0
    print(f"{'ID':>4}  {'DEVICE':<24} {'KEY STARTS':<10} {'CREATED':<21} STATUS")
    for k in keys:
        status = f"revoked {k['revoked_at'][:19]}" if k["revoked_at"] else "active"
        print(f"{k['id']:>4}  {k['device_id']:<24} {k['key_prefix']:<10} {k['created_at'][:19]:<21} {status}")
    return 0


def revoke(args: argparse.Namespace) -> int:
    key = db.revoke_key(args.key_id)
    if key is None:
        print(f"Error: there is no key with id {args.key_id}. Run 'list' to see the ids.")
        return 1
    print(f"Revoked key #{key['id']} for device '{key['device_id']}'. It no longer works.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage device API keys for beacon-telemetry.")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("create", help="make a new key for a device")
    p.add_argument("device_id", help="the device this key is for, for example alice_esp")
    p.set_defaults(run=create)

    p = commands.add_parser("list", help="list all keys")
    p.set_defaults(run=list_all)

    p = commands.add_parser("revoke", help="stop a key from working")
    p.add_argument("key_id", type=int, help="the id shown by 'list'")
    p.set_defaults(run=revoke)

    args = parser.parse_args()
    db.init_db()   # so it also works on a brand-new database
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
