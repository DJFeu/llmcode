"""Spinner verb pool ported from Claude Code's spinnerVerbs.ts.

Provides a pool of whimsical verbs used to label the TUI spinner while the
model is thinking/processing. Deterministic seeding supported for tests.
"""
from __future__ import annotations

import random

DEFAULT_VERBS: tuple[str, ...] = (
    "Accomplishing", "Actioning", "Actualizing", "Architecting", "Baking",
    "Beaming", "Beboppin'", "Befuddling", "Billowing", "Blanching",
    "Bloviating", "Boogieing", "Boondoggling", "Booping", "Bootstrapping",
    "Brewing", "Bunning", "Burrowing", "Calculating", "Canoodling",
    "Caramelizing", "Cascading", "Catapulting", "Cerebrating", "Channeling",
    "Channelling", "Choreographing", "Churning", "Clauding", "Coalescing",
    "Cogitating", "Combobulating", "Composing", "Computing", "Concocting",
    "Considering", "Contemplating", "Cooking", "Crafting", "Creating",
    "Crunching", "Crystallizing", "Cultivating", "Deciphering", "Deliberating",
    "Determining", "Dilly-dallying", "Discombobulating", "Doing", "Doodling",
    "Drizzling", "Ebbing", "Effecting", "Elucidating", "Embellishing",
    "Enchanting", "Envisioning", "Evaporating", "Fermenting", "Fiddle-faddling",
    "Finagling", "Flambéing", "Flibbertigibbeting", "Flowing", "Flummoxing",
    "Fluttering", "Forging", "Forming", "Frolicking", "Frosting",
    "Gallivanting", "Galloping", "Garnishing", "Generating", "Gesticulating",
    "Germinating", "Gitifying", "Grooving", "Gusting", "Harmonizing",
    "Hashing", "Hatching", "Herding", "Honking", "Hullaballooing",
    "Hyperspacing", "Ideating", "Imagining", "Improvising", "Incubating",
    "Inferring", "Infusing", "Ionizing", "Jitterbugging", "Julienning",
    "Kneading", "Leavening", "Levitating", "Lollygagging", "Manifesting",
    "Marinating", "Meandering", "Metamorphosing", "Misting", "Moonwalking",
    "Moseying", "Mulling", "Mustering", "Musing", "Nebulizing",
    "Nesting", "Newspapering", "Noodling", "Nucleating", "Orbiting",
    "Orchestrating", "Osmosing", "Perambulating", "Percolating", "Perusing",
    "Philosophising", "Photosynthesizing", "Pollinating", "Pondering",
    "Pontificating", "Pouncing", "Precipitating", "Prestidigitating",
    "Processing", "Proofing", "Propagating", "Puttering", "Puzzling",
    "Quantumizing", "Razzle-dazzling", "Razzmatazzing", "Recombobulating",
    "Reticulating", "Roosting", "Ruminating", "Sautéing", "Scampering",
    "Schlepping", "Scurrying", "Seasoning", "Shenaniganing", "Shimmying",
    "Simmering", "Skedaddling", "Sketching", "Slithering", "Smooshing",
    "Sock-hopping", "Spelunking", "Spinning", "Sprouting", "Stewing",
    "Sublimating", "Swirling", "Swooping", "Symbioting", "Synthesizing",
    "Tempering", "Thinking", "Thundering", "Tinkering", "Tomfoolering",
    "Topsy-turvying", "Transfiguring", "Transmuting", "Twisting", "Undulating",
    "Unfurling", "Unravelling", "Vibing", "Waddling", "Wandering",
    "Warping", "Whatchamacalliting", "Whirlpooling", "Whirring", "Whisking",
    "Wibbling", "Working", "Wrangling", "Zesting", "Zigzagging",
)


def get_verb(
    seed: int | None = None,
    override: tuple[str, ...] = (),
    mode: str = "append",
) -> str:
    """Return a random spinner verb.

    Args:
        seed: Optional seed for deterministic selection (testability).
        override: Additional or replacement verbs from user config.
        mode: "append" extends defaults; "replace" uses only override
              (falls back to defaults if override is empty).
    """
    if mode == "replace":
        pool: tuple[str, ...] = override if override else DEFAULT_VERBS
    else:
        pool = DEFAULT_VERBS + tuple(override)
    rng = random.Random(seed)
    return rng.choice(pool)
