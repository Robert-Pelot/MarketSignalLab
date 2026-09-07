# Security policy

## Credentials

MarketSignalLab's offline demo and analysis do not require credentials. Optional
Alpaca historical-data updates require API credentials, but the application does
not contain order-placement endpoints or place trades. Never add API keys, access
tokens, passwords, account identifiers, or `.env` files to this repository.

For archive updates, the application reads `APCA_API_KEY_ID` and
`APCA_API_SECRET_KEY` from the process environment. It sends them only as the
authentication headers documented by Alpaca; they are never included in request
URLs, database tables, logs, reports, or configuration files.

If a credential is committed accidentally:

1. Revoke or regenerate it with the service provider immediately.
2. Remove it from the repository and its Git history.
3. Review the account for unexpected activity.
4. Replace it with an environment variable or an appropriate secret manager.

Deleting a credential from the newest commit is not sufficient because earlier
commits may still contain it.

## Reporting a problem

Please report a security concern privately to the repository owner rather than
opening a public issue containing sensitive information.
