"""Import point for the live distributor channel (Photon + ElevenLabs). Imported at startup.

`channel_for` uses this factory when the `phone` dependency is `live`. In `cached` mode the
fixture channel runs instead, so no SMS or call leaves the machine.
"""

from app.distributor.live import LiveDistributor
from app.distributor.registry import register_live_channel

register_live_channel(LiveDistributor)
