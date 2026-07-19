"""
ERC-8183 agentic-commerce provider — the agent SELLS its CMC intelligence to other agents.

The trading agent already BUYS data with x402 (consumer side). This module makes it a PROVIDER:
it offers its live Market Regime Report (`agent/regime_report.build_report`) as a paid ERC-8183
service. Another agent runs `create_job → fund → settle`; this agent's `on_job` callback computes
the report and returns it; the SDK hashes it, uploads the manifest via a `StorageProvider`, and
submits it on-chain. One agent that pays for inputs (x402) and monetises outputs (ERC-8183) is the
"most inventive integration" thesis. Built gaslessly on bsc-testnet (the SDK's public MegaFuel
paymaster); the same code flips to bsc-mainnet (keyed paymaster, real payment token).

SECURITY MODEL
  - Signing wallet = the local IDENTITY keystore that mints/heartbeats (`AGENT_IDENTITY_ADDRESS`),
    DISTINCT by design from the TWAK trading wallet (`AGENT_TRADING_ADDRESS`). It is derived from
    `AGENT_WALLET_PASSWORD` (+ optional `AGENT_PRIVATE_KEY`) LOCALLY; the key never leaves the
    process and is never logged. `available()` requires the password, so this never runs on the
    read-only (zero-secret) dashboard deploy.
  - The deliverable is PUBLIC market analysis only (regime score, ranking, rationale, CMC
    provenance) — it never embeds a key, password, or internal path. The jobs journal likewise
    records only public job metadata (ids, hashes, amounts, public addresses).
  - The ERC-8183 client signs the job-lifecycle CONTRACT calls only (create/fund/submit/settle) —
    not arbitrary EIP-712 typed data (that surface stays the x402 `SigningPolicy`-guarded path).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from ictbot.settings import JOURNAL_DIR, settings

COMMERCE_JOURNAL = JOURNAL_DIR / "commerce_jobs.jsonl"


def available() -> bool:
    """True iff ERC-8183 commerce is enabled, the SDK is importable, and a wallet password is set.
    Mirrors `identity.available()`/`x402_cmc.available()` — signing needs the LOCAL password, so a
    read-only deploy (no secret) returns False and never attempts to sign."""
    if not settings.erc8183_enabled:
        return False
    try:
        import bnbagent  # noqa: F401
    except Exception:
        return False
    return bool(settings.agent_wallet_password)


def _network():
    """The ERC-8183 network. Testnet (default) uses the SDK's PUBLIC gasless MegaFuel out of the
    box (no NodeReal key needed); mainnet reuses the identity layer's KEYED paymaster route so the
    sponsored txns land on the user's NodeReal dashboard."""
    net = settings.erc8183_network
    if net in ("bsc", "bsc-mainnet"):
        from ictbot.agent.identity import _sdk_network

        return _sdk_network("bsc-mainnet")
    return "bsc-testnet"


def _wallet():
    """The agent's signing wallet — the SAME local identity keystore that mints/heartbeats.
    SECURITY: derived locally from the password (+ optional key); never logged; only built where
    signing actually happens (guard with `available()` first)."""
    from bnbagent import EVMWalletProvider

    return EVMWalletProvider(
        password=settings.agent_wallet_password,
        private_key=settings.agent_private_key or None,
        persist=True,
    )


def build_client():
    """Construct the ERC8183Client over the identity wallet on the configured network.
    Caller must guard with `available()` (this builds the signing wallet)."""
    from bnbagent import ERC8183Client

    return ERC8183Client(wallet_provider=_wallet(), network=_network())


def storage_provider():
    """Deliverable storage backend. Local (`file://`) by default for a single-host demo; IPFS
    (`ERC8183_STORAGE=ipfs`) for decentralised, publicly verifiable manifests. The deliverable is
    PUBLIC market analysis, so publishing it leaks nothing."""
    if settings.erc8183_storage == "ipfs":
        try:
            import os

            from bnbagent.storage import IPFSStorageProvider

            # Pinata uses Bearer-JWT auth. Read the JWT from the standard slot first, then the
            # common Pinata names (the operator may have added it as JWT_SECRET / PINATA_JWT).
            jwt = os.getenv("STORAGE_API_KEY") or os.getenv("PINATA_JWT") or os.getenv("JWT_SECRET")
            if jwt:
                return IPFSStorageProvider(
                    pinning_api_url=os.getenv("STORAGE_API_URL")
                    or "https://api.pinata.cloud/pinning/pinJSONToIPFS",
                    pinning_api_key=jwt,
                    gateway_url=os.getenv("STORAGE_GATEWAY_URL") or "https://gateway.pinata.cloud/ipfs/",
                )
        except Exception:
            pass
    from bnbagent.storage import LocalStorageProvider

    return LocalStorageProvider()


def journal_commerce(event: str, **fields: Any) -> None:
    """Append a commerce-lifecycle row (CREATE/FUND/SUBMIT/SETTLE) for the dashboard + telemetry.
    Best-effort; never raises. SECURITY: only public job metadata — never a secret."""
    try:
        COMMERCE_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}
        with COMMERCE_JOURNAL.open("a") as f:
            # default=str: SDK fields (tx/deliverable_hash) can be web3 HexBytes/AttributeDict — without
            # this, json.dumps raises and the except-pass below SILENTLY drops the on-chain row (the
            # dashboard would show the job unserved despite it landing). Mirrors run_allocator.journal().
            f.write(json.dumps(row, default=str) + "\n")
    except Exception:
        pass


def _job_field(job: Any, *names: str):
    """Read a field from a Job dataclass OR a dict (the server passes either)."""
    for n in names:
        v = job.get(n) if isinstance(job, dict) else getattr(job, n, None)
        if v is not None:
            return v
    return None


def on_job(job: Any) -> str:
    """ERC-8183 provider callback for a FUNDED job → returns the deliverable as a JSON string.

    The SDK hashes the returned string, uploads the manifest via the StorageProvider, and submits
    it on-chain. The deliverable is the agent's live Market Regime Report — PUBLIC market intelligence
    (no key/password/path). The buyer's free-text request (job description) is echoed into the
    report. Best-effort journaled; the report builder never raises."""
    from ictbot.agent.regime_report import build_report

    query = _job_field(job, "description", "desc")
    query = str(query)[:200] if query else None
    report = build_report(query=query)
    journal_commerce(
        "SUBMIT",
        job_id=_job_field(job, "jobId", "job_id", "id"),
        status=report.get("status"),
        regime_score=report.get("regime_score"),
        query=query,
    )
    return json.dumps(report)


def make_job_ops():
    """Build the provider's ERC8183JobOps over the identity wallet + configured network/storage.
    Caller must guard with `available()`."""
    from bnbagent.erc8183.server import ERC8183JobOps

    from ictbot.agent.identity import _identity_address

    return ERC8183JobOps(
        wallet_provider=_wallet(),
        network=_network(),
        provider_address=_identity_address(),
        storage_provider=storage_provider(),
        service_price=settings.erc8183_service_price,
        agent_url=settings.erc8183_agent_url or None,
    )


def _run_coro(coro):
    """Drive an async SDK call to completion from a SYNC caller, whether or not an event loop is
    already running: the `serve()` watcher runs one (→ run on a worker-thread loop), while the CLI
    / endpoint thread does not (→ plain asyncio.run). The SDK's `submit_result` is a coroutine."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def submit_for(job_ops, job: Any) -> dict:
    """Compute the deliverable for ONE funded job and submit it on-chain (the SDK hashes the
    content, uploads the manifest via StorageProvider, and submits). Returns the SDK result;
    journals the on-chain submission. Raising/returning False re-queues the job (watcher contract)."""
    job_id = int(_job_field(job, "jobId", "job_id", "id"))
    out = job_ops.submit_result(job_id, on_job(job))
    res = (_run_coro(out) if asyncio.iscoroutine(out) else out) or {}
    # The SDK returns the tx under `txHash` (success path) and signals failure with
    # {"success": False, "error": ...}. Only journal a REAL on-chain submission — never a phantom
    # "served" with no tx (the ledger counts SUBMITTED_ONCHAIN events).
    tx = res.get("transactionHash") or res.get("tx") or res.get("txHash")
    if res.get("success") is False or not tx:
        raise RuntimeError(f"submit_result failed: {res.get('error') or res}")
    journal_commerce(
        "SUBMITTED_ONCHAIN",
        job_id=job_id,
        tx=tx,
        deliverable_hash=res.get("deliverableHash") or res.get("hash") or res.get("deliverable"),
        deliverable_url=res.get("deliverableUrl"),
    )
    return res


async def serve(*, interval: float | None = None, stop=None) -> None:
    """Autonomous provider loop: poll FUNDED jobs and submit the Market Regime Report for each.
    Gasless on testnet. The watcher only fires on FUNDED jobs (unfunded jobs are never served)."""
    from bnbagent.erc8183.server import funded_job_watcher

    job_ops = make_job_ops()
    await funded_job_watcher(
        job_ops,
        lambda job: submit_for(job_ops, job),
        interval=30.0 if interval is None else interval,
        stop=stop,
    )


# --------------------------------------------------------------------------- #
# BUYER side — operator-local "create a job from the UI" (a DISTINCT agent that
# pays our provider, so the two-sided loop is genuinely agent-to-agent). All of
# this requires the buyer keystore password, so it NEVER runs on the read-only
# deploy (mirrors `available()`).
# --------------------------------------------------------------------------- #
def buyer_available() -> bool:
    """True iff the provider can sign AND a DISTINCT buyer keystore is configured. Gates the
    dashboard's 'create job' button (key-free boolean — safe to call on every snapshot read; the
    cloud deploy has no buyer password, so it returns False and the button stays disabled)."""
    return available() and bool(settings.erc8183_client_password)


def _buyer_client():
    """The BUYER-side ERC8183Client (a separate agent that pays our provider). Operator-local only;
    guard with `buyer_available()` (this builds the buyer signing wallet)."""
    from bnbagent import ERC8183Client, EVMWalletProvider

    kwargs: dict = {
        "password": settings.erc8183_client_password,
        "private_key": settings.erc8183_client_private_key or None,
        # Disambiguate the buyer keystore from the provider's (both default to ~/.bnbagent/wallets).
        "address": settings.erc8183_client_address or None,
        "persist": True,
    }
    if settings.erc8183_client_wallets_dir:
        kwargs["wallets_dir"] = settings.erc8183_client_wallets_dir
    wallet = EVMWalletProvider(**kwargs)
    return ERC8183Client(wallet_provider=wallet, network=_network())


def _net_name() -> str:
    """Resolved network name (`_network()` returns a NetworkConfig on mainnet, a str on testnet)."""
    net = _network()
    return getattr(net, "name", None) or str(net)


def _buyer_and_info():
    """Build the buyer client + read its public funding picture (read-only; no job, no value moved):
    network, address, the ERC-20 payment token symbol + CONTRACT ADDRESS (immutable on the kernel),
    decimals, current balance, and the per-job price. The token address is what the operator funds —
    critical on mainnet, where `U` (0xcE24…6666) is a real token, not a faucet drip."""
    buyer = _buyer_client()
    info: dict = {
        "network": _net_name(), "buyer": buyer.address,
        "price": int(settings.erc8183_service_price),
        "token": None, "token_address": None, "decimals": None, "balance": None,
    }
    try:
        info["token"] = buyer.token_symbol()
        pt = getattr(buyer, "payment_token", None)
        info["token_address"] = (pt() if callable(pt) else pt)
        info["decimals"] = buyer.token_decimals()
        info["balance"] = int(buyer.token_balance(buyer.address))
    except Exception as e:  # best-effort read; the loop's fund() will surface any hard failure
        info["error"] = str(e)[:160]
    return buyer, info


def buyer_wallet_info() -> dict:
    """Read-only buyer + payment-token picture for the operator to FUND before a job (see
    `_buyer_and_info`). Guard with `buyer_available()`; creates no job, moves no value."""
    return _buyer_and_info()[1]


def create_and_serve_job(query: str, *, amount: int | None = None, expiry_min: int = 20160) -> dict:
    """Run the FULL ERC-8183 loop end-to-end so the agent serves a REAL job: buyer create → fund →
    provider submits on-chain → buyer settle. Reuses the provider path (`make_job_ops`/`submit_for`)
    and journals each lifecycle event for the dashboard ledger. Returns a PUBLIC-metadata dict (ids,
    hashes, amounts, public addresses — never a secret). Caller must guard with `buyer_available()`.

    Network-agnostic (`ERC8183_NETWORK`): testnet is gasless via public MegaFuel; mainnet uses the
    keyed paymaster when `AGENT_USE_PAYMASTER=true`, else direct-gas (wallets need a little BNB).
    Only the escrow FUNDING moves the payment token, so the buyer must hold >= `amount` — we surface
    its address + the token CONTRACT to fund when it doesn't, never crashing mid-loop."""
    from ictbot.agent.identity import _identity_address, _lower_sdk_gas_floor

    # Direct-gas path (the keyed paymaster isn't sponsoring): drop the SDK's hardcoded 3-gwei floor
    # to ~max(2x live, 0.1 gwei). BSC runs ~0.05 gwei, so this makes each buyer/provider tx cost
    # ~0.00003 BNB instead of ~0.001 — the whole loop fits in a few cents of BNB.
    _lower_sdk_gas_floor()

    amount = int(amount if amount is not None else settings.erc8183_service_price)
    query = str(query or "Give me your current CMC regime read + momentum ranking.")[:200]

    buyer, info = _buyer_and_info()
    buyer_addr = info["buyer"]
    token, token_addr, bal = info.get("token"), info.get("token_address"), info.get("balance")
    # Funding precheck — a clear, actionable result instead of a mid-loop revert.
    if bal is not None and bal < amount:
        return {
            "ok": False, "stage": "fund-precheck", "buyer": buyer_addr, "network": info["network"],
            "token": token, "token_address": token_addr, "need": amount, "have": bal,
            "message": f"fund the buyer wallet {buyer_addr} with >= {amount} {token or 'U'} "
                       f"({token_addr}) on {info['network']} (have {bal}), then retry",
        }

    provider_addr = _identity_address()
    # expiry MUST exceed the kernel's ~7-day dispute window: the submission deadline is
    # (expiredAt - disputeWindow), so a short expiry is already "past" and submit reverts.
    expired_at = int(time.time()) + max(1, int(expiry_min)) * 60

    # 1. create the job
    job = buyer.create_job(provider=provider_addr, expired_at=expired_at, description=query)
    job_id = int(job.get("jobId") or job.get("job_id"))
    journal_commerce("CREATE", job_id=job_id, provider=provider_addr, query=query, buyer=buyer_addr)
    # 2. bind the evaluator POLICY on the Router + set the BUDGET on the kernel, THEN fund. Both are
    #    prerequisites the kernel enforces: fund() reverts PolicyNotSet() without the policy and
    #    ZeroBudget() without the budget (the demo create→fund shortcut skips both).
    buyer.register_job(job_id)
    buyer.set_budget(job_id, amount)
    buyer.fund(job_id, amount)
    journal_commerce("FUND", job_id=job_id, amount=amount)

    # 3. provider serves inline — compute the report + submit on-chain (journals SUBMITTED_ONCHAIN).
    res = submit_for(make_job_ops(), {"jobId": job_id, "description": query})

    # 4. buyer settles (optimistic; may defer if a dispute window is open).
    status = None
    try:
        buyer.settle(job_id)
        status = str(buyer.get_job_status(job_id))
        journal_commerce("SETTLE", job_id=job_id, status=status)
    except Exception as e:
        status = "settle-deferred"
        journal_commerce("SETTLE_DEFERRED", job_id=job_id, detail=str(e)[:160])

    url = res.get("deliverableUrl")
    if not url:
        try:
            url = buyer.get_deliverable_url(job_id)
        except Exception:
            pass
    return {
        "ok": True, "stage": "served", "job_id": job_id, "buyer": buyer_addr, "provider": provider_addr,
        "network": info["network"], "amount": amount, "token": token, "token_address": token_addr,
        "status": status,
        # str()-coerce the SDK fields: they may be web3 HexBytes/AttributeDict, which FastAPI's
        # JSON encoder can't serialize → the /api/commerce/create-job endpoint would 500. Mirrors
        # write_heartbeat / submit_for. (None stays None.)
        "tx": (lambda t: str(t) if t is not None else None)(
            res.get("transactionHash") or res.get("tx") or res.get("txHash")
        ),
        "deliverable_hash": (lambda h: str(h) if h is not None else None)(
            res.get("deliverableHash") or res.get("hash") or res.get("deliverable")
        ),
        "deliverable_url": str(url) if url is not None else None,
    }


# --------------------------------------------------------------------------- #
# SETTLE finalization — close out served jobs once their OPTIMISTIC dispute
# window has elapsed. `settle()` reverts NotDecided() (selector 0x17be5b7b) until
# then, so create_and_serve_job leaves the job SETTLE_DEFERRED; this finalizes it
# later. Operator-local (buyer signs); guard with `buyer_available()`.
# --------------------------------------------------------------------------- #
_NOT_DECIDED = ("0x17be5b7b", "notdecided")  # NotDecided() — dispute window still open


def _journal_rows() -> list[dict]:
    """Parse COMMERCE_JOURNAL into dict rows (best-effort; mirrors reads._commerce_jobs' walk)."""
    try:
        lines = COMMERCE_JOURNAL.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except (ValueError, TypeError):
            continue
    return rows


def unsettled_job_ids() -> list[int]:
    """Job ids that were delivered on-chain (SUBMITTED_ONCHAIN) but have no later SETTLE event —
    i.e. served jobs still awaiting finalization. Read-only; key-free."""
    served: dict[int, bool] = {}
    settled: set[int] = set()
    for r in _journal_rows():
        jid, ev = r.get("job_id"), r.get("event")
        if jid is None:
            continue
        try:
            jid = int(jid)
        except (TypeError, ValueError):
            continue
        if ev == "SUBMITTED_ONCHAIN":
            served.setdefault(jid, True)
        elif ev == "SETTLE":
            settled.add(jid)
    return [j for j in served if j not in settled]


def settle_pending_jobs(job_ids: list[int] | None = None) -> dict:
    """Finalize served-but-unsettled ERC-8183 jobs whose optimistic dispute window has closed.

    For each id: `buyer.settle(jid)` → read status → journal SETTLE. A revert with NotDecided
    (`0x17be5b7b`) means the window is still open → classified DEFERRED (not an error), journaled
    SETTLE_DEFERRED; any other failure → error. Operator-local — caller must guard with
    `buyer_available()` (this builds the buyer signing wallet). Never raises; returns PUBLIC metadata.
    `job_ids=None` → every currently-unsettled job from the ledger."""
    from ictbot.agent.identity import _lower_sdk_gas_floor

    # Match the create path's gas handling so a direct-gas settle doesn't 30x-overpay / paymaster-403.
    _lower_sdk_gas_floor()
    ids = [int(j) for j in (job_ids if job_ids is not None else unsettled_job_ids())]
    settled: list[dict] = []
    deferred: list[dict] = []
    errors: list[dict] = []
    buyer = None
    if ids:
        try:
            buyer = _buyer_client()
        except Exception as e:  # can't even build the wallet → report, don't crash
            return {
                "ok": False, "pending_before": len(ids), "settled": [], "deferred": [],
                "errors": [{"job_id": j, "error": f"buyer wallet: {str(e)[:120]}"} for j in ids],
                "network": _net_name(),
                "message": f"could not build buyer wallet: {str(e)[:120]}",
            }
    for jid in ids:
        try:
            buyer.settle(jid)
            status = str(buyer.get_job_status(jid))
            journal_commerce("SETTLE", job_id=jid, status=status)
            settled.append({"job_id": jid, "status": status})
        except Exception as e:
            msg = str(e)
            if any(tok in msg.lower() for tok in _NOT_DECIDED):
                journal_commerce("SETTLE_DEFERRED", job_id=jid, detail=msg[:160])
                deferred.append({"job_id": jid, "reason": "dispute window still open"})
            else:
                errors.append({"job_id": jid, "error": msg[:160]})
    n_s, n_d, n_e = len(settled), len(deferred), len(errors)
    if not ids:
        message = "no jobs awaiting finalization"
    else:
        message = f"{n_s} settled · {n_d} still deferred (window open) · {n_e} error(s)"
    return {
        "ok": not errors, "pending_before": len(ids),
        "settled": settled, "deferred": deferred, "errors": errors,
        "network": _net_name(), "message": message,
    }
