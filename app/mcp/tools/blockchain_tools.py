"""
Nexus - Blockchain & Cryptocurrency Analysis Tools.

Tools for tracing cryptocurrency transactions, wallet clustering,
mixer detection, and exchange attribution.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)


async def _handle_blockchain_trace(arguments: dict) -> str:
    """Trace cryptocurrency transaction flow from a given address or tx hash."""
    address = arguments.get("address", "")
    tx_hash = arguments.get("tx_hash", "")
    chain = arguments.get("chain", "bitcoin")
    depth = arguments.get("depth", 3)
    direction = arguments.get("direction", "both")

    if not address and not tx_hash:
        return "Error: either 'address' or 'tx_hash' is required."

    results: list[str] = ["## Blockchain Transaction Trace"]
    results.append(f"Chain: {chain}")
    results.append(f"Target: {address or tx_hash}")
    results.append(f"Direction: {direction} | Depth: {depth}")
    results.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    results.append("")

    identifier = address or tx_hash

    # Query public blockchain explorers via web search
    try:
        from app.mcp.tools.search_engines import _do_web_search

        if chain == "bitcoin":
            explorer_query = f"blockchain.com btc address {identifier}"
        elif chain in ("ethereum", "eth"):
            explorer_query = f"etherscan.io address {identifier}"
        elif chain == "tron":
            explorer_query = f"tronscan.org address {identifier}"
        else:
            explorer_query = f"{chain} blockchain explorer {identifier}"

        search_results = await _do_web_search(explorer_query, max_results=5)
        if search_results:
            results.append("### Explorer References")
            for r in search_results[:3]:
                results.append(f"  - {r.get('title', '')[:80]}")
                results.append(f"    URL: {r.get('url', '')}")
        else:
            results.append("### No explorer results found via search")
    except Exception as e:
        results.append(f"### Explorer search error: {e}")

    # Attempt direct API queries for public blockchains
    if chain == "bitcoin" and address:
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"https://blockchain.info/rawaddr/{address}?limit=20"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        balance = data.get("final_balance", 0) / 1e8
                        total_received = data.get("total_received", 0) / 1e8
                        total_sent = data.get("total_sent", 0) / 1e8
                        n_tx = data.get("n_tx", 0)

                        results.append(f"\n### Bitcoin Address Summary")
                        results.append(f"  Balance: {balance:.8f} BTC")
                        results.append(f"  Total received: {total_received:.8f} BTC")
                        results.append(f"  Total sent: {total_sent:.8f} BTC")
                        results.append(f"  Transaction count: {n_tx}")

                        txs = data.get("txs", [])[:10]
                        if txs:
                            results.append(f"\n### Recent Transactions (last {len(txs)})")
                            for tx in txs[:5]:
                                tx_time = datetime.fromtimestamp(
                                    tx.get("time", 0), timezone.utc
                                ).isoformat()
                                inputs = [
                                    inp.get("prev_out", {}).get("addr", "?")
                                    for inp in tx.get("inputs", [])
                                ]
                                outputs = [
                                    (out.get("addr", "?"), out.get("value", 0) / 1e8)
                                    for out in tx.get("out", [])
                                ]
                                results.append(f"  TX: {tx.get('hash', '')[:16]}...")
                                results.append(f"    Time: {tx_time}")
                                results.append(f"    Inputs: {', '.join(inputs[:3])}")
                                results.append(
                                    f"    Outputs: {', '.join(f'{a}({v:.4f})' for a, v in outputs[:3])}"
                                )
                    else:
                        results.append(f"\n### API query failed (status {resp.status})")
        except Exception as e:
            results.append(f"\n### Direct API error: {e}")

    elif chain in ("ethereum", "eth") and address:
        results.append("\n### Ethereum Tracing")
        results.append("  Note: Full Ethereum tracing requires Etherscan API key.")
        results.append("  Use execute_code with web3.py for detailed analysis.")
        results.append(f"  Etherscan link: https://etherscan.io/address/{address}")

    results.append("\n## ANALYSIS NOTES")
    results.append("- Transaction data from public APIs is CURRENT but may have propagation delays")
    results.append("- Wallet clustering requires multiple transactions for confidence")
    results.append("- Change addresses may belong to same entity (common-input heuristic)")
    results.append("- Exchange deposits go to unique addresses — check known exchange databases")
    results.append("- NEVER declare ownership without multiple corroborating signals")

    return "\n".join(results)


async def _btc_input_addresses(session: Any, address: str) -> tuple[set[str], int]:
    """Fetch the set of addresses that co-appear as inputs with `address`.

    Returns (co_input_addresses, txs_seen). Real query against blockchain.info.
    Raises on network/parse failure so the caller can report honestly.
    """
    url = f"https://blockchain.info/rawaddr/{address}?limit=50"
    async with session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"blockchain.info returned HTTP {resp.status} for {address}")
        data = await resp.json()
    txs = data.get("txs", [])
    co_inputs: set[str] = set()
    for tx in txs:
        input_addrs = {
            inp.get("prev_out", {}).get("addr", "")
            for inp in tx.get("inputs", [])
        }
        input_addrs.discard("")
        # Common-input-ownership: if `address` is among the inputs, every other
        # input address in that tx is probably the same entity.
        if address in input_addrs:
            co_inputs |= (input_addrs - {address})
    return co_inputs, len(txs)


async def _handle_wallet_cluster(arguments: dict) -> str:
    """Cluster addresses likely belonging to the same entity.

    For Bitcoin, performs the REAL common-input-ownership heuristic by querying
    each address's transactions and union-find grouping addresses that co-appear
    as inputs. For unsupported chains it returns success=false honestly rather
    than fabricating a result.
    """
    addresses = arguments.get("addresses", [])
    chain = arguments.get("chain", "bitcoin")
    method = arguments.get("method", "common_input")

    if not addresses:
        return "Error: 'addresses' list is required (at least 2 addresses)."
    if len(addresses) < 2:
        return "Error: need at least 2 addresses to cluster."

    if chain != "bitcoin" or method != "common_input":
        return (
            f"Error: real clustering is currently implemented only for "
            f"chain='bitcoin' with method='common_input'. Requested "
            f"chain='{chain}', method='{method}'. For other chains/methods, use "
            f"`transaction_graph` to pull data, then `execute_code` with networkx "
            f"to run the heuristic — this tool will not fabricate a grouping."
        )

    results: list[str] = ["## Wallet Clustering Analysis (common-input, live)"]
    results.append(f"Chain: {chain}")
    results.append(f"Input addresses: {len(addresses)}")
    results.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    results.append("")

    # Union-Find over the input addresses, linking any that share a tx input.
    parent: dict[str, str] = {a: a for a in addresses}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    addr_set = set(addresses)
    errors: list[str] = []
    co_input_map: dict[str, set[str]] = {}

    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for addr in addresses:
                try:
                    co_inputs, _ = await _btc_input_addresses(session, addr)
                    co_input_map[addr] = co_inputs
                    # Link with any OTHER input address we were asked about.
                    for other in addr_set & co_inputs:
                        union(addr, other)
                except Exception as e:  # noqa: BLE001 — report per-address, keep going
                    errors.append(f"{addr}: {e}")
    except Exception as e:  # noqa: BLE001
        return f"Error: could not run clustering (network/session failure): {e}"

    if len(co_input_map) == 0:
        return (
            "Error: no address could be queried successfully — clustering not "
            "performed.\n" + "\n".join(f"  - {e}" for e in errors)
        )

    # Group addresses by their union-find root.
    clusters: dict[str, list[str]] = {}
    for addr in addresses:
        clusters.setdefault(find(addr), []).append(addr)

    results.append("### Clusters (addresses linked by a shared transaction input)")
    for i, (_, members) in enumerate(
        sorted(clusters.items(), key=lambda kv: -len(kv[1])), 1
    ):
        if len(members) > 1:
            results.append(f"  Cluster {i} (LINKED, HIGH confidence): {', '.join(members)}")
        else:
            results.append(f"  Cluster {i} (singleton — no shared input found): {members[0]}")

    linked = sum(1 for m in clusters.values() if len(m) > 1)
    results.append("")
    results.append(f"### Summary: {len(clusters)} cluster(s), {linked} with 2+ linked addresses")
    if errors:
        results.append("\n### ⚠️ Partial data — these addresses failed to query:")
        for e in errors:
            results.append(f"  - {e}")

    results.append("\n## CONFIDENCE NOTES")
    results.append("- Links are from the common-input-ownership heuristic (shared tx input).")
    results.append("- HIGH confidence for Bitcoin, but CoinJoin/PayJoin can break this.")
    results.append("- Singletons mean no shared input was found in the last 50 txs — NOT proof of independence.")

    return "\n".join(results)


async def _handle_transaction_graph(arguments: dict) -> str:
    """Build and analyze transaction graph for an address."""
    address = arguments.get("address", "")
    chain = arguments.get("chain", "bitcoin")
    hops = arguments.get("hops", 2)
    focus = arguments.get("focus", "all")

    if not address:
        return "Error: 'address' is required."

    results: list[str] = ["## Transaction Graph Analysis"]
    results.append(f"Chain: {chain}")
    results.append(f"Root address: {address}")
    results.append(f"Hop depth: {hops}")
    results.append(f"Focus: {focus}")
    results.append(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    results.append("")

    # Fetch transaction data
    if chain == "bitcoin":
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"https://blockchain.info/rawaddr/{address}?limit=50"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        txs = data.get("txs", [])

                        # Build counterparty graph
                        counterparties: dict[str, dict] = {}
                        for tx in txs:
                            for inp in tx.get("inputs", []):
                                addr_in = inp.get("prev_out", {}).get("addr", "")
                                if addr_in and addr_in != address:
                                    if addr_in not in counterparties:
                                        counterparties[addr_in] = {"sent_to_us": 0, "received_from_us": 0, "tx_count": 0}
                                    counterparties[addr_in]["sent_to_us"] += inp.get("prev_out", {}).get("value", 0) / 1e8
                                    counterparties[addr_in]["tx_count"] += 1
                            for out in tx.get("out", []):
                                addr_out = out.get("addr", "")
                                if addr_out and addr_out != address:
                                    if addr_out not in counterparties:
                                        counterparties[addr_out] = {"sent_to_us": 0, "received_from_us": 0, "tx_count": 0}
                                    counterparties[addr_out]["received_from_us"] += out.get("value", 0) / 1e8
                                    counterparties[addr_out]["tx_count"] += 1

                        results.append(f"### Graph Summary")
                        results.append(f"  Total transactions analyzed: {len(txs)}")
                        results.append(f"  Unique counterparties: {len(counterparties)}")

                        # Top counterparties by volume
                        sorted_cp = sorted(
                            counterparties.items(),
                            key=lambda x: x[1]["sent_to_us"] + x[1]["received_from_us"],
                            reverse=True,
                        )

                        results.append(f"\n### Top Counterparties (by volume)")
                        for cp_addr, stats in sorted_cp[:10]:
                            total = stats["sent_to_us"] + stats["received_from_us"]
                            results.append(
                                f"  {cp_addr[:16]}... | "
                                f"in:{stats['sent_to_us']:.4f} out:{stats['received_from_us']:.4f} "
                                f"| txs:{stats['tx_count']}"
                            )

                        # Detect patterns
                        results.append(f"\n### Pattern Detection")
                        peel_candidates = [
                            a for a, s in counterparties.items()
                            if s["tx_count"] == 1 and s["received_from_us"] > 0
                        ]
                        if len(peel_candidates) > 5:
                            results.append(
                                f"  ⚠️ Peel chain indicator: {len(peel_candidates)} "
                                f"single-use output addresses detected"
                            )

                        high_freq = [
                            a for a, s in counterparties.items() if s["tx_count"] >= 5
                        ]
                        if high_freq:
                            results.append(
                                f"  🔄 High-frequency counterparties: {len(high_freq)} "
                                f"(possible exchange or service)"
                            )
                    else:
                        results.append(f"API returned status {resp.status}")
        except Exception as e:
            results.append(f"Error fetching transaction data: {e}")
    else:
        results.append(f"Chain '{chain}' requires custom API integration.")
        results.append("Use execute_code with web3.py (Ethereum) or chain-specific SDK.")

    results.append("\n## INTERPRETATION GUIDELINES")
    results.append("- High-volume single counterparty: likely exchange hot wallet")
    results.append("- Many single-use outputs: peel chain (sequential small sends)")
    results.append("- Equal-value outputs: possible CoinJoin/mixer usage")
    results.append("- Rapid cascading transactions: possible layering")
    results.append("- Round-trip (A→B→A): possible self-transfer or testing")

    return "\n".join(results)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="blockchain_trace",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "address": {
                "type": "string",
                "description": "Cryptocurrency address to trace",
            },
            "tx_hash": {
                "type": "string",
                "description": "Transaction hash to trace (alternative to address)",
            },
            "chain": {
                "type": "string",
                "description": "Blockchain: bitcoin, ethereum, tron, bsc, polygon",
                "enum": ["bitcoin", "ethereum", "tron", "bsc", "polygon"],
                "default": "bitcoin",
            },
            "depth": {
                "type": "integer",
                "description": "Number of hops to trace (1-5)",
                "default": 3,
            },
            "direction": {
                "type": "string",
                "description": "Trace direction: incoming, outgoing, or both",
                "enum": ["incoming", "outgoing", "both"],
                "default": "both",
            },
        },
    },
    handler=_handle_blockchain_trace,
    is_async=True,
    emoji="⛓️",
    category="intelligence",
)

registry.register(
    name="wallet_cluster",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "addresses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of addresses to cluster",
            },
            "chain": {
                "type": "string",
                "description": "Blockchain network",
                "enum": ["bitcoin", "ethereum", "tron", "bsc"],
                "default": "bitcoin",
            },
            "method": {
                "type": "string",
                "description": "Clustering method",
                "enum": ["common_input", "change_address", "behavioral"],
                "default": "common_input",
            },
        },
        "required": ["addresses"],
    },
    handler=_handle_wallet_cluster,
    is_async=True,
    emoji="🔗",
    category="intelligence",
)

registry.register(
    name="transaction_graph",
    toolset="intelligence",
    schema={
        "type": "object",
        "properties": {
            "address": {
                "type": "string",
                "description": "Root address for graph analysis",
            },
            "chain": {
                "type": "string",
                "description": "Blockchain network",
                "enum": ["bitcoin", "ethereum", "tron", "bsc"],
                "default": "bitcoin",
            },
            "hops": {
                "type": "integer",
                "description": "Graph traversal depth (1-3)",
                "default": 2,
            },
            "focus": {
                "type": "string",
                "description": "Focus area: all, high_value, frequent, suspicious",
                "enum": ["all", "high_value", "frequent", "suspicious"],
                "default": "all",
            },
        },
        "required": ["address"],
    },
    handler=_handle_transaction_graph,
    is_async=True,
    emoji="📊",
    category="intelligence",
)
