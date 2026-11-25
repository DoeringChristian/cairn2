class CairnError(Exception):
    code = "error"

    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


class Invalid(CairnError):
    code = "invalid"


class NotFound(CairnError):
    code = "not_found"


class Conflict(CairnError):
    code = "conflict"


class Pending(CairnError):
    code = "pending"


class Full(CairnError):
    code = "full"


class Unauthorized(CairnError):
    code = "unauthorized"


class Corrupt(CairnError):
    code = "corrupt"


CODES = {e.code: e for e in (CairnError, Invalid, NotFound, Conflict, Pending, Full, Unauthorized, Corrupt)}
