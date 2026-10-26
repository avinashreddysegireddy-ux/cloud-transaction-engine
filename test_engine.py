import os, tempfile
os.environ["CTE_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
from fastapi.testclient import TestClient
from app import app

def test_flow():
    with TestClient(app) as c:
        a = c.post("/accounts", json={"owner": "Alice", "initial_balance": 100}).json()
        b = c.post("/accounts", json={"owner": "Bob"}).json()

        # transfer + idempotent retry
        h = {"Idempotency-Key": "k1"}
        body = {"from_account": a["id"], "to_account": b["id"], "amount": 40}
        r1 = c.post("/transactions/transfer", json=body, headers=h)
        r2 = c.post("/transactions/transfer", json=body, headers=h)
        assert r1.status_code == 201 and r1.json()["id"] == r2.json()["id"]
        assert c.get(f"/accounts/{a['id']}").json()["balance"] == 60
        assert c.get(f"/accounts/{b['id']}").json()["balance"] == 40

        # overdraft rejected, balances unchanged
        r = c.post("/transactions/withdraw", json={"account_id": b["id"], "amount": 500})
        assert r.status_code == 409
        assert c.get(f"/accounts/{b['id']}").json()["balance"] == 40

        c.post("/transactions/deposit", json={"account_id": b["id"], "amount": 10.5})
        assert c.get(f"/accounts/{b['id']}").json()["balance"] == 50.5
        assert len(c.get(f"/accounts/{b['id']}/transactions").json()) == 2
