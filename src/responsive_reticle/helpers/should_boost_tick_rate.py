from typing import Optional

import BigWorld
from items.vehicles import VehicleDescriptor


# I know, very creative name for a class from my side ...
class ShouldBoostTickRateHelper(object):

    def __init__(self):
        self.lastCheckedPlayerVehicleDescriptor = None  # type: Optional[VehicleDescriptor]
        self.lastIsPlayerVehicleValid = False

    # this method is overall very fast (normally around 2 us)
    # but measurement (without cache) shows for first call around 10 us even through nothing of its calculation changed
    #
    # just to be sure, when this is called multiple times (and it can with some mods), just cache it at VGR.__onTick
    def shouldBoostTickRate(self):
        # we don't want to change SPGs gun tick rate because it breaks top-down view reticle dots
        # and this mod is not useful for SPGs, so it's not an issue
        #
        # performance note: avoid BigWorld.entity(id).vehicle.typeDescriptor
        vehicleDescriptor = BigWorld.player().getVehicleDescriptor()  # type: VehicleDescriptor
        if vehicleDescriptor is None:
            return False

        # performance note: avoid iterating over tags list for a little faster result
        if self.lastCheckedPlayerVehicleDescriptor == vehicleDescriptor:
            return self.lastIsPlayerVehicleValid

        self.lastCheckedPlayerVehicleDescriptor = vehicleDescriptor

        if 'SPG' in vehicleDescriptor.type.tags:
            self.lastIsPlayerVehicleValid = False
            return False

        # we don't want to change gun tick rate for vehicles that have static gun yaw (for example Strv 103B)
        # because it already has hull-controlled reticle movement
        # and because reticle blinks horribly due to 0/0 gun angles
        self.lastIsPlayerVehicleValid = vehicleDescriptor.gun.staticTurretYaw != 0
        return self.lastIsPlayerVehicleValid


g_shouldBoostTickRateHelper = ShouldBoostTickRateHelper()
