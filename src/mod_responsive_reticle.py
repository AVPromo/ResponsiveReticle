import logging
from math import pi
from typing import Optional

import BattleReplay
import BigWorld
import constants
from Avatar import PlayerAvatar
from AvatarInputHandler import aih_global_binding, _BINDING_ID
from AvatarInputHandler.gun_marker_ctrl import _GunMarkerController, _CrosshairShotResults
from VehicleGunRotator import VehicleGunRotator
from aih_constants import SHOT_RESULT, GUN_MARKER_TYPE
from debug_utils import LOG_WARNING
from gui.Scaleform.daapi.view.battle.shared.crosshair.plugins import ShotResultIndicatorPlugin
from gun_rotation_shared import calcPitchLimitsFromDesc
from items.vehicles import VehicleDescriptor
from projectile_trajectory import getShotAngles
from realm import CURRENT_REALM


log = logging.getLogger(__name__)


def isClientLesta():
    return CURRENT_REALM == 'RU'


def isClientWG():
    return not isClientLesta()


def overrideIn(cls, classMethod=False, staticMethod=False, condition=lambda: True):

    def _overrideMethod(func):
        if not condition():
            return func

        funcName = func.__name__

        if funcName.startswith("__") and funcName != "__init__":
            funcName = "_" + cls.__name__ + funcName

        old = getattr(cls, funcName)

        if staticMethod or classMethod:
            old = getattr(cls, funcName).__func__
        else:
            old = getattr(cls, funcName)

        if classMethod:
            @classmethod
            def wrapper(clss, *args, **kwargs):
                return func(old, clss, *args, **kwargs)
        elif staticMethod:
            @staticmethod
            def wrapper(*args, **kwargs):
                return func(old, *args, **kwargs)
        else:
            def wrapper(*args, **kwargs):
                return func(old, *args, **kwargs)

        setattr(cls, funcName, wrapper)
        return wrapper
    return _overrideMethod


def shouldBoostTickRate():
    # we don't want to change tick rate when we're displaying replay
    if BattleReplay.isPlaying():
        return False

    player = BigWorld.player()  # type: PlayerAvatar

    veh = BigWorld.entity(player.playerVehicleID)
    if veh is None:
        return False

    # we don't want to change SPGs gun tick rate because it breaks top-down view reticle dots
    # and this mod is not useful for SPGs, so it's not an issue
    vehicleDescriptor = veh.typeDescriptor  # type: VehicleDescriptor
    if 'SPG' in vehicleDescriptor.type.tags:
        return False

    # we don't want to change gun tick rate for vehicles that have static gun yaw (for example Strv 103B)
    # because it already has hull-controlled reticle movement
    # and because reticle blinks horribly due to 0/0 gun angles
    return vehicleDescriptor.gun.staticTurretYaw != 0


# __ROTATION_TICK_LENGTH controls, how fast gun rotator updates vehicle turret rotation and gun markers
#
# __INSUFFICIENT_TIME_DIFF controls how small the time diff must be before rotation update can be performed
# because by default it is 0.02 (where rotation __ROTATION_TICK_LENGTH was 0.1), we have to lower it some reasonably
# to allow faster tick-rate

@overrideIn(VehicleGunRotator)
def __onTick(func, self):
    if shouldBoostTickRate():
        # contract of BigWorld.callback(delay, func) which is used with those constants, is
        # that BigWorld is required to call func no earlier than provided delay
        # but that doesn't mean it will be exactly this delay - it might be delayed even more
        #
        # we set callback to 0.001 because we want gun rotator to be called in next game tick,
        # but we DO NOT accept zero delay
        # otherwise, in gun rotator code there would be division by zero in some places
        # and overall we don't want it to be this close to floating-point precision limits
        VehicleGunRotator._VehicleGunRotator__ROTATION_TICK_LENGTH = 0.001
        VehicleGunRotator._VehicleGunRotator__INSUFFICIENT_TIME_DIFF = 0.0005
    else:
        VehicleGunRotator._VehicleGunRotator__ROTATION_TICK_LENGTH = constants.SERVER_TICK_LENGTH
        VehicleGunRotator._VehicleGunRotator__INSUFFICIENT_TIME_DIFF = 0.02

    func(self)


@overrideIn(_GunMarkerController)
def _updateMatrixProvider(func, self, positionMatrix, relaxTime=0.0):
    # second check makes sure, we alter relaxTime only for client-side reticle code - not the server one
    if shouldBoostTickRate() and relaxTime == VehicleGunRotator._VehicleGunRotator__ROTATION_TICK_LENGTH:
        # when ROTATION_TICK_LENGTH is quite small (like 0.006), then reticle movement stutters,
        # and it is like that even despite surrounding code properly interpolating it
        # I even did my own manual interpolation just to exclude potential Math.MatrixAnimation() flaw or something
        #
        # generally interpolation in games doesn't work well when tick-rate is very fast
        # due to time variance, distance variation (which causes movement oscillation)
        # and randomly gives period of time, where position is not interpolated due to finished destination
        #
        # so - for such high tick-rate it is better to remove interpolation
        # and trigger next reticle position every frame (its still fast code, so we can do that)
        relaxTime = 0

    func(self, positionMatrix, relaxTime)


# Avatar.getOwnVehicleShotDispersionAngle() is not only a "getter"
# it does modify player state related to reticle size
#
# the problem is:
# - when, with high tick-rate, we move reticle even slightly, it is registered as "full turret move"
# due to very low time diff in which this move happened (low maximum turret yaw angle) - resulting in big reticle bloom
# which doesn't actually happen on the server (and vanilla client)
# - for the same small mouse movement this does not happen on lower tick-rate,
# because time diff is big (0.1 sec), so proportionally maximum turret yaw angle is very big,
# so it won't be "full turret move" but just very small turret move, which is registered as much smaller reticle bloom
#
# we have to somehow compensate for that
# the problem is (again) - position updates are tied to dispersion angle updates,
# and we have to somehow separate them now
#
# AND - we have to interpolate that size
# otherwise it would be very stuttering


# we unfortunately have to override entire method,
# because only in this place we want to capture avatar.getOwnVehicleShotDispersionAngle() calls
#
# we want to alter them, because we have to separate invocation rate of that method from gun rotator code
# which we would do by introducing cache for 0.1 second
# that return last computed values (and calls that method only when computing it)
# and do it in clever way to simulate slower tick-rate
#
# also WG and Lesta specific
# slightly different method body
@overrideIn(VehicleGunRotator, condition=isClientWG)
def __rotate(func, self, shotPoint, timeDiff):
    self._VehicleGunRotator__turretRotationSpeed = 0.0
    targetPoint = shotPoint if shotPoint is not None else self._VehicleGunRotator__prevSentShotPoint
    replayCtrl = BattleReplay.g_replayCtrl
    if targetPoint is None or self._VehicleGunRotator__isLocked and not replayCtrl.isUpdateGunOnTimeWarp:
        if shouldBoostTickRate():
            self._VehicleGunRotator__dispersionAngles = getOwnVehicleShotDispersionAngleForGunRotator(self, 0.0)
        else:
            self._VehicleGunRotator__dispersionAngles = self._avatar.getOwnVehicleShotDispersionAngle(0.0)
    else:
        avatar = self._avatar
        descr = avatar.getVehicleDescriptor()
        turretYawLimits = self._VehicleGunRotator__getTurretYawLimits()
        maxTurretRotationSpeed = self._VehicleGunRotator__maxTurretRotationSpeed
        prevTurretYaw = self._VehicleGunRotator__turretYaw
        vehicleMatrix = self.getAvatarOwnVehicleStabilisedMatrix()
        if self._VehicleGunRotator__fixedShotAngles is not None:
            shotTurretYaw, shotGunPitch = self._VehicleGunRotator__fixedShotAngles
        else:
            shotTurretYaw, shotGunPitch = getShotAngles(descr, vehicleMatrix, targetPoint,
                                                        overrideGunPosition=self._VehicleGunRotator__gunPosition)
        estimatedTurretYaw = self.getNextTurretYaw(prevTurretYaw, shotTurretYaw, maxTurretRotationSpeed * timeDiff,
                                                   turretYawLimits)
        if not replayCtrl.isPlaying:
            self._VehicleGunRotator__turretYaw = turretYaw = self._VehicleGunRotator__syncWithServerTurretYaw(estimatedTurretYaw)
        else:
            self._VehicleGunRotator__turretYaw = turretYaw = estimatedTurretYaw
        if maxTurretRotationSpeed != 0:
            self.estimatedTurretRotationTime = abs(turretYaw - shotTurretYaw) / maxTurretRotationSpeed
        else:
            self.estimatedTurretRotationTime = 0
        gunPitchLimits = calcPitchLimitsFromDesc(turretYaw, self._VehicleGunRotator__getGunPitchLimits(),
                                                 descr.hull.turretPitches[0], descr.turret.gunJointPitch)
        self._VehicleGunRotator__gunPitch = self.getNextGunPitch(self._VehicleGunRotator__gunPitch, shotGunPitch, timeDiff, gunPitchLimits)
        if replayCtrl.isPlaying and replayCtrl.isUpdateGunOnTimeWarp:
            self._VehicleGunRotator__updateTurretMatrix(turretYaw, 0.0)
            self._VehicleGunRotator__updateGunMatrix(self._VehicleGunRotator__gunPitch, 0.0)
        else:
            self._VehicleGunRotator__updateTurretMatrix(turretYaw, self._VehicleGunRotator__ROTATION_TICK_LENGTH)
            self._VehicleGunRotator__updateGunMatrix(self._VehicleGunRotator__gunPitch, self._VehicleGunRotator__ROTATION_TICK_LENGTH)
        diff = abs(estimatedTurretYaw - prevTurretYaw)
        if diff > pi:
            diff = 2 * pi - diff
        self._VehicleGunRotator__turretRotationSpeed = diff / timeDiff

        if shouldBoostTickRate():
            self._VehicleGunRotator__dispersionAngles = getOwnVehicleShotDispersionAngleForGunRotator(self, self._VehicleGunRotator__turretRotationSpeed)
        else:
            self._VehicleGunRotator__dispersionAngles = avatar.getOwnVehicleShotDispersionAngle(self._VehicleGunRotator__turretRotationSpeed)


@overrideIn(VehicleGunRotator, condition=isClientLesta)
def __rotate(func, self, shotPoint, timeDiff):
    self._VehicleGunRotator__turretRotationSpeed = 0.0
    targetPoint = shotPoint if shotPoint is not None else self._VehicleGunRotator__prevSentShotPoint
    replayCtrl = BattleReplay.g_replayCtrl
    if targetPoint is None or self._VehicleGunRotator__isLocked and not replayCtrl.isUpdateGunOnTimeWarp:
        if shouldBoostTickRate():
            self._VehicleGunRotator__dispersionAngles = getOwnVehicleShotDispersionAngleForGunRotator(self, 0.0)
        else:
            self._VehicleGunRotator__dispersionAngles = self._avatar.getOwnVehicleShotDispersionAngle(0.0)
    else:
        avatar = self._avatar
        descr = avatar.getVehicleDescriptor()
        turretYawLimits = self._VehicleGunRotator__getTurretYawLimits()
        maxTurretRotationSpeed = self._VehicleGunRotator__maxTurretRotationSpeed
        prevTurretYaw = self._VehicleGunRotator__turretYaw
        vehicleMatrix = self.getAvatarOwnVehicleStabilisedMatrix()
        shotTurretYaw, shotGunPitch = getShotAngles(descr, vehicleMatrix, (
            prevTurretYaw, self._VehicleGunRotator__gunPitch), targetPoint, overrideGunPosition=self._VehicleGunRotator__gunPosition)
        estimatedTurretYaw = self.getNextTurretYaw(prevTurretYaw, shotTurretYaw, maxTurretRotationSpeed * timeDiff,
                                                   turretYawLimits)
        self._VehicleGunRotator__turretYaw = turretYaw = self._VehicleGunRotator__syncWithServerTurretYaw(estimatedTurretYaw)
        if maxTurretRotationSpeed != 0:
            self.estimatedTurretRotationTime = abs(turretYaw - shotTurretYaw) / maxTurretRotationSpeed
        else:
            self.estimatedTurretRotationTime = 0
        gunPitchLimits = calcPitchLimitsFromDesc(turretYaw, self._VehicleGunRotator__getGunPitchLimits(), descr.hull.turretPitches[0],
                                                 descr.turret.gunJointPitch)
        self._VehicleGunRotator__gunPitch = self.getNextGunPitch(self._VehicleGunRotator__gunPitch, shotGunPitch, timeDiff, gunPitchLimits)
        if replayCtrl.isPlaying and replayCtrl.isUpdateGunOnTimeWarp:
            self._VehicleGunRotator__updateTurretMatrix(turretYaw, 0.0)
            self._VehicleGunRotator__updateGunMatrix(self._VehicleGunRotator__gunPitch, 0.0)
        else:
            self._VehicleGunRotator__updateTurretMatrix(turretYaw, self._VehicleGunRotator__ROTATION_TICK_LENGTH)
            self._VehicleGunRotator__updateGunMatrix(self._VehicleGunRotator__gunPitch, self._VehicleGunRotator__ROTATION_TICK_LENGTH)
        diff = abs(estimatedTurretYaw - prevTurretYaw)
        if diff > pi:
            diff = 2 * pi - diff
        self._VehicleGunRotator__turretRotationSpeed = diff / timeDiff

        if shouldBoostTickRate():
            self._VehicleGunRotator__dispersionAngles = getOwnVehicleShotDispersionAngleForGunRotator(self, self._VehicleGunRotator__turretRotationSpeed)
        else:
            self._VehicleGunRotator__dispersionAngles = avatar.getOwnVehicleShotDispersionAngle(self._VehicleGunRotator__turretRotationSpeed)


class DispersionState(object):

    def __init__(self, lastTime, lastTurretYaw, dispersionAngles):
        self.lastTime = lastTime
        self.lastTurretYaw = lastTurretYaw
        self.dispersionAngles = dispersionAngles

    def setState(self, lastTime, lastTurretYaw, dispersionAngles):
        self.lastTime = lastTime
        self.lastTurretYaw = lastTurretYaw
        self.dispersionAngles = dispersionAngles


def getOwnVehicleShotDispersionAngleForGunRotator(gunRotator, turretRotationSpeed):
    avatar = BigWorld.player()  # type: PlayerAvatar
    gunRotator = gunRotator  # type: VehicleGunRotator

    turretYaw = gunRotator.turretYaw

    # cache current state if not exists
    dispersionState = getattr(gunRotator, "_mod_dispersion_state", None)  # type: Optional[DispersionState]
    if dispersionState is None:
        dispersionAngles = avatar.getOwnVehicleShotDispersionAngle(turretRotationSpeed)

        gunRotator._mod_dispersion_state = DispersionState(lastTime=BigWorld.time(),
                                                           lastTurretYaw=turretYaw,
                                                           dispersionAngles=dispersionAngles)
        return dispersionAngles

    # return last cached dispersion angles (and overall ignore fast consecutive dispersion state updates)
    timeDiff = BigWorld.time() - dispersionState.lastTime
    if timeDiff < constants.SERVER_TICK_LENGTH:
        return dispersionState.dispersionAngles

    # simulate slower dispersion state update by using cached last turret yaw
    # using similar code that is in gun rotator __rotate method
    turretYawDiff = abs(turretYaw - dispersionState.lastTurretYaw)
    if turretYawDiff > pi:
        turretYawDiff = 2 * pi - turretYawDiff
    newTurretRotationSpeed = turretYawDiff / timeDiff

    dispersionAngles = avatar.getOwnVehicleShotDispersionAngle(newTurretRotationSpeed)
    dispersionState.setState(lastTime=BigWorld.time(),
                             lastTurretYaw=turretYaw,
                             dispersionAngles=dispersionAngles)
    return dispersionAngles


# the best place to handle reticle size interpolation would be in _DefaultGunMarkerController
# however - that class is very commonly either overridden or replaced completely by server-reticle related mods
# so the next good place to do that is at the data provider directly
#
# Lesta specific
# different gun marker data provider class name and method name

if isClientWG():
    from GUI import WGGunMarkerDataProvider

    @overrideIn(WGGunMarkerDataProvider)
    def updateSizes(func, self, currentSize, currentSizeOffset, relaxTime, offsetInertness):
        # second check makes sure, we alter data provider only for client-side data provider - not the server one
        if not shouldBoostTickRate() or relaxTime != VehicleGunRotator._VehicleGunRotator__ROTATION_TICK_LENGTH:
            func(self, currentSize, currentSizeOffset, relaxTime, offsetInertness)
            return

        # we cannot add attributes to data provider, because it is python binding object that doesn't have it enabled :(
        # so we must track method calls somewhere outside
        # and I want it to be cleared somewhere automatically without doing overrides
        # so let's just store it in player object and call it a day
        player = BigWorld.player()

        dataProviderSizeCache = getattr(player, "_mod_dataProviderSizeCache", None)  # type: dict
        if dataProviderSizeCache is None:
            dataProviderSizeCache = {}
            player._mod_dataProviderSizeCache = dataProviderSizeCache

        selfId = id(self)
        lastTime = dataProviderSizeCache.get(selfId, None)
        if lastTime is None:
            dataProviderSizeCache[selfId] = BigWorld.time()
            func(self, currentSize, currentSizeOffset, constants.SERVER_TICK_LENGTH, offsetInertness)
            return

        # ignore fast consecutive size updates in data provider
        timeDiff = BigWorld.time() - lastTime
        if timeDiff < constants.SERVER_TICK_LENGTH:
            return

        dataProviderSizeCache[selfId] = BigWorld.time()
        func(self, currentSize, currentSizeOffset, constants.SERVER_TICK_LENGTH, offsetInertness)


else:
    from GUI import GunMarkerDataProvider

    @overrideIn(GunMarkerDataProvider)
    def updateSize(func, self, currentSize, relaxTime):
        # second check makes sure, we alter data provider only for client-side data provider - not the server one
        if not shouldBoostTickRate() or relaxTime != VehicleGunRotator._VehicleGunRotator__ROTATION_TICK_LENGTH:
            func(self, currentSize, relaxTime)
            return

        # we cannot add attributes to data provider, because it is python binding object that doesn't have it enabled :(
        # so we must track method calls somewhere outside
        # and I want it to be cleared somewhere automatically without doing overrides
        # so let's just store it in player object and call it a day
        player = BigWorld.player()

        dataProviderSizeCache = getattr(player, "_mod_dataProviderSizeCache", None)  # type: dict
        if dataProviderSizeCache is None:
            dataProviderSizeCache = {}
            player._mod_dataProviderSizeCache = dataProviderSizeCache

        selfId = id(self)
        lastTime = dataProviderSizeCache.get(selfId, None)
        if lastTime is None:
            dataProviderSizeCache[selfId] = BigWorld.time()
            func(self, currentSize, constants.SERVER_TICK_LENGTH)
            return

        # ignore fast consecutive size updates in data provider
        timeDiff = BigWorld.time() - lastTime
        if timeDiff < constants.SERVER_TICK_LENGTH:
            return

        dataProviderSizeCache[selfId] = BigWorld.time()
        func(self, currentSize, constants.SERVER_TICK_LENGTH)


class _ShotResultCache(object):
    clientState = aih_global_binding.bindRW(_BINDING_ID.CLIENT_GUN_MARKER_STATE)

    def __init__(self):
        self.lastShotResult = SHOT_RESULT.UNDEFINED  # type: SHOT_RESULT
        self.lastShotResultTime = BigWorld.time()  # type: float


g_shotResultCache = _ShotResultCache()


# last WoT update 2.3.1.0 did something to BigWorld.CollisionComponent such that other mods calling it
# more frequently (more calls *in the same game tick*) for some reason caused fps drops when aiming at tanks
# even through that measurements of calls to it showed
# that those calls didn't take significant time that could explain that
#
# because responsive reticle boost tick rate, this also inherently increases calls to that component
# however - responsive reticle does it over time (one call per tick, but ticks are much more frequent)
# it's not instantaneous like other mods did (where they called it 2-7 times inside the same tick)
#
# responsive reticle didn't suffer from this mysterious fps drop
# but let's just make sure we're calling it around the same amount of time like vanilla game does (once every 100 ms)
#
# this will inherently slightly slow down reticle penetration indicator responsiveness, but it not noticeable

@overrideIn(ShotResultIndicatorPlugin, condition=isClientWG)
def __onGunMarkerStateChanged(func, self, markerType, gunMarkerState, supportMarkersInfo):
    # handle shot result caching only for client reticle
    #
    # I'd like to cache it on getShotResult class method side, but we unfortunately don't have markerType there
    # also because lesta doesn't store gun marker state in some object (like WG did)
    # and I don't want to make 2 different code handling for them
    # then I cannot compare them by reference by the same code
    #
    # so, we have to compare them somewhere upper in call stack where method signature is kinda the same
    # and this is best place to do so
    if markerType != GUN_MARKER_TYPE.CLIENT or not shouldBoostTickRate():
        return func(self, markerType, gunMarkerState, supportMarkersInfo)

    if not self._ShotResultIndicatorPlugin__isEnabled:
        return

    shotResult = wg_getShotResultByCacheLookup(self, gunMarkerState, supportMarkersInfo)
    if self._ShotResultIndicatorPlugin__cache[markerType] == shotResult:
        return
    if self._parentObj.setGunMarkerColor(markerType, self._ShotResultIndicatorPlugin__colors[shotResult]):
        self._ShotResultIndicatorPlugin__cache[markerType] = shotResult


def wg_getShotResultByCacheLookup(self, gunMarkerState, supportMarkersInfo):
    time = BigWorld.time()
    if time - g_shotResultCache.lastShotResultTime < constants.SERVER_TICK_LENGTH:
        return g_shotResultCache.lastShotResult

    g_shotResultCache.lastShotResult = self._getShotResult(gunMarkerState, supportMarkersInfo)
    g_shotResultCache.lastShotResultTime = time

    return g_shotResultCache.lastShotResult


@overrideIn(ShotResultIndicatorPlugin, condition=isClientLesta)
def __updateColor(func, self, markerType, position, collision, direction):
    if markerType != GUN_MARKER_TYPE.CLIENT or not shouldBoostTickRate():
        return func(self, markerType, position, collision, direction)

    result = lesta_getShotResultByCacheLookup(self, position, collision, direction)
    if result in self._ShotResultIndicatorPlugin__colors:
        color = self._ShotResultIndicatorPlugin__colors[result]
        if self._ShotResultIndicatorPlugin__cache[markerType] != result and self._parentObj.setGunMarkerColor(markerType, color):
            self._ShotResultIndicatorPlugin__cache[markerType] = result
    else:
        LOG_WARNING('Color is not found by shot result', result)


def lesta_getShotResultByCacheLookup(self, position, collision, direction):
    time = BigWorld.time()
    if time - g_shotResultCache.lastShotResultTime < constants.SERVER_TICK_LENGTH:
        return g_shotResultCache.lastShotResult

    g_shotResultCache.lastShotResult = self._ShotResultIndicatorPlugin__shotResultResolver.getShotResult(
        position, collision, direction,
        excludeTeam=self._ShotResultIndicatorPlugin__playerTeam,
        piercingMultiplier=self._ShotResultIndicatorPlugin__piercingMultiplier
    )
    g_shotResultCache.lastShotResultTime = time

    return g_shotResultCache.lastShotResult
