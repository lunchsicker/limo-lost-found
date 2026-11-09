"""
Exceptions raised while running a policy (shared by the interpreter and Robot APIs).

``code`` is the ExecutePolicy result code (docs/interface_contract.md section 5).
A normal "object not found" is NOT an exception: ``is_obj_visible`` / ``search_here``
return False. Exceptions are reserved for failures that must not look like "not found".
"""


class PolicyRuntimeError(Exception):
    """Policy execution stopped. ``code`` matches the ExecutePolicy result codes."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f'[{code}] {message}')


class PolicyCanceled(PolicyRuntimeError):

    def __init__(self, message='canceled'):
        super().__init__('CANCELED', message)


class DetectionError(PolicyRuntimeError):
    """Camera / detector failure (distinct from 'not detected')."""

    def __init__(self, message):
        super().__init__('DETECTION_ERROR', message)


class UnsupportedObjectError(PolicyRuntimeError):
    """The detector does not support this object."""

    def __init__(self, obj_name):
        super().__init__('UNSUPPORTED_OBJECT', f'detector does not support {obj_name!r}')


class UnknownLocationError(PolicyRuntimeError):
    """A location name that is not registered."""

    def __init__(self, loc_name):
        super().__init__('UNKNOWN_LOCATION', f'{loc_name!r} is not registered')
