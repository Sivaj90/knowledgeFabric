"""Classify step (HLD §7.2). Slice 1 hardcodes a fixed tier for every
chunk -- no real rule-based classifier or LLM-assist yet. Isolated into its
own function so the later real classifier is a drop-in replacement here,
not a change to every caller.
"""

# Hardcoded per Slice 1 scope (local VPC HLD §4 explicitly calls this out:
# "hardcoded classify (`internal` tier)"). effective_tier = classification_tier
# for Slice 1 since there is no native-source ACL signal yet to max() against
# (HLD §7.2: effective_tier = max(rule tier, native ACL tier) -- with no real
# native ACL yet, there's nothing to take a max against).
HARDCODED_TIER = "internal"


def classify_chunk(content: str) -> tuple[str, str]:
    """Returns (classification_tier, effective_tier). Both hardcoded to
    "internal" for every chunk in Slice 1."""
    return HARDCODED_TIER, HARDCODED_TIER
