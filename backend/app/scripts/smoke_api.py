import asyncio
import json

import httpx


async def main() -> None:
    base_url = "http://localhost:8000"
    async with httpx.AsyncClient(timeout=10.0) as client:
        health_response = await client.get(f"{base_url}/health")
        health_response.raise_for_status()
        print("Health:", health_response.json())

        alerts_response = await client.get(f"{base_url}/alerts", params={"limit": 5, "offset": 0})
        alerts_response.raise_for_status()
        alerts_payload = alerts_response.json()
        if not isinstance(alerts_payload, list):
            raise RuntimeError("Expected alerts payload to be a list.")
        print(f"Alerts count: {len(alerts_payload)}")
        if alerts_payload:
            print("Alert sample:", json.dumps(alerts_payload[0], indent=2))
        else:
            raise RuntimeError("No alerts returned. Run smoke_alerts first.")


if __name__ == "__main__":
    asyncio.run(main())
