"""Clean-slate TRCC architecture.

Hexagonal (ports & adapters) with a Command bus.  Five roles:

    Platform       OS I/O primitives (ABC, one per OS)
    UsbTransport   byte mover (ABC)
    Device         physical device, knows its wire protocol (ABC, one per protocol)
    App            holds Platform + devices, dispatches Commands
    UIs            thin adapters (CLI / GUI / API), speak Commands + Results

Built inside-out: core → adapters → UIs.  Existing code in sibling
packages is untouched during the build; switchover happens once feature
parity lands.
"""
