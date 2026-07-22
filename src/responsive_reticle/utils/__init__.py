from realm import CURRENT_REALM


def isClientLesta():
    return CURRENT_REALM == 'RU'


def isClientWG():
    return not isClientLesta()


# normally we could wrap overridden function and pass it as argument to our hook
# but this would introduce a common wrapper function, to which all hooks converge
# causing cProfile tree printer to aggregate recursive calls into this common wrapper function
# making call tree harder to follow
#
# if we need overridden function, simply declare it as global variable before decorating hook
def overrideIn(cls, condition=lambda: True):

    def _overrideMethod(func):
        if not condition():
            return func

        funcName = func.__name__

        if funcName.startswith("__") and funcName != "__init__":
            funcName = "_" + cls.__name__ + funcName

        setattr(cls, funcName, func)
        return func
    return _overrideMethod
