"""python -m agent.scoring [merchant_id ...]  — score at the current simulation date."""
import asyncio
import sys

from agent.scoring.scorer import main

sys.stdout.reconfigure(encoding="utf-8")
asyncio.run(main(sys.argv[1:]))
