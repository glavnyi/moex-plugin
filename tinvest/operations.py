"""Operation history — spec §5.2/5.7."""

from __future__ import annotations

from datetime import datetime, timezone

from .client import TInvestClient, quotation_to_float

_STATES = {"OPERATION_STATE_EXECUTED", "OPERATION_STATE_CANCELED",
           "OPERATION_STATE_PROGRESS"}


def list_operations(
    client: TInvestClient,
    account_id: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    status: str = "OPERATION_STATE_EXECUTED",
) -> list[dict]:
    payload = {
        "accountId": account_id,
        "from": f"{date_from}T00:00:00" if date_from else None,
        "to": f"{date_to}T23:59:59" if date_to else None,
        "state": status,
        "limit": int(limit),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    if client.sandbox:
        data = client.call("SandboxService", "GetSandboxOperations", payload)
    else:
        data = client.call("OperationsService", "GetOperations", payload)
    out = []
    for op in data.get("operations") or []:
        payment = quotation_to_float(op.get("payment"))
        out.append({
            # Sandbox actually returns the id under "id" (live-verified);
            # "operationId" kept as a fallback for schema drift.
            "operation_id": op.get("id") or op.get("operationId") or "",
            "date": str(op.get("date") or "")[:19],
            "type": op.get("type") or "",
            "status": op.get("state") or "",
            "description": op.get("description") or "",
            "figi": op.get("figi") or "",
            "instrument_type": op.get("instrumentType") or "",
            "payment": payment,
            "currency": (op.get("payment") or {}).get("currency") or "",
            "quantity": quotation_to_float(op.get("quantity")),
            "quantity_lots": quotation_to_float(op.get("quantityLots")),
            "source": "t-invest-api",
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def operations_summary(client: TInvestClient, account_id: str,
                       *, date_from: str | None = None) -> dict:
    ops = list_operations(client, account_id, date_from=date_from, limit=300)
    realized = sum(o["payment"] or 0.0 for o in ops
                   if o["status"] == "OPERATION_STATE_EXECUTED")
    return {
        "account_id": account_id,
        "count": len(ops),
        "net_payment": round(realized, 4),
        "by_type": {
            t: sum(1 for o in ops if o["type"] == t)
            for t in sorted({o["type"] for o in ops})
        },
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "operations": ops,
    }
