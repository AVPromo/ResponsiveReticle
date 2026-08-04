from typing import Optional

import BigWorld
from Avatar import PlayerAvatar
from items.vehicles import VehicleDescriptor


# I know, very creative name for a class from my side ...
class ShouldBoostTickRateHelper(object):

    def __init__(self):
        pass

    # this method is overall very fast (normally around 2 us)
    # but measurement (without cache) shows for first call around 10 us even through nothing of its calculation changed
    #
    # just to be sure, when this is called multiple times (and it can with some mods), just cache it at VGR.__onTick
    def shouldBoostTickRate(self):
        player = BigWorld.player()  # type: PlayerAvatar

        # we don't want to boost tick-rate, when current input controller
        # doesn't have gun marker that we want to increase responsiveness of
        if not hasattr(player.inputHandler.ctrl, "_gunMarker"):
            return False

        vehicleDescriptor = player.getVehicleDescriptor()  # type: VehicleDescriptor
        if vehicleDescriptor is None:
            return False

        # we don't want to change gun tick rate for vehicles that have static gun yaw (for example Strv 103B)
        # because it already has hull-controlled reticle movement
        # and because reticle blinks horribly due to 0/0 gun angles
        return vehicleDescriptor.gun.staticTurretYaw != 0


g_shouldBoostTickRateHelper = ShouldBoostTickRateHelper()
