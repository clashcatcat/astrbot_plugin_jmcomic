class JmComicError(Exception):
    pass


class JmRequestError(JmComicError):
    pass


class AlbumNotFoundError(JmComicError):
    pass


class PhotoNotFoundError(JmComicError):
    pass


class ArtifactError(JmComicError):
    pass


class InvalidInputError(JmComicError):
    pass
