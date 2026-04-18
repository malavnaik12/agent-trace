"""
query_generator.py — Agent 1

Uses Claude to produce a specific research question on a randomly chosen topic.
"""

import asyncio
import random
from anthropic import Anthropic

_TOPICS = [
    "quantum computing applications",
    "climate tipping points",
    "gene therapy breakthroughs",
    "nuclear fusion progress",
    "antibiotic resistance crisis",
    "deep ocean exploration",
    "dark matter detection",
    "urban vertical farming",
    "neuroplasticity research",
    "microplastics in human bodies",
    "longevity science advances",
    "autonomous vehicle safety",
    "psychedelic-assisted therapy",
    "coral reef restoration",
    "space debris solutions",
    "lab-grown meat scaling",
    "carbon capture technology",
    "AI interpretability",
    "quantum cryptography",
    "wildfire prediction models",
]


async def generate_query(client: Anthropic) -> str:
    """Return a focused research question on a random topic."""
    topic = random.choice(_TOPICS)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate one specific, interesting research question about {topic}. "
                    "Return only the question itself, nothing else. "
                    "Make it specific and intellectually interesting, not generic."
                ),
            }],
        ),
    )
    return response.content[0].text.strip()
