__version__ = "0.2.0"
__build__   = 18
__dev__     = False


def version_string() -> str:
    """Returns the display version string. Includes build number during development."""
    if __dev__:
        return f"{__version__}+b{__build__}"
    return __version__
