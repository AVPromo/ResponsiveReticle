###############################################################################################
# Terminology
# - us - microseconds
# - ms - milliseconds
# - (value1; value2) - value varies between value1 and value2
# - VGR tick - VehicleGunRotator tick (this basically handles turret rotation logic)
# - VGR AT tick - VGR Aiming Terrain tick (WG code flow when aiming terrain)
# - VGR AE tick - VGR Aiming Enemy tick (WG code flow when aiming enemy tank; those are heavier due to
#                 tank collision calculations)
###############################################################################################

###############################################################################################
# Performance measurements
#
# Normally VGR tick is called only every 100 ms, but with this mod enabled, it is called *every rendering frame*.
# This is by design - without this, we wouldn't be able to deliver reticle responsiveness.
#
# Let's assume we will be working on a machine that delivers 200 FPS on average.
# This means that an average game tick (rendering one frame) takes around 5 ms.
#
# Normally VGR tick with WG code takes on average around 600 us (VGR AT tick) or 900 us (VGR AE tick) on my machine
# with sometimes VGR AE tick randomly jumping up to (1100 us; 1400 us)
# but on vanilla logic, it is executed only once every 100 ms.
# So, on my machine, only 1 of 20 game rendering frames would be longer those ~750 us - this is VERY efficient.

# However - now with this mod, those ~750 us would be taken from *every rendering frame*.
# So performance will inevitably be lower than without this mod, because we will be triggering
# turret rotation logic 20 times more often on my machine.
# This alone would turn our FPS on our example machine from 200 FPS to around 170 FPS.
#
# Due to that, we should eliminate every possible heavy bottleneck to maximise average FPS for user
# by optimizing VGR tick with some clever compromises.
# We're targeting to strip down reticle parts of code that takes most significant amount of time
# and that aren't necessary to be executed that often for either crucial reticle logic or for reticle responsiveness
# and those, which MUST be skipped for proper vanilla game state.
#
# Measurement aren't 100% precise.
# We are in python 2 code that returns values in floating-point seconds
# instead of integer nanoseconds (still it uses most precise clock available in operating system).
# The measurements WILL NOT show exactly precise real time taken on method execution - but are helpful as a guideline
# where "something heavier" might be located, because the heavier the call, the better precision of measurement.
# Also include measurement overhead - while I used cProfile (which does that in C language, so its fast), there's
# still some smaller overhead (I noticed around (80 us; 100 us) overhead).
# On top of that, we're talking about *microseconds* of measurements - those are tiny numbers
# so expect variations and possible deviations from the mean.
#
# Current results (on my machine, approximations, with cache hits between 100 ms updates that we implemented)
# - VGR AT tick:
#    - vanilla: 450 us (580 us with full profiler)
#    - modded:  210 us (250 us with full profiler)
#    - so on average 53% (57% with profiler) shorter than vanilla
#    - that's over 200% times faster code!
# - VGR AE tick (with armor flashlight enabled):
#    - vanilla: 730 us (905 us with full profiler), randomly jumping to even (1100 us; 1700 us)
#    - modded:  250 us (320 us with full profiler), no random jumps in between main ticks
#    - so on average 65% (65% with full profiler) shorter than vanilla
#    - that's almost 300% faster code!
#
# So this would, on our example 200 FPS machine, reduce average mod overhead
# by going from roughly around 170 FPS to about 190 FPS - this is impressive result.
#
# A cache hit = skipped some heavy calls on 19 out of 20 game ticks, assuming 200 FPS
# because it wasn't necessary to be calculated every rendering frame
# or was necessary to be skipped for proper logic.
#
# All performance notes mentioned in comments (for certain methods) are done with those results above in mind
###############################################################################################

# avoid registering anything related to responsive reticle in replays
# because it cannot work there due to replay saving reticle position every 100 ms no matter what
#
# this also allows us to do some optimizations of calls to it

def init():
    import BattleReplay

    if not BattleReplay.isLoading() and not BattleReplay.isPlaying():
        import responsive_reticle.hooks  # type: ignore
