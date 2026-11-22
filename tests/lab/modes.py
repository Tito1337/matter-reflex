"""Lab policy assignments and expected handlers for a single-controller outage."""

# The two gestures use direct assignment. Each additional sensor exercises one
# of the other allowed pairs on the same fabric and the same integration entry.
ADVANCED = {
    "multi_press_2": ("esp", "ha"),
    "long_press": ("esp", "ha"),
    "contact": ("ha", "esp"),
    "occupancy": ("esp", "none"),
    "leak": ("ha", "none"),
    "temperature": ("none", "ha"),
    "humidity": ("none", "esp"),
}


PRESETS = {"direct": ("esp", "ha"), "fallback": ("ha", "esp")}


def handlers(mode, rule):
    if mode == "advanced":
        return ADVANCED[rule]
    return PRESETS[mode]


def condition(mode, rule, entry):
    if mode == "advanced":
        normal, degraded = handlers(mode, rule)
        return {
            "condition": "matter_reflex.handlers",
            "options": {
                "esp": entry,
                "normal_handler": normal,
                "degraded_handler": degraded,
            },
        }
    return {"condition": "matter_reflex.mode", "options": {"esp": entry, "mode": mode}}


def executor(mode, rule, offline):
    normal, degraded = handlers(mode, rule)
    if normal != "none" and normal != offline:
        return normal
    if offline and degraded != offline:
        return degraded
    return "none"
