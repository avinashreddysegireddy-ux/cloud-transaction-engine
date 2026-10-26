# Cloud Transaction Engine (base)

Simple REST API for accounts and money movement.

## Run
    pip install -r requirements.txt
    uvicorn app:app --reload        # docs at http://localhost:8000/docs

## Docker
    docker build -t cte . && docker run -p 8000:8000 -v cte-data:/data cte

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| POST | /accounts | create account |
| GET | /accounts/{id} | balance |
| GET | /accounts/{id}/transactions | history |
| POST | /transactions/deposit | add funds |
| POST | /transactions/withdraw | remove funds |
| POST | /transactions/transfer | move funds atomically |

Send an `Idempotency-Key` header on money endpoints; retries with the same key return the original result instead of repeating it.

## Test
    pip install pytest && pytest -q

## Next steps
Auth (JWT/API keys), PostgreSQL, double-entry ledger, rate limiting, message queue for async settlement.
