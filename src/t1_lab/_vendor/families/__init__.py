"""Data-only family registry; importing both profiles does not load either ROS ABI."""

from . import t1

FAMILIES = {"t1": t1}
COMMITS = {name: profile.SDK_COMMIT for name, profile in FAMILIES.items()}
CAMERAS = {name: profile.CAMERAS for name, profile in FAMILIES.items()}
EMOTIONS = {name: profile.EMOTIONS for name, profile in FAMILIES.items()}
MODES = {name: profile.PRESET_MODE for name, profile in FAMILIES.items()}
PRESETS = {name: profile.PRESETS for name, profile in FAMILIES.items()}
