import asyncio

import httpx


async def main() -> None:
    base_url = "http://localhost:8000"
    async with httpx.AsyncClient(timeout=10.0) as client:
        health_response = await client.get(f"{base_url}/ops/health")
        health_response.raise_for_status()
        health = health_response.json()
        heartbeats = health.get("heartbeats")
        if not isinstance(heartbeats, dict):
            raise RuntimeError("ops/health missing heartbeats")
        fresh = [name for name, age in heartbeats.items() if isinstance(age, (int, float)) and age < 45]
        if not fresh:
            raise RuntimeError("No fresh worker heartbeats found; ensure workers are running")

        metrics_response = await client.get(f"{base_url}/ops/metrics")
        metrics_response.raise_for_status()
        metrics = metrics_response.json()
        for key in ["alerts_by_regime", "trap_rate", "avg_net_return_by_horizon", "top_wallets", "top_pairs"]:
            if key not in metrics:
                raise RuntimeError(f"ops/metrics missing key: {key}")

    print("smoke_ops passed")


if __name__ == "__main__":
    asyncio.run(main())
