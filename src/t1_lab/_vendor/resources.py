"""Read existing package-root assets independently of module locations."""

from importlib.resources import files


def resource_file(name):
    return files("t1_lab._vendor").joinpath(name)
