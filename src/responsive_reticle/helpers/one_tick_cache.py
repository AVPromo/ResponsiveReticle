from typing import Optional

import Math
import BigWorld
from Avatar import PlayerAvatar
from Vehicle import Vehicle
from VehicleGunRotator import VehicleGunRotator
from avatar_components.AvatarObserver import AvatarObserver
from items.vehicles import VehicleDescriptor

from responsive_reticle.helpers.should_boost_tick_rate import g_shouldBoostTickRateHelper


# we're putting old methods here instead of near hooks
# because we have circular dependency between hooks and this cache,
#
# and we want to avoid import statements in updateCache()
# because those takes on average extra 10 us from VGR tick
#
# and vice versa for our hooks as well
old_observer_getVehicleAttached = AvatarObserver.getVehicleAttached
old_avatar_getVehicleDescriptor = PlayerAvatar.getVehicleDescriptor
old_avatar_getOwnVehicleStabilisedMatrix = PlayerAvatar.getOwnVehicleStabilisedMatrix
old_VGR_getAvatarOwnVehicleStabilisedMatrix = VehicleGunRotator.getAvatarOwnVehicleStabilisedMatrix


# those method results in this cache are calculated only once
# and their hooked variants kept as fast as possible
# to keep very fast repeated calls on some a little heavier result calculations
#
# next calls on such methods are around 0.2 us!
class OneTickCache(object):

    def __init__(self):
        self.isDuringVgrTick = False

        self.shouldBoostTickRate = False

        # we want them to be kept as properties instead of methods, because this is fast
        self.gunRotator_avatarOwnVehicleStabilisedMatrix = Math.Matrix()
        self.avatar_ownVehicleStabilisedMatrix = Math.Matrix()
        self.observer_vehicleAttached = None  # type: Optional[Vehicle]
        self.avatar_vehicleDescriptor = None  # type: Optional[VehicleDescriptor]

    def updateCache(self):
        player = BigWorld.player()

        # by calling them in this order, every next cache value loading uses previous one
        # nice!
        self.observer_vehicleAttached = old_observer_getVehicleAttached(player)
        self.avatar_vehicleDescriptor = old_avatar_getVehicleDescriptor(player)
        self.avatar_ownVehicleStabilisedMatrix = old_avatar_getOwnVehicleStabilisedMatrix(player)
        self.gunRotator_avatarOwnVehicleStabilisedMatrix = old_VGR_getAvatarOwnVehicleStabilisedMatrix(player.gunRotator)

        self.shouldBoostTickRate = g_shouldBoostTickRateHelper.shouldBoostTickRate()


g_oneTickCache = OneTickCache()
