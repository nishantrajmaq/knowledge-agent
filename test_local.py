import asyncio
import logging

from app.agent import build_agent

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    agent = build_agent()
    print(f"{agent.name} ready. Type a question (Ctrl+C to quit).")

    while True:
        try:
            message = input("\nyou> ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not message:
            continue

        result = await agent.run(message)
        print(f"agent> {result.text}")


if __name__ == "__main__":
    asyncio.run(main())
