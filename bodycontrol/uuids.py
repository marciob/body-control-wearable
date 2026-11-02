"""GATT UUIDs used by the WHOOP 5.0 / MG strap.

Sources: community reverse engineering (see README, "Sources").
The vendor service needs a bonded link. The standard services do not.
"""

# Standard Bluetooth SIG services. These work WITHOUT a bond.
HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"  # notify: HR + R-R intervals
BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"  # read: percent

# Device Information Service. Tells a 5.0 from an MG.
SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"  # prefix "5AG" = WHOOP 5.0
HARDWARE_REVISION = "00002a27-0000-1000-8000-00805f9b34fb"  # contains "WG50" = WHOOP 5.0
FIRMWARE_REVISION = "00002a26-0000-1000-8000-00805f9b34fb"

# WHOOP 5.0 / MG vendor service ("puffin" framing). Every operation needs a bond.
WHOOP5_SERVICE = "fd4b0001-cce1-4033-93ce-002d5875f58a"
WHOOP5_CMD_WRITE = "fd4b0002-cce1-4033-93ce-002d5875f58a"  # app -> strap, write WITH response
WHOOP5_NOTIFY = (
    "fd4b0003-cce1-4033-93ce-002d5875f58a",
    "fd4b0004-cce1-4033-93ce-002d5875f58a",
    "fd4b0005-cce1-4033-93ce-002d5875f58a",
    "fd4b0007-cce1-4033-93ce-002d5875f58a",
)

# WHOOP 4.0 vendor service. Listed only so a scan can name it.
WHOOP4_SERVICE = "61080001-8d6d-82b8-614a-1c8cb0f8dcc6"

# Second WHOOP service family. Seen advertised by a device named "WBB5BP...".
# NOOP lists it as "puffin1150": detected, not supported. Likely the 5.0 battery pack.
WHOOP_1150_SERVICE = "11500001-6215-11ee-8c99-0242ac120002"

# Short prefixes used to recognise a strap in scan results.
WHOOP5_PREFIX = "fd4b0001"
WHOOP4_PREFIX = "61080001"
WHOOP_1150_PREFIX = "11500001"
